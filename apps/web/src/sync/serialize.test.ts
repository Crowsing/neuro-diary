import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { emptyCtx } from '../lib/checkin';
import type { AppData, DoneEntry } from '../lib/types';
import { emptyData } from '../state/persist';
import { JOURNAL_TTL_MS } from './journals';
import {
  SINGLETON_PATHS,
  fromRecords,
  journalsAfter,
  snapshotOf,
  toRecords
} from './serialize';
import { EMPTY_JOURNALS, type Journals } from './types';

const T0 = 1_768_435_200_000;
const DEVICE = 'aaaa0000';

function done(note = 'нотатка'): DoneEntry {
  return {
    status: 'done',
    wb: 3,
    sym: { fatigue: { int: 2 } },
    absent: ['nausea'],
    ctx: emptyCtx(),
    note,
    flare: null,
    noSymptoms: false,
    filledLater: false
  };
}

function sample(): AppData {
  return {
    ...emptyData(),
    entries: { '2026-01-15': done(), '2026-01-16': done('друга') },
    cycleStarts: ['2026-01-02', '2026-01-30'],
    active: ['fatigue', 'nausea'],
    archived: ['tremor'],
    custom: [{ id: 'own', name: 'Власний', type: 'bool' }],
    groups: [{ id: 'g1', name: 'Мігрень', archived: false }],
    symptomGroupIds: { fatigue: ['g1'] },
    cycleOn: true,
    lock: true
  };
}

describe('toRecords — the storage units of §6.1', () => {
  it('emits one record per day and four singletons', () => {
    const records = toRecords(sample(), EMPTY_JOURNALS, DEVICE, T0);
    const paths = records.map((item) => item.path).sort();
    expect(paths).toEqual(
      ['entry:2026-01-15', 'entry:2026-01-16', ...SINGLETON_PATHS].sort()
    );
  });

  it('never puts a date in a singleton path', () => {
    const records = toRecords(sample(), EMPTY_JOURNALS, DEVICE, T0);
    for (const path of SINGLETON_PATHS) {
      expect(records.some((item) => item.path === path)).toBe(true);
    }
    expect(records.every((item) => !item.path.startsWith('cycle:'))).toBe(true);
  });

  it('stamps every record with the same client_ts and device id', () => {
    const records = toRecords(sample(), EMPTY_JOURNALS, DEVICE, T0);
    for (const item of records) {
      expect(item.clientTs).toBe(T0);
      expect(item.deviceId).toBe(DEVICE);
    }
  });

  it('carries the whole cycle set in one record', () => {
    const [cycle] = toRecords(sample(), EMPTY_JOURNALS, DEVICE, T0).filter(
      (item) => item.path === 'cycle'
    );
    expect(cycle.body.kind).toBe('cycle');
    if (cycle.body.kind !== 'cycle') throw new Error('unreachable');
    expect(cycle.body.starts.map((s) => s.date)).toEqual([
      '2026-01-02',
      '2026-01-30'
    ]);
  });

  it('prunes journal entries older than T_journal on the way out', () => {
    const journals: Journals = {
      cycle: [
        { id: 'old', removedAt: T0 - JOURNAL_TTL_MS - 1 },
        { id: 'fresh', removedAt: T0 - 1 }
      ],
      catalog: [],
      groups: []
    };
    const [cycle] = toRecords(sample(), journals, DEVICE, T0).filter(
      (item) => item.path === 'cycle'
    );
    if (cycle.body.kind !== 'cycle') throw new Error('unreachable');
    expect(cycle.body.journal.map((entry) => entry.id)).toEqual(['fresh']);
  });
});

describe('fromRecords', () => {
  it('round-trips the sample without losing a field', () => {
    const restored = fromRecords(toRecords(sample(), EMPTY_JOURNALS, DEVICE, T0));
    const expected = sample();
    // `lock` не синхронізується свідомо (§6.1), тож round-trip повертає дефолт.
    expect(restored.data).toEqual({ ...expected, lock: false });
  });

  it('ignores tombstones', () => {
    const records = toRecords(sample(), EMPTY_JOURNALS, DEVICE, T0);
    const restored = fromRecords([
      ...records,
      { path: 'entry:2026-01-15', clientTs: T0, deviceId: DEVICE, deleted: true }
    ]);
    // Надгробки застосовує merge, а не серіалізація: сюди приходить уже
    // злитий набір, і присутній запис означає, що він пережив merge.
    expect(Object.keys(restored.data.entries)).toContain('2026-01-15');
  });

  it('keeps an active symptom the display order forgot', () => {
    const records = toRecords(sample(), EMPTY_JOURNALS, DEVICE, T0);
    const patched = records.map((item) =>
      item.body.kind === 'catalog'
        ? { ...item, body: { ...item.body, order: ['fatigue'] } }
        : item
    );
    expect(fromRecords(patched).data.active).toEqual(['fatigue', 'nausea']);
  });

  it('returns empty data for an empty vault', () => {
    expect(fromRecords([]).data).toEqual(emptyData());
  });
});

