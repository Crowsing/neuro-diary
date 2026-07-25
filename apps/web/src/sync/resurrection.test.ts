// Регресія, знайдена незалежним review Фази 2.
//
// Тести merge конструювали тіла записів руками з різними мітками часу — і всі
// проходили. Але жоден із них не йшов через `toRecords`, яка штампувала
// `addedAt = now` на кожній серіалізації. У проді це означало б, що будь-яке
// додавання свіжіше за будь-яке видалення, тобто 2P-set §9.3 не працює зовсім:
// елемент, видалений на іншому пристрої, воскресає при першому ж синку.
//
// Тому цей файл принципово тестує ланцюг цілком: домен → toRecords → merge.

import { describe, expect, it } from 'vitest';
import { emptyData } from '../state/persist';
import type { AppData } from '../lib/types';
import { mergeCatalog, mergeCycle, mergeGroups } from './merge';
import type { DomainSnapshot } from './meta';
import { journalsAfter, snapshotOf, toRecords } from './serialize';
import { EMPTY_JOURNALS, type CatalogBody, type CycleBody, type GroupsBody } from './types';

const T0 = 1_768_435_200_000;
const MINUTE = 60_000;
const DEVICE_A = 'aaaa0000';
const DEVICE_B = 'bbbb1111';

function bodyOf<T>(data: AppData, path: string, at: number, snapshot: DomainSnapshot | null, journals = EMPTY_JOURNALS, device = DEVICE_A): T {
  const record = toRecords(data, journals, device, at, snapshot).find(
    (item) => item.path === path
  );
  if (record === undefined) throw new Error(`no record for ${path}`);
  return record.body as T;
}

describe('a deletion on one device is not undone by the other simply syncing', () => {
  it('keeps a removed cycle date removed', () => {
    // Обидва пристрої почали з тієї самої дати.
    const before: AppData = { ...emptyData(), cycleStarts: ['2026-01-01'] };
    const snapshot = snapshotOf(before, null, T0);

    // A видаляє її і серіалізується пізніше.
    const afterDelete: AppData = { ...emptyData(), cycleStarts: [] };
    const journalsA = journalsAfter(before, afterDelete, EMPTY_JOURNALS, T0 + MINUTE);
    const fromA = bodyOf<CycleBody>(
      afterDelete,
      'cycle',
      T0 + MINUTE,
      snapshotOf(afterDelete, snapshot, T0 + MINUTE),
      journalsA
    );

    // B нічого не робив, лише синхронізувався ще пізніше.
    const fromB = bodyOf<CycleBody>(
      before,
      'cycle',
      T0 + 2 * MINUTE,
      snapshotOf(before, snapshot, T0 + 2 * MINUTE),
      EMPTY_JOURNALS,
      DEVICE_B
    );

    expect(mergeCycle(fromA, fromB).starts).toEqual([]);
    expect(mergeCycle(fromB, fromA).starts).toEqual([]);
  });

  it('keeps a removed symptom out of the catalog', () => {
    const before: AppData = { ...emptyData(), active: ['fatigue', 'nausea'] };
    const snapshot = snapshotOf(before, null, T0);

    const afterDelete: AppData = { ...emptyData(), active: ['fatigue'] };
    const journalsA = journalsAfter(before, afterDelete, EMPTY_JOURNALS, T0 + MINUTE);
    const fromA = bodyOf<CatalogBody>(
      afterDelete,
      'catalog',
      T0 + MINUTE,
      snapshotOf(afterDelete, snapshot, T0 + MINUTE),
      journalsA
    );
    const fromB = bodyOf<CatalogBody>(
      before,
      'catalog',
      T0 + 2 * MINUTE,
      snapshotOf(before, snapshot, T0 + 2 * MINUTE),
      EMPTY_JOURNALS,
      DEVICE_B
    );

    expect(Object.keys(mergeCatalog(fromA, fromB).places)).toEqual(['fatigue']);
  });

  it('keeps a removed group removed', () => {
    const before: AppData = {
      ...emptyData(),
      groups: [{ id: 'g1', name: 'Мігрень', archived: false }]
    };
    const snapshot = snapshotOf(before, null, T0);

    const afterDelete: AppData = { ...emptyData(), groups: [] };
    const journalsA = journalsAfter(before, afterDelete, EMPTY_JOURNALS, T0 + MINUTE);
    const fromA = bodyOf<GroupsBody>(
      afterDelete,
      'groups',
      T0 + MINUTE,
      snapshotOf(afterDelete, snapshot, T0 + MINUTE),
      journalsA
    );
    const fromB = bodyOf<GroupsBody>(
      before,
      'groups',
      T0 + 2 * MINUTE,
      snapshotOf(before, snapshot, T0 + 2 * MINUTE),
      EMPTY_JOURNALS,
      DEVICE_B
    );

    expect(mergeGroups(fromA, fromB).groups).toEqual({});
  });

  it('still lets a genuine re-addition win', () => {
    // Дата справді додана заново вже після видалення — і має пережити merge.
    const removedAt = T0 + MINUTE;
    const deleted: AppData = { ...emptyData(), cycleStarts: [] };
    const journalsA = journalsAfter(
      { ...emptyData(), cycleStarts: ['2026-01-01'] },
      deleted,
      EMPTY_JOURNALS,
      removedAt
    );
    const fromA = bodyOf<CycleBody>(deleted, 'cycle', removedAt, null, journalsA);

    const readded: AppData = { ...emptyData(), cycleStarts: ['2026-01-01'] };
    const fromB = bodyOf<CycleBody>(
      readded,
      'cycle',
      removedAt + MINUTE,
      // Знімка немає: для цього пристрою дата справді нова.
      null,
      EMPTY_JOURNALS,
      DEVICE_B
    );

    expect(mergeCycle(fromA, fromB).starts.map((s) => s.date)).toEqual(['2026-01-01']);
  });

  it('keeps the stamp of an element across repeated serializations', () => {
    const data: AppData = { ...emptyData(), cycleStarts: ['2026-01-01'] };
    const first = snapshotOf(data, null, T0);
    const later = snapshotOf(data, first, T0 + 10 * MINUTE);
    expect(later.cycleStarts['2026-01-01']).toBe(T0);

    const body = bodyOf<CycleBody>(data, 'cycle', T0 + 10 * MINUTE, later);
    expect(body.starts[0].addedAt).toBe(T0);
  });
});
