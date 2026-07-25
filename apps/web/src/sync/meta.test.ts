import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { STORAGE_KEY } from '../state/persist';
import {
  SYNC_META_KEY,
  type StorageLike,
  type SyncMeta,
  clearMeta,
  emptyMeta,
  loadMeta,
  newDeviceId,
  parseMeta,
  saveMeta
} from './meta';

class MemoryStorage implements StorageLike {
  readonly items = new Map<string, string>();
  failOnWrite = false;

  getItem(key: string): string | null {
    return this.items.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    if (this.failOnWrite) throw new DOMException('quota', 'QuotaExceededError');
    this.items.set(key, value);
  }

  removeItem(key: string): void {
    this.items.delete(key);
  }
}

const DEVICE = 'aaaa0000';

describe('sync metadata live in their own key', () => {
  it('never touches the diary key', () => {
    const storage = new MemoryStorage();
    saveMeta(storage, emptyMeta(DEVICE));
    expect([...storage.items.keys()]).toEqual([SYNC_META_KEY]);
    expect(SYNC_META_KEY).not.toBe(STORAGE_KEY);
  });

  it('is invisible to the diary persistence layer', () => {
    // `migrateState` будує стан із білого списку полів, тож службові дані в
    // ньому не з'явилися б навіть помилково. Перевіряється саме це: шар
    // персистенції домену не знає імені службового ключа й не читає його.
    const persistence = readFileSync(
      fileURLToPath(new URL('../state/persist.ts', import.meta.url)),
      'utf-8'
    );
    expect(persistence).not.toContain(SYNC_META_KEY);
    expect(persistence).not.toContain('nd_sync');
  });
});

describe('parseMeta — a tolerant reader', () => {
  it('returns empty metadata for a missing value', () => {
    expect(parseMeta(null, DEVICE)).toEqual(emptyMeta(DEVICE));
  });

  it('returns empty metadata for broken json', () => {
    expect(parseMeta('{not json', DEVICE)).toEqual(emptyMeta(DEVICE));
  });

  it('discards metadata of an unknown version', () => {
    const foreign = JSON.stringify({ ...emptyMeta(DEVICE), version: 99 });
    expect(parseMeta(foreign, DEVICE)).toEqual(emptyMeta(DEVICE));
  });

  it('keeps only well-formed record entries', () => {
    const raw = JSON.stringify({
      ...emptyMeta(DEVICE),
      records: {
        good: { revision: 4, sha256: 'ab', dirty: false },
        bad: { revision: 'four' }
      }
    });
    expect(Object.keys(parseMeta(raw, DEVICE).records)).toEqual(['good']);
  });

  it('round-trips a full value', () => {
    const meta: SyncMeta = {
      ...emptyMeta(DEVICE),
      lastAckedRevision: 12,
      vaultSeq: 3,
      consentEpoch: 2,
      consentsFetchedAtRevision: 12,
      lastSuccessfulSyncAt: 1_768_435_200_000,
      highestSeenRevision: 12,
      records: {
        aa: {
          revision: 12,
          sha256: 'ff',
          dirty: true,
          clientTs: 1_768_435_200_000,
          plain: 'ee'
        }
      },
      snapshot: {
        cycleStarts: { '2026-01-01': 1_768_435_200_000 },
        catalogIds: { fatigue: 1_768_435_200_000 },
        groupIds: {},
        orderAt: 1_768_435_200_000
      }
    };
    expect(parseMeta(JSON.stringify(meta), DEVICE)).toEqual(meta);
  });

  it('keeps the device id it was written with', () => {
    const meta = { ...emptyMeta('deadbeef'), lastAckedRevision: 1 };
    expect(parseMeta(JSON.stringify(meta), DEVICE).deviceId).toBe('deadbeef');
  });
});

describe('storage failures never break the diary', () => {
  it('reports a failed write instead of throwing', () => {
    const storage = new MemoryStorage();
    storage.failOnWrite = true;
    expect(saveMeta(storage, emptyMeta(DEVICE))).toBe(false);
  });

  it('starts over after a clear', () => {
    const storage = new MemoryStorage();
    saveMeta(storage, { ...emptyMeta(DEVICE), lastAckedRevision: 7 });
    clearMeta(storage);
    expect(loadMeta(storage, DEVICE)).toEqual(emptyMeta(DEVICE));
  });
});

describe('newDeviceId', () => {
  it('is 128 random bits, hex encoded', () => {
    const seen = new Set<string>();
    for (let i = 0; i < 64; i += 1) {
      const id = newDeviceId();
      expect(id).toMatch(/^[0-9a-f]{32}$/);
      seen.add(id);
    }
    expect(seen.size).toBe(64);
  });
});
