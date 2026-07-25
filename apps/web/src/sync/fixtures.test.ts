import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { entryState } from '../lib/entry';
import type { DoneEntry } from '../lib/types';
import { migrateState } from '../state/persist';
import { mergeCycle } from './merge';
import type { CycleBody } from './types';

/**
 * Ті самі файли, які читає `apps/api/tests/contract`. Розбіжність у тому, що
 * приймають дві сторони, падає тут, а не в проді.
 */
const ROOT = fileURLToPath(new URL('../../../../fixtures/contract/', import.meta.url));

function load<T>(name: string): T {
  return JSON.parse(readFileSync(`${ROOT}${name}`, 'utf-8')) as T;
}

describe('shared contract fixtures', () => {
  it('accepts the valid done entry as present / absent / unknown', () => {
    const entry = load<DoneEntry>('done-entry.valid.json');
    expect(entryState(entry, 'fatigue')).toBe('present');
    expect(entryState(entry, 'nausea')).toBe('absent');
    expect(entryState(entry, 'tremor')).toBe('unknown');
  });

  it('reads an overlapping entry as present, where api refuses it outright', () => {
    // Асиметрія свідома і задокументована: api відхиляє такий запис, бо
    // погоджуватися на «одночасно є і явно немає» немає сенсу; web його
    // приймає й інтерпретує — present перемагає absent (§4.3), бо щоденник
    // ніколи не має зникнути через один поганий запис.
    const overlapping = load<DoneEntry>('done-entry.sym-absent-overlap.json');
    const state = migrateState(
      {
        data: {
          ...load<Record<string, unknown>>('app-data.v4.min.json'),
          entries: { '2026-01-15': overlapping }
        }
      },
      new Date('2026-01-16T12:00:00Z'),
      true
    );
    const stored = state.data.entries['2026-01-15'] as DoneEntry;
    expect(entryState(stored, 'fatigue')).toBe('present');
  });

  it('reads the minimal app data as an empty diary', () => {
    const state = migrateState(
      { data: load<Record<string, unknown>>('app-data.v4.min.json') },
      new Date('2026-01-16T12:00:00Z'),
      true
    );
    expect(state.data.schemaVersion).toBe(4);
    expect(state.data.entries).toEqual({});
  });

  it('converges the cycle fixture exactly as the file states', () => {
    const fixture = load<{
      deviceA: CycleBody;
      deviceB: CycleBody;
      expected: CycleBody;
    }>('merge/cycle-reconvergence.json');

    const oneWay = mergeCycle(fixture.deviceA, fixture.deviceB);
    const otherWay = mergeCycle(fixture.deviceB, fixture.deviceA);

    expect(oneWay).toEqual(fixture.expected);
    expect(otherWay).toEqual(fixture.expected);
  });
});
