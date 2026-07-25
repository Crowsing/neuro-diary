import { describe, expect, it } from 'vitest';
import { emptyCtx } from '../lib/checkin';
import type { DoneEntry, DraftEntry } from '../lib/types';
import { JOURNAL_TTL_MS, pruneJournal } from './journals';
import {
  mergeCatalog,
  mergeCycle,
  mergeEntry,
  mergeGroups,
  mergeRecord,
  mergeSettings
} from './merge';
import type { CatalogBody, CycleBody, GroupsBody, PlainRecord, SyncRecord } from './types';

const T0 = 1_768_435_200_000;
const DEVICE_A = 'aaaa0000';
const DEVICE_B = 'bbbb1111';

function done(note = 'a'): DoneEntry {
  return {
    status: 'done',
    wb: 3,
    sym: {},
    absent: [],
    ctx: emptyCtx(),
    note,
    flare: null,
    noSymptoms: false,
    filledLater: false
  };
}

const draft: DraftEntry = {
  status: 'draft',
  d: {
    wb: null,
    wbSkip: false,
    sel: [],
    sym: {},
    absent: [],
    groupId: null,
    ctx: emptyCtx(),
    note: '',
    flare: null,
    confirmed: false,
    noSymptoms: false,
    step: 1
  }
};

function entryRecord(
  entry: DoneEntry | DraftEntry,
  clientTs: number,
  deviceId = DEVICE_A
): PlainRecord {
  return {
    path: 'entry:2026-01-15',
    clientTs,
    deviceId,
    body: { kind: 'entry', entry }
  };
}

function tombstone(clientTs: number, deviceId = DEVICE_A): SyncRecord {
  return { path: 'entry:2026-01-15', clientTs, deviceId, deleted: true };
}

describe('mergeEntry — per-record LWW with two stronger rules (§9.3)', () => {
  it('takes the newer authenticated client_ts', () => {
    const older = entryRecord(done('older'), T0);
    const newer = entryRecord(done('newer'), T0 + 1);
    expect(mergeEntry(older, newer)).toBe(newer);
    expect(mergeEntry(newer, older)).toBe(newer);
  });

  it('breaks a tie by device id, not by arrival order', () => {
    const a = entryRecord(done('from-a'), T0, DEVICE_A);
    const b = entryRecord(done('from-b'), T0, DEVICE_B);
    expect(mergeEntry(a, b)).toBe(b);
    expect(mergeEntry(b, a)).toBe(b);
  });

  it('never lets a draft overwrite a done entry of the same day', () => {
    const finished = entryRecord(done('finished'), T0);
    const started = entryRecord(draft, T0 + 10_000);
    expect(mergeEntry(finished, started)).toBe(finished);
    expect(mergeEntry(started, finished)).toBe(finished);
  });

  it('keeps the newer draft when neither side is done', () => {
    const older = entryRecord(draft, T0);
    const newer = entryRecord(draft, T0 + 1);
    expect(mergeEntry(older, newer)).toBe(newer);
  });

  it('lets a tombstone win against an equally old update', () => {
    const update = entryRecord(done(), T0);
    const removed = tombstone(T0);
    expect(mergeRecord(update, removed)).toBe(removed);
    expect(mergeRecord(removed, update)).toBe(removed);
  });

  it('lets a newer update win against an older tombstone', () => {
    const removed = tombstone(T0);
    const readded = entryRecord(done('re-added'), T0 + 1);
    expect(mergeRecord(removed, readded)).toBe(readded);
  });

  it('keeps the tombstone when the update is older', () => {
    const removed = tombstone(T0 + 1);
    const stale = entryRecord(done(), T0);
    expect(mergeRecord(stale, removed)).toBe(removed);
  });

  it('returns the only side it was given', () => {
    const one = entryRecord(done(), T0);
    expect(mergeRecord(one, null)).toBe(one);
    expect(mergeRecord(null, one)).toBe(one);
    expect(mergeRecord(null, null)).toBeNull();
  });
});

describe('mergeCycle — element-wise 2P-set (§9.3)', () => {
  const cycle = (
    starts: [string, number][],
    journal: [string, number][] = []
  ): CycleBody => ({
    kind: 'cycle',
    starts: starts.map(([date, addedAt]) => ({ date, addedAt })),
    journal: journal.map(([id, removedAt]) => ({ id, removedAt }))
  });

  it('unions the sets', () => {
    const merged = mergeCycle(
      cycle([['2026-01-01', T0]]),
      cycle([['2026-02-01', T0]])
    );
    expect(merged.starts.map((s) => s.date)).toEqual(['2026-01-01', '2026-02-01']);
  });

  it('applies a removal that is newer than the addition', () => {
    const merged = mergeCycle(
      cycle([['2026-01-01', T0]]),
      cycle([], [['2026-01-01', T0 + 1]])
    );
    expect(merged.starts).toEqual([]);
    expect(merged.journal.map((r) => r.id)).toEqual(['2026-01-01']);
  });

  it('lets a re-addition win over an older removal', () => {
    const merged = mergeCycle(
      cycle([['2026-01-01', T0 + 2]]),
      cycle([], [['2026-01-01', T0 + 1]])
    );
    expect(merged.starts.map((s) => s.date)).toEqual(['2026-01-01']);
  });

  it('resolves an exact tie towards removal', () => {
    const merged = mergeCycle(
      cycle([['2026-01-01', T0]]),
      cycle([], [['2026-01-01', T0]])
    );
    expect(merged.starts).toEqual([]);
  });

  it('is commutative', () => {
    const a = cycle([['2026-01-01', T0 + 2]], [['2026-02-01', T0]]);
    const b = cycle([['2026-02-01', T0 + 1]], [['2026-01-01', T0]]);
    expect(mergeCycle(a, b)).toEqual(mergeCycle(b, a));
  });

  it('is idempotent', () => {
    const a = cycle([['2026-01-01', T0]], [['2026-02-01', T0]]);
    expect(mergeCycle(a, a)).toEqual(mergeCycle(mergeCycle(a, a), a));
  });

  it('converges on delete / offline-add / re-add within two rounds', () => {
    // Пристрій A видаляє дату; пристрій B, ще не знаючи цього, додає її знову
    // пізніше. Після одного обміну обидва бачать той самий стан.
    const afterDelete = cycle([], [['2026-01-01', T0 + 1]]);
    const offlineReadd = cycle([['2026-01-01', T0 + 2]]);

    const roundOneA = mergeCycle(afterDelete, offlineReadd);
    const roundOneB = mergeCycle(offlineReadd, afterDelete);
    expect(roundOneA).toEqual(roundOneB);

    const roundTwoA = mergeCycle(roundOneA, roundOneB);
    expect(roundTwoA).toEqual(roundOneA);
    expect(roundTwoA.starts.map((s) => s.date)).toEqual(['2026-01-01']);
  });
});

