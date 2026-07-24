import type { Entry, SymptomDef } from './types';

export type ObservationState = 'present' | 'absent' | 'unknown';

/** Historical truth comes only from the entry itself, never current settings. */
export function entryState(e: Entry | null | undefined, symId: string): ObservationState {
  if (!e || e.status !== 'done') return 'unknown';
  if (Object.prototype.hasOwnProperty.call(e.sym || {}, symId)) return 'present';
  if ((e.absent || []).includes(symId)) return 'absent';
  return 'unknown';
}

/** Numeric value used by charts: unknown=null, explicit absent=0, present=1..N. */
export function entryVal(
  e: Entry | null | undefined,
  symId: string,
  def?: SymptomDef
): number | null {
  if (!e || e.status !== 'done') return null;
  if (symId === 'stress') return e.ctx?.stress ?? null;
  if (symId === 'sleepQ') return e.ctx?.sleepQ ?? null;
  if (symId === 'wb') return e.wb ?? null;
  const state = entryState(e, symId);
  if (state === 'absent') return 0;
  if (state === 'unknown') return null;
  return def?.type === 'scale' ? (e.sym[symId]?.int ?? null) : 1;
}
