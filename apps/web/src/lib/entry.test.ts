import { describe, expect, it } from 'vitest';
import { entryState, entryVal } from './entry';
import { emptyCtx } from './checkin';
import type { DoneEntry, Entry, SymptomDef } from './types';

const scale: SymptomDef = { id: 'fatigue', name: 'Втома', type: 'scale' };
const bool: SymptomDef = { id: 'numb', name: 'Оніміння', type: 'bool' };

function done(p: Partial<DoneEntry> = {}): DoneEntry {
  return {
    status: 'done', wb: null, sym: {}, absent: [], ctx: emptyCtx(), note: '', flare: null,
    noSymptoms: false, filledLater: false, ...p
  };
}

describe('entryState — explicit present / absent / unknown', () => {
  it('present wins when malformed legacy data also lists the id as absent', () => {
    const entry = done({ sym: { fatigue: { int: 4 } }, absent: ['fatigue'] });
    expect(entryState(entry, 'fatigue')).toBe('present');
    expect(entryVal(entry, 'fatigue', scale)).toBe(4);
  });

  it('reads explicit absence only from the entry', () => {
    const entry = done({ absent: ['fatigue'] });
    expect(entryState(entry, 'fatigue')).toBe('absent');
    expect(entryVal(entry, 'fatigue', scale)).toBe(0);
  });

  it('unanswered completed symptom is unknown', () => {
    expect(entryState(done(), 'fatigue')).toBe('unknown');
    expect(entryVal(done(), 'fatigue', scale)).toBeNull();
  });

  it('missing day and draft are unknown', () => {
    const draft: Entry = {
      status: 'draft',
      d: { wb: null, wbSkip: false, sel: ['fatigue'], sym: { fatigue: { int: 3 } }, absent: [], groupId: null, ctx: emptyCtx(), note: '', flare: null, confirmed: false, noSymptoms: false, step: 3 }
    };
    expect(entryState(undefined, 'fatigue')).toBe('unknown');
    expect(entryState(draft, 'fatigue')).toBe('unknown');
  });

  it('current active/archive/regroup settings are irrelevant by construction', () => {
    const oldEntry = done({ absent: ['fatigue'] });
    expect(entryVal(oldEntry, 'fatigue', scale)).toBe(0);
    expect(entryVal(oldEntry, 'numb', bool)).toBeNull();
  });

  it('new symptom added tomorrow remains unknown in an old entry', () => {
    expect(entryState(done({ absent: ['fatigue'] }), 'tomorrow-symptom')).toBe('unknown');
  });

  it('nullable day-level fields retain null', () => {
    const entry = done({ wb: null, ctx: emptyCtx() });
    expect(entryVal(entry, 'wb')).toBeNull();
    expect(entryVal(entry, 'stress')).toBeNull();
    expect(entryVal(entry, 'sleepQ')).toBeNull();
  });
});