describe('mergeCatalog', () => {
  const catalog = (over: Partial<CatalogBody> = {}): CatalogBody => ({
    kind: 'catalog',
    places: { fatigue: { place: 'active', at: T0 } },
    custom: {},
    order: ['fatigue'],
    orderAt: T0,
    journal: [],
    ...over
  });

  it('takes the newer place of one symptom', () => {
    const merged = mergeCatalog(
      catalog(),
      catalog({ places: { fatigue: { place: 'archived', at: T0 + 1 } } })
    );
    expect(merged.places.fatigue.place).toBe('archived');
  });

  it('drops a symptom removed after it was placed', () => {
    const merged = mergeCatalog(
      catalog(),
      catalog({ places: {}, journal: [{ id: 'fatigue', removedAt: T0 + 1 }] })
    );
    expect(merged.places).toEqual({});
    expect(merged.order).toEqual([]);
  });

  it('takes the newer display order as a whole', () => {
    const merged = mergeCatalog(
      catalog({ order: ['fatigue', 'nausea'], orderAt: T0 }),
      catalog({
        places: {
          fatigue: { place: 'active', at: T0 },
          nausea: { place: 'active', at: T0 }
        },
        order: ['nausea', 'fatigue'],
        orderAt: T0 + 1
      })
    );
    expect(merged.order).toEqual(['nausea', 'fatigue']);
  });

  it('is commutative', () => {
    const a = catalog({ places: { fatigue: { place: 'archived', at: T0 + 1 } } });
    const b = catalog({ journal: [{ id: 'nausea', removedAt: T0 }] });
    expect(mergeCatalog(a, b)).toEqual(mergeCatalog(b, a));
  });
});

describe('mergeGroups', () => {
  const groups = (over: Partial<GroupsBody> = {}): GroupsBody => ({
    kind: 'groups',
    groups: {
      g1: { group: { id: 'g1', name: 'Мігрень', archived: false }, at: T0 }
    },
    membership: { fatigue: { ids: ['g1'], at: T0 } },
    order: ['g1'],
    orderAt: T0,
    journal: [],
    ...over
  });

  it('drops a group deleted after it was created', () => {
    const merged = mergeGroups(
      groups(),
      groups({ groups: {}, journal: [{ id: 'g1', removedAt: T0 + 1 }] })
    );
    expect(merged.groups).toEqual({});
  });

  it('keeps symptom values when a group disappears', () => {
    const merged = mergeGroups(
      groups(),
      groups({ groups: {}, journal: [{ id: 'g1', removedAt: T0 + 1 }] })
    );
    // Членство лишається записаним; чистка посилань — справа reducer, який і
    // сьогодні зберігає значення симптомів при видаленні групи.
    expect(merged.membership.fatigue.ids).toEqual(['g1']);
  });

  it('takes the newer membership per symptom', () => {
    const merged = mergeGroups(
      groups(),
      groups({ membership: { fatigue: { ids: [], at: T0 + 1 } } })
    );
    expect(merged.membership.fatigue.ids).toEqual([]);
  });
});

describe('mergeSettings — whole-record LWW', () => {
  it('takes the newer record and breaks a tie by device id', () => {
    const a: PlainRecord = {
      path: 'settings',
      clientTs: T0,
      deviceId: DEVICE_A,
      body: { kind: 'settings', cycleOn: true }
    };
    const b: PlainRecord = {
      path: 'settings',
      clientTs: T0,
      deviceId: DEVICE_B,
      body: { kind: 'settings', cycleOn: false }
    };
    expect(mergeSettings(a, b)).toBe(b);
    expect(mergeSettings(b, a)).toBe(b);
  });
});

describe('pruneJournal', () => {
  it('drops entries older than T_journal and keeps the rest', () => {
    const now = T0 + JOURNAL_TTL_MS + 10;
    const pruned = pruneJournal(
      [
        { id: 'old', removedAt: T0 },
        { id: 'fresh', removedAt: now - 1 }
      ],
      now
    );
    expect(pruned.map((entry) => entry.id)).toEqual(['fresh']);
  });

  it('keeps an entry exactly at the boundary', () => {
    const now = T0 + JOURNAL_TTL_MS;
    expect(pruneJournal([{ id: 'edge', removedAt: T0 }], now)).toHaveLength(1);
  });

  it('matches the tombstone ttl of the server', () => {
    // §6.4: T_journal >= T_tomb. Коротший журнал воскрешав би записи, які
    // сервер уже скомпактив.
    expect(JOURNAL_TTL_MS).toBe(187 * 24 * 60 * 60 * 1000);
  });
});
