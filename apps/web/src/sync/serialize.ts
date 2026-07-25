// Серіалізація домену у записи сейфа і назад — §4.5 і §6.1 плану.
//
// Це єдине місце, де домен щоденника перетворюється на одиниці зберігання.
// Reducer при цьому не змінюється: він і далі не знає, що синхронізація існує.
//
// Одиниці рівно за §6.1: `entry:<iso>` на кожен день і п'ять синглтонів.
// Ключі виду `cycle:<date>` заборонені — вони злили б дати в простір, видимий
// серверу, тобто саме те, чого вся схема непрозорих ключів уникає.

import { emptyData } from '../state/persist';
import type { AppData, Entry, SymptomDef, TrackingGroup } from '../lib/types';
import type { DomainIds, DomainSnapshot, StampedIds } from './meta';
import { pruneJournal } from './journals';
import type {
  CatalogBody,
  CycleBody,
  GroupsBody,
  Journals,
  PlainRecord,
  Removal,
  SyncRecord
} from './types';
import { isTombstone } from './types';

export const SINGLETON_PATHS = ['cycle', 'catalog', 'groups', 'settings'] as const;

export function entryPathOf(iso: string): string {
  return `entry:${iso}`;
}

export function isoOfEntryPath(path: string): string | null {
  return path.startsWith('entry:') ? path.slice('entry:'.length) : null;
}

function record<T>(pairs: [string, T][]): Record<string, T> {
  return Object.fromEntries(pairs);
}

/**
 * Мітка появи елемента: збережена, якщо елемент уже був, і `nowMs` лише для
 * справді нового.
 *
 * Без цього кожна серіалізація робила б будь-яке додавання свіжішим за будь-яке
 * видалення, і 2P-set §9.3 перестав би працювати: елемент, видалений на іншому
 * пристрої, воскресав би при першому ж синку. Дефект знайдено незалежним
 * review Фази 2.
 */
function stampOf(known: StampedIds | undefined, id: string, nowMs: number): number {
  const at = known?.[id];
  return at === undefined ? nowMs : at;
}

export function toRecords(
  data: AppData,
  journals: Journals,
  deviceId: string,
  nowMs: number,
  snapshot: DomainSnapshot | null = null
): PlainRecord[] {
  const stamp = { clientTs: nowMs, deviceId };

  const cycle: CycleBody = {
    kind: 'cycle',
    starts: [...data.cycleStarts]
      .sort()
      .map((date) => ({
        date,
        addedAt: stampOf(snapshot?.cycleStarts, date, nowMs)
      })),
    journal: pruneJournal(journals.cycle, nowMs)
  };

  type Placement = { place: 'active' | 'archived'; at: number };
  const catalogStamp = (id: string): number =>
    stampOf(snapshot?.catalogIds, id, nowMs);
  const placements: [string, Placement][] = [
    ...data.active.map((id): [string, Placement] => [
      id,
      { place: 'active', at: catalogStamp(id) }
    ]),
    ...data.archived.map((id): [string, Placement] => [
      id,
      { place: 'archived', at: catalogStamp(id) }
    ])
  ];

  const catalog: CatalogBody = {
    kind: 'catalog',
    places: record(placements),
    custom: record(
      data.custom.map((def) => [def.id, { def, at: catalogStamp(def.id) }])
    ),
    order: [...data.active],
    orderAt: snapshot?.orderAt ?? nowMs,
    journal: pruneJournal(journals.catalog, nowMs)
  };

  const groups: GroupsBody = {
    kind: 'groups',
    groups: record(
      data.groups.map((group) => [
        group.id,
        { group, at: stampOf(snapshot?.groupIds, group.id, nowMs) }
      ])
    ),
    membership: record(
      Object.entries(data.symptomGroupIds).map(([id, ids]) => [
        id,
        { ids, at: stampOf(snapshot?.catalogIds, id, nowMs) }
      ])
    ),
    order: data.groups.map((group) => group.id),
    orderAt: snapshot?.orderAt ?? nowMs,
    journal: pruneJournal(journals.groups, nowMs)
  };

  return [
    ...Object.entries(data.entries).map(([iso, entry]) => ({
      ...stamp,
      path: entryPathOf(iso),
      body: { kind: 'entry' as const, entry }
    })),
    { ...stamp, path: 'cycle', body: cycle },
    { ...stamp, path: 'catalog', body: catalog },
    { ...stamp, path: 'groups', body: groups },
    { ...stamp, path: 'settings', body: { kind: 'settings' as const, cycleOn: data.cycleOn } }
  ];
}

export interface Restored {
  readonly data: AppData;
  readonly journals: Journals;
}

