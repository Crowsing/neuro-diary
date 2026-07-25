// Детермінований merge — §9.3 плану.
//
// Сервер не вміє мерджити (він бачить лише шифротекст), тож 409 → pull →
// локальний merge у plaintext → повторний push. Правила мусять давати той
// самий результат на кожному пристрої незалежно від порядку надходження:
// звідси комутативність та ідемпотентність, закріплені тестами.
//
// LWW рахується за `clientTs` ЗСЕРЕДИНИ payload. Серверна колонка
// `client_ts_ms` — лише індексна копія, і merge за нею був би merge за
// значенням, яке сервер може переписати.

import { mergeJournals, removedAt, survives } from './journals';
import {
  type CatalogBody,
  type CycleBody,
  type GroupsBody,
  type PlainRecord,
  type Removal,
  type SyncRecord,
  isTombstone
} from './types';

/** Пізніший виграє; при рівному часі — лексикографічно більший device_id. */
function newer(left: SyncRecord, right: SyncRecord): SyncRecord {
  if (left.clientTs !== right.clientTs) {
    return left.clientTs > right.clientTs ? left : right;
  }
  return left.deviceId >= right.deviceId ? left : right;
}

function isDone(record: SyncRecord): boolean {
  return (
    !isTombstone(record) &&
    record.body.kind === 'entry' &&
    record.body.entry.status === 'done'
  );
}

function isDraft(record: SyncRecord): boolean {
  return (
    !isTombstone(record) &&
    record.body.kind === 'entry' &&
    record.body.entry.status === 'draft'
  );
}

export function mergeEntry(left: SyncRecord, right: SyncRecord): SyncRecord {
  // Заповнений запис ніколи не поступається чернетці тієї самої дати,
  // хоч би якою свіжою була чернетка: чернетка — це незавершений ввід, а не
  // новіша правда про день.
  if (isDone(left) && isDraft(right)) return left;
  if (isDraft(left) && isDone(right)) return right;
  return newer(left, right);
}

export function mergeSettings(left: SyncRecord, right: SyncRecord): SyncRecord {
  return newer(left, right);
}

export function mergeCycle(left: CycleBody, right: CycleBody): CycleBody {
  const journal = mergeJournals(left.journal, right.journal);
  const added = new Map<string, number>();
  for (const start of [...left.starts, ...right.starts]) {
    const known = added.get(start.date);
    if (known === undefined || start.addedAt > known) {
      added.set(start.date, start.addedAt);
    }
  }
  const starts = [...added.entries()]
    .filter(([date, addedAt]) => survives(addedAt, removedAt(journal, date)))
    .map(([date, addedAt]) => ({ date, addedAt }))
    .sort((a, b) => (a.date < b.date ? -1 : 1));
  return { kind: 'cycle', starts, journal };
}

/** Поелементний вибір найсвіжішого значення з двох мап. */
function mergeStamped<T>(
  left: Readonly<Record<string, { at: number } & T>>,
  right: Readonly<Record<string, { at: number } & T>>,
  journal: readonly Removal[]
): Record<string, { at: number } & T> {
  const out: Record<string, { at: number } & T> = {};
  for (const key of new Set([...Object.keys(left), ...Object.keys(right)])) {
    const a = left[key];
    const b = right[key];
    const winner = a === undefined ? b : b === undefined ? a : a.at >= b.at ? a : b;
    if (survives(winner.at, removedAt(journal, key))) out[key] = winner;
  }
  return out;
}

export function mergeCatalog(left: CatalogBody, right: CatalogBody): CatalogBody {
  const journal = mergeJournals(left.journal, right.journal);
  const places = mergeStamped(left.places, right.places, journal);
  const custom = mergeStamped(left.custom, right.custom, journal);
  // Порядок відображення — властивість списку, а не елемента, тож він єдиний
  // тут іде за LWW цілком. Після цього з нього прибираються id, яких уже немає.
  const source = left.orderAt >= right.orderAt ? left : right;
  const order = source.order.filter((id) => id in places);
  return {
    kind: 'catalog',
    places,
    custom,
    order,
    orderAt: Math.max(left.orderAt, right.orderAt),
    journal
  };
}

export function mergeGroups(left: GroupsBody, right: GroupsBody): GroupsBody {
  const journal = mergeJournals(left.journal, right.journal);
  const groups = mergeStamped(left.groups, right.groups, journal);
  // Членство свідомо НЕ чиститься журналом груп: видалення групи не має
  // торкатися значень симптомів, і reducer домену тримає те саме правило.
  const membership = mergeStamped(left.membership, right.membership, []);
  const source = left.orderAt >= right.orderAt ? left : right;
  return {
    kind: 'groups',
    groups,
    membership,
    order: source.order.filter((id) => id in groups),
    orderAt: Math.max(left.orderAt, right.orderAt),
    journal
  };
}

/**
 * Merge двох версій одного запису, включно з надгробками.
 *
 * Надгробок перемагає при рівному часі: інакше пристрій, який щойно видалив
 * запис, і пристрій, який щойно його відредагував, розійшлися б назавжди — а
 * повернення видаленого медичного запису гірше за втрату однієї правки.
 */
export function mergeRecord(
  left: SyncRecord | null,
  right: SyncRecord | null
): SyncRecord | null {
  if (left === null) return right;
  if (right === null) return left;

  if (isTombstone(left) || isTombstone(right)) {
    if (isTombstone(left) && isTombstone(right)) return newer(left, right);
    const grave = isTombstone(left) ? left : right;
    const alive = isTombstone(left) ? right : left;
    return alive.clientTs > grave.clientTs ? alive : grave;
  }

  if (left.body.kind !== right.body.kind) return newer(left, right);

  switch (left.body.kind) {
    case 'entry':
      return mergeEntry(left, right);
    case 'settings':
      return mergeSettings(left, right);
    case 'cycle':
      return {
        ...newer(left, right),
        body: mergeCycle(left.body, right.body as CycleBody)
      } as PlainRecord;
    case 'catalog':
      return {
        ...newer(left, right),
        body: mergeCatalog(left.body, right.body as CatalogBody)
      } as PlainRecord;
    case 'groups':
      return {
        ...newer(left, right),
        body: mergeGroups(left.body, right.body as GroupsBody)
      } as PlainRecord;
  }
}
