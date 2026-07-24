import { describe, expect, it } from 'vitest';
import {
  buildSeq,
  draftFromDone,
  draftMatchesDone,
  emptyCtx,
  finalizeEntry,
  initDraft,
  missingIntensityIds,
  noSymptomsEntry,
  resolveDraft,
  seqIndex
} from './checkin';
import type { DoneEntry, SymptomDef } from './types';

const TODAY = '2026-07-22';
const symDef = (id: string): SymptomDef | undefined => ({
  fatigue: { id: 'fatigue', name: 'Втома', type: 'scale' },
  numb: { id: 'numb', name: 'Оніміння', type: 'bool' }
} as Record<string, SymptomDef>)[id];

function done(p: Partial<DoneEntry> = {}): DoneEntry {
  return { status: 'done', wb: 7, sym: {}, absent: [], ctx: emptyCtx(), note: '', flare: null, noSymptoms: false, filledLater: false, ...p };
}

describe('check-in draft', () => {
  it('starts nullable context and explicit absence empty', () => {
    expect(initDraft()).toMatchObject({
      sel: [], absent: [], groupId: null,
      ctx: { sleepH: null, activity: null, heat: null }
    });
  });

  it('builds one detail step per selected symptom', () => {
    expect(buildSeq(['fatigue', 'numb'])).toEqual([{ s: 1 }, { s: 2 }, { s: 3, i: 0 }, { s: 3, i: 1 }, { s: 6 }]);
    expect(seqIndex(buildSeq(['fatigue']), 3, 0)).toBe(2);
  });

  it('restores done values and absence without mutating the entry', () => {
    const entry = done({ sym: { fatigue: { int: 3, extra: ['Фізична'] } }, absent: ['numb'] });
    const draft = draftFromDone(entry);
    draft.sym.fatigue.int = 5;
    draft.absent.push('other');
    expect(entry.sym.fatigue.int).toBe(3);
    expect(entry.absent).toEqual(['numb']);
    expect(draft.groupId).toBeNull();
  });

  it('resolveDraft prefers an open same-day draft, then persisted draft, then done', () => {
    const open = initDraft(); open.wb = 2;
    const persisted = initDraft(); persisted.wb = 4;
    expect(resolveDraft(TODAY, { date: TODAY, d: open, back: null }, { status: 'draft', d: persisted })).toBe(open);
    expect(resolveDraft(TODAY, null, { status: 'draft', d: persisted })).toBe(persisted);
    expect(resolveDraft(TODAY, null, done()).step).toBe(6);
  });
});

describe('finalizeEntry', () => {
  it('stores each present symptom once and present always removes it from absent', () => {
    const draft = initDraft();
    draft.sel = ['fatigue', 'fatigue'];
    draft.sym = { fatigue: { int: 3, more: true } };
    draft.absent = ['fatigue', 'numb', 'numb'];
    const entry = finalizeEntry(draft, TODAY, undefined, TODAY);
    expect(entry.sym).toEqual({ fatigue: { int: 3 } });
    expect(entry.absent).toEqual(['numb']);
  });

  it('does not infer absence from legacy confirmation', () => {
    const draft = initDraft(); draft.confirmed = true;
    expect(finalizeEntry(draft, TODAY, undefined, TODAY).absent).toEqual([]);
  });

  it('keeps past filledLater and nullable context', () => {
    const entry = finalizeEntry(initDraft(), '2026-07-20', undefined, TODAY);
    expect(entry.filledLater).toBe(true);
    expect(entry.ctx).toEqual(emptyCtx());
  });

  it('quick global absence snapshots exactly the supplied unique active ids', () => {
    const entry = noSymptomsEntry(['fatigue', 'numb', 'fatigue']);
    expect(entry.absent).toEqual(['fatigue', 'numb']);
    expect(entry.noSymptoms).toBe(true);
  });
});

describe('validation and completed-entry comparison', () => {
  it('requires intensity only for selected scale symptoms', () => {
    const draft = initDraft(); draft.sel = ['fatigue', 'numb']; draft.sym = { numb: {} };
    expect(missingIntensityIds(draft, symDef)).toEqual(['fatigue']);
    draft.sym.fatigue = { int: 2 };
    expect(missingIntensityIds(draft, symDef)).toEqual([]);
  });

  it('detects unsaved edits without treating draft-only UI flags as data', () => {
    const entry = done({ sym: { fatigue: { int: 3 } }, absent: ['numb'] });
    const draft = draftFromDone(entry);
    expect(draftMatchesDone(draft, entry)).toBe(true);
    draft.wb = 2;
    expect(draftMatchesDone(draft, entry)).toBe(false);
  });

  it('preserves a migrated day-level no-symptoms statement until exact symptom data replaces it', () => {
    const legacy = done({ noSymptoms: true, legacyNoSymptoms: true });
    const draft = draftFromDone(legacy);
    expect(draft.legacyNoSymptoms).toBe(true);
    expect(draftMatchesDone(draft, legacy)).toBe(true);
    expect(finalizeEntry(draft, TODAY, legacy, TODAY).legacyNoSymptoms).toBe(true);

    draft.sel = ['fatigue'];
    draft.sym = { fatigue: { int: 3 } };
    expect(finalizeEntry(draft, TODAY, legacy, TODAY).legacyNoSymptoms).toBeUndefined();
  });
});