export function fromRecords(records: readonly SyncRecord[]): Restored {
  const entries: Record<string, Entry> = {};
  let cycleStarts: string[] = [];
  let active: string[] = [];
  let archived: string[] = [];
  let custom: SymptomDef[] = [];
  let groups: TrackingGroup[] = [];
  let symptomGroupIds: Record<string, string[]> = {};
  let cycleOn = false;
  let journals: Journals = { cycle: [], catalog: [], groups: [] };

  for (const item of records) {
    if (isTombstone(item)) continue;
    const body = item.body;
    switch (body.kind) {
      case 'entry': {
        const iso = isoOfEntryPath(item.path);
        if (iso !== null) entries[iso] = body.entry;
        break;
      }
      case 'cycle': {
        cycleStarts = body.starts.map((start) => start.date).sort();
        journals = { ...journals, cycle: body.journal };
        break;
      }
      case 'catalog': {
        const placed = Object.entries(body.places);
        const activeIds = placed
          .filter(([, value]) => value.place === 'active')
          .map(([id]) => id);
        // Порядок відображення — з `order`; усе, що не названо, дописується
        // після нього, щоб жоден симптом не зник із каталогу через розбіжність.
        active = [
          ...body.order.filter((id) => activeIds.includes(id)),
          ...activeIds.filter((id) => !body.order.includes(id))
        ];
        archived = placed
          .filter(([, value]) => value.place === 'archived')
          .map(([id]) => id);
        custom = Object.values(body.custom).map((value) => value.def);
        journals = { ...journals, catalog: body.journal };
        break;
      }
      case 'groups': {
        const known = Object.entries(body.groups);
        groups = [
          ...body.order
            .filter((id) => id in body.groups)
            .map((id) => body.groups[id].group),
          ...known
            .filter(([id]) => !body.order.includes(id))
            .map(([, value]) => value.group)
        ];
        symptomGroupIds = record(
          Object.entries(body.membership).map(([id, value]) => [id, [...value.ids]])
        );
        journals = { ...journals, groups: body.journal };
        break;
      }
      case 'settings': {
        cycleOn = body.cycleOn;
        break;
      }
    }
  }

  return {
    data: {
      ...emptyData(),
      entries,
      cycleStarts,
      active,
      archived,
      custom,
      groups,
      symptomGroupIds,
      cycleOn
    },
    journals
  };
}

/**
 * Виявлення видалень на межі серіалізації.
 *
 * Reducer знає лише, що елемента більше немає, тож журнал складається тут —
 * порівнянням поточного стану з попереднім знімком. Без цього пристрій, який
 * був офлайн, додав би елемент назад просто тому, що в нього він ще є.
 */
export function domainIdsOf(data: AppData): DomainIds {
  return {
    cycle: [...data.cycleStarts],
    catalog: [...data.active, ...data.archived, ...data.custom.map((def) => def.id)],
    groups: data.groups.map((group) => group.id)
  };
}

/**
 * Те саме виявлення видалень, але від збережуваних множин ідентифікаторів.
 *
 * Окрема форма потрібна тому, що попередній стан має пережити перезавантаження
 * вкладки: тримати заради цього другу копію всього щоденника було б і зайвим,
 * і гіршим для приватності, ніж три списки ідентифікаторів.
 */
export function journalsAfterIds(
  previous: DomainIds | null,
  current: DomainIds,
  journals: Journals,
  nowMs: number
): Journals {
  if (previous === null) return journals;

  const append = (
    existing: readonly Removal[],
    before: readonly string[],
    after: readonly string[]
  ): readonly Removal[] => {
    const present = new Set(after);
    const gone = before.filter((id) => !present.has(id));
    return pruneJournal(
      [...existing, ...gone.map((id) => ({ id, removedAt: nowMs }))],
      nowMs
    );
  };

  return {
    cycle: append(journals.cycle, previous.cycle, current.cycle),
    catalog: append(journals.catalog, previous.catalog, current.catalog),
    groups: append(journals.groups, previous.groups, current.groups)
  };
}

export function journalsAfter(
  previous: AppData | null,
  current: AppData,
  journals: Journals,
  nowMs: number
): Journals {
  return journalsAfterIds(
    previous === null ? null : domainIdsOf(previous),
    domainIdsOf(current),
    journals,
    nowMs
  );
}

// Канонічний рядок відкритого тіла запису — основа відповіді «чи змінився
// він»: без упорядкованих ключів зміна порядку симптомів у каталозі робила б
// `groups`/`catalog` «зміненими» без жодної зміни змісту, і кожен запуск
// застосунку відправляв би на сервер той самий вміст під новим `client_ts`,
// безпідставно перемагаючи в LWW правки з іншого пристрою.
export { canonicalBody } from './canonical';

/** Ідентифікатори множин домену разом із мітками їхньої появи. */
export function snapshotOf(
  data: AppData,
  previous: DomainSnapshot | null,
  nowMs: number
): DomainSnapshot {
  const stamped = (ids: readonly string[], known: StampedIds | undefined): StampedIds =>
    Object.fromEntries(ids.map((id) => [id, stampOf(known, id, nowMs)]));

  // Дедуплікація обов'язкова, а не косметична: id симптома, який належить
  // групі, трапляється і в `active`, і в ключах `symptomGroupIds`. Попередній
  // знімок — об'єкт, тож дублікати в ньому вже згорнуті, і порівняння списку з
  // дублікатами проти списку без них давало «змінилося» ЗАВЖДИ. Наслідок був
  // не косметичний: `orderAt` дорівнював `now` на кожній серіалізації, тож
  // `catalog` і `groups` вічно вважалися зміненими й порядок відображення
  // дістававався тому пристрою, який синхронізувався останнім.
  const catalogIds = [
    ...new Set([
      ...data.active,
      ...data.archived,
      ...data.custom.map((def) => def.id),
      ...Object.keys(data.symptomGroupIds)
    ])
  ];
  const orderChanged =
    previous === null ||
    JSON.stringify(Object.keys(previous.catalogIds)) !== JSON.stringify(catalogIds);

  return {
    cycleStarts: stamped(data.cycleStarts, previous?.cycleStarts),
    catalogIds: stamped(catalogIds, previous?.catalogIds),
    groupIds: stamped(
      data.groups.map((group) => group.id),
      previous?.groupIds
    ),
    orderAt: orderChanged ? nowMs : (previous?.orderAt ?? nowMs)
  };
}