describe('round-trip is lossless for arbitrary diaries', () => {
  const isoDate = fc
    .integer({ min: 0, max: 3650 })
    .map((offset) => new Date(Date.UTC(2020, 0, 1 + offset)).toISOString().slice(0, 10));
  const symptomId = fc.stringMatching(/^[a-z]{3,8}$/);

  it('preserves every synchronized field', () => {
    fc.assert(
      fc.property(
        fc.record({
          entries: fc.dictionary(isoDate, fc.constant(done()), { maxKeys: 5 }),
          cycleStarts: fc.uniqueArray(isoDate, { maxLength: 6 }),
          active: fc.uniqueArray(symptomId, { maxLength: 6 }),
          cycleOn: fc.boolean()
        }),
        (parts) => {
          const data: AppData = {
            ...emptyData(),
            entries: parts.entries,
            cycleStarts: [...parts.cycleStarts].sort(),
            active: parts.active,
            cycleOn: parts.cycleOn
          };
          const restored = fromRecords(toRecords(data, EMPTY_JOURNALS, DEVICE, T0));
          expect(restored.data).toEqual(data);
        }
      ),
      { numRuns: 200 }
    );
  });
});

describe('journalsAfter — deletions are detected at the serialization boundary', () => {
  it('records a removed cycle date', () => {
    const before = sample();
    const after = { ...before, cycleStarts: ['2026-01-02'] };
    const journals = journalsAfter(before, after, EMPTY_JOURNALS, T0);
    expect(journals.cycle).toEqual([{ id: '2026-01-30', removedAt: T0 }]);
  });

  it('records a removed group without touching symptom values', () => {
    const before = sample();
    const after = { ...before, groups: [] };
    const journals = journalsAfter(before, after, EMPTY_JOURNALS, T0);
    expect(journals.groups).toEqual([{ id: 'g1', removedAt: T0 }]);
    expect(after.symptomGroupIds).toEqual({ fatigue: ['g1'] });
  });

  it('records a symptom removed from the catalog', () => {
    const before = sample();
    const after = { ...before, archived: [] };
    const journals = journalsAfter(before, after, EMPTY_JOURNALS, T0);
    expect(journals.catalog).toEqual([{ id: 'tremor', removedAt: T0 }]);
  });

  it('does not record a move between active and archived as a removal', () => {
    const before = sample();
    const after = { ...before, active: ['fatigue'], archived: ['tremor', 'nausea'] };
    expect(journalsAfter(before, after, EMPTY_JOURNALS, T0).catalog).toEqual([]);
  });

  it('keeps earlier entries and prunes what aged out', () => {
    const before = sample();
    const after = { ...before, cycleStarts: [] };
    const journals = journalsAfter(
      before,
      after,
      { ...EMPTY_JOURNALS, cycle: [{ id: 'ancient', removedAt: T0 - JOURNAL_TTL_MS - 1 }] },
      T0
    );
    expect(journals.cycle.map((entry) => entry.id).sort()).toEqual([
      '2026-01-02',
      '2026-01-30'
    ]);
  });

  it('records nothing without a previous snapshot', () => {
    expect(journalsAfter(null, sample(), EMPTY_JOURNALS, T0)).toEqual(EMPTY_JOURNALS);
  });
});

describe('snapshotOf — мітка порядку відображення', () => {
  it('не перештампується, коли нічого не змінилося', () => {
    // `sample()` має симптом, що належить групі, тож його id трапляється і в
    // `active`, і в ключах `symptomGroupIds`. Порівняння з попереднім знімком
    // мусить це витримати: інакше `orderAt` дорівнює `now` на кожній
    // серіалізації, обидва синглтони назавжди «змінені», і порядок
    // відображення дістається тому пристрою, який синхронізувався останнім.
    const data = sample();
    const first = snapshotOf(data, null, T0);
    const second = snapshotOf(data, first, T0 + 60_000);
    expect(second.orderAt).toBe(T0);

    const third = snapshotOf(data, second, T0 + 120_000);
    expect(third.orderAt).toBe(T0);
  });

  it('перештампується, коли склад каталогу справді змінився', () => {
    const data = sample();
    const first = snapshotOf(data, null, T0);
    // Саме новий id: `tremor` уже лежить в `archived`, тож дедуплікований набір
    // від його додавання в `active` не змінився б — і це правильно.
    const grown = snapshotOf({ ...data, active: [...data.active, 'dizziness'] }, first, T0 + 60_000);
    expect(grown.orderAt).toBe(T0 + 60_000);
  });
});
