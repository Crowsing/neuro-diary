// Чиста логіка чек-іна — портовано з docs/prototype/nd-v2.dc.html:
// startCheckin (рядки 1164–1176), seq/idx у _vCheckin (1326–1330),
// fin (1187–1193), todNoSym у vToday (1154).

import type { CheckinDraft, CheckinState, Ctx, DoneEntry, Entry, SymValue, SymptomDef } from './types';
import { uniqueIds } from './groups';

export function emptyCtx(): Ctx {
  return { stress: null, sleepQ: null, sleepH: null, activity: null, actType: '', heat: null, extras: [] };
}

/** Порожня чернетка нового чек-іна (startCheckin, рядок 1174). */
export function initDraft(): CheckinDraft {
  return {
    wb: null, wbSkip: false, sel: [], sym: {},
    absent: [], groupId: null, ctx: emptyCtx(),
    note: '', flare: null, confirmed: false, noSymptoms: false, step: 1, symIdx: 0, ctxMore: false
  };
}

/**
 * Чернетка для редагування done-запису (startCheckin, рядки 1169–1173).
 * sym глибоко клонується (JSON); значенням із деталями (side/extra/ep/impact/comment)
 * відновлюється прапорець more. Відкривається одразу на кроці 6 (огляд);
 * wbSkip = !e.wb; ctxMore — якщо була активність/спека/додаткові позначки.
 */
export function draftFromDone(e: DoneEntry): CheckinDraft {
  const sym: Record<string, SymValue> = JSON.parse(JSON.stringify(e.sym || {}));
  Object.keys(sym).forEach((id) => {
    const v = sym[id];
    if (v.side || (v.extra && v.extra.length) || v.ep || v.impact || v.comment) v.more = true;
  });
  return {
    wb: e.wb, wbSkip: !e.wb, sel: Object.keys(sym), sym,
    // У прототипі {extras:[], ...e.ctx} — толерантність до legacy-записів без ctx/extras.
    absent: [...(e.absent || [])], groupId: null,
    ctx: { ...emptyCtx(), ...(e.ctx || {}), extras: e.ctx?.extras || [] },
    note: e.note || '', flare: e.flare, confirmed: false, noSymptoms: e.noSymptoms,
    legacyNoSymptoms: e.legacyNoSymptoms,
    step: 6, symIdx: 0,
    ctxMore: !!(e.ctx && (e.ctx.activity || e.ctx.heat || (e.ctx.extras && e.ctx.extras.length)))
  };
}

/**
 * Вибір чернетки при відкритті чек-іна (startCheckin, рядки 1164–1174), за пріоритетом:
 * незакритий чек-ін тієї самої дати → draft-запис → done-запис (draftFromDone) → нова.
 */
export function resolveDraft(iso: string, checkin: CheckinState | null, e: Entry | undefined): CheckinDraft {
  if (checkin && checkin.date === iso) return checkin.d;
  if (e && e.status === 'draft' && e.d) return e.d;
  if (e && e.status === 'done') return draftFromDone(e);
  return initDraft();
}

/** Крок динамічної послідовності: s — номер кроку, i — індекс симптому (лише для s=3). */
export interface SeqStep { s: number; i?: number; }

/**
 * Динамічна послідовність кроків чек-іна (_vCheckin, рядок 1326):
 * без вибраних симптомів [1, 2, 6]; з k симптомами [1, 2, 3×k, 6].
 * Кроки 4/5 — опційні «сателіти», у послідовність не входять.
 */
export function buildSeq(sel: string[]): SeqStep[] {
  return sel.length
    ? [{ s: 1 }, { s: 2 }, ...sel.map((_id, i) => ({ s: 3, i })), { s: 6 }]
    : [{ s: 1 }, { s: 2 }, { s: 6 }];
}

/**
 * Індекс поточного кроку в seq (_vCheckin, рядки 1328–1330). Для кроку 3 звіряється
 * symIdx (за замовчуванням 0); невідомий крок → 0. Кроки-сателіти 4/5 сюди не
 * передавати — для них екран використовує seq.length-1 (рядок 1329).
 */
export function seqIndex(seq: SeqStep[], step: number, symIdx: number | undefined): number {
  let idx = seq.findIndex((q) => q.s === step && (q.s !== 3 || q.i === (symIdx || 0)));
  if (idx < 0) idx = seq.findIndex((q) => q.s === step);
  if (idx < 0) idx = 0;
  return idx;
}

/**
 * Готовий done-запис із чернетки (fin, рядки 1187–1193).
 * - sym: лише вибрані (sel) симптоми, прапорець more видаляється;
 * - wb: null при wbSkip;
 * - absent: лише явно підтверджені ID, причому present завжди перемагає;
 * - noSymptoms: legacy day-level flag; нова точна семантика зберігається в absent;
 * - filledLater: для вже done-запису зберігається як було, інакше true для не-сьогодні.
 *
 * @param d        чернетка чек-іна
 * @param date     ISO-дата запису (checkin.date)
 * @param prev     попередній запис цієї дати (data.entries[date])
 * @param todayIso ISO сьогодні — isoOff(now, 0)
 */
export function finalizeEntry(d: CheckinDraft, date: string, prev: Entry | undefined, todayIso: string): DoneEntry {
  const sym: Record<string, SymValue> = {};
  const selected = uniqueIds(d.sel);
  selected.forEach((id) => { const v: SymValue = { ...(d.sym[id] || {}) }; delete v.more; sym[id] = v; });
  const absent = uniqueIds(d.absent || []).filter((id) => !Object.prototype.hasOwnProperty.call(sym, id));
  const entry: DoneEntry = {
    status: 'done',
    wb: d.wbSkip ? null : d.wb,
    sym,
    absent,
    ctx: { ...emptyCtx(), ...d.ctx, extras: d.ctx.extras || [] },
    note: d.note || '',
    flare: d.flare || null,
    noSymptoms: selected.length === 0 && d.noSymptoms && (absent.length > 0 || !!d.legacyNoSymptoms),
    filledLater: (prev && prev.status === 'done') ? (prev.filledLater || false) : (date !== todayIso)
  };
  if (d.legacyNoSymptoms && selected.length === 0 && absent.length === 0) entry.legacyNoSymptoms = true;
  return entry;
}

/**
 * Швидкий запис «сьогодні симптомів не було» (vToday todNoSym, рядок 1154):
 * done + snapshot явної відсутності active IDs, без самопочуття й контексту.
 */
export function noSymptomsEntry(activeIds: readonly string[]): DoneEntry {
  return {
    status: 'done', wb: null, sym: {}, absent: uniqueIds(activeIds),
    ctx: emptyCtx(), note: '', flare: null, noSymptoms: activeIds.length > 0, filledLater: false
  };
}

export function normalizeDraft(d: CheckinDraft): CheckinDraft {
  const sel = uniqueIds(d.sel || []);
  const present = new Set(sel);
  return {
    ...d,
    sel,
    absent: uniqueIds(d.absent || []).filter((id) => !present.has(id)),
    ctx: { ...emptyCtx(), ...(d.ctx || {}), extras: d.ctx?.extras || [] }
  };
}

export function missingIntensityIds(
  d: CheckinDraft,
  symDef: (id: string) => SymptomDef | undefined
): string[] {
  return uniqueIds(d.sel).filter((id) => symDef(id)?.type === 'scale' && d.sym[id]?.int == null);
}

export function draftMatchesDone(d: CheckinDraft, entry: DoneEntry): boolean {
  const comparableDraft = finalizeEntry(normalizeDraft(d), '', entry, '');
  const pick = (value: DoneEntry) => ({
    wb: value.wb,
    sym: value.sym,
    absent: uniqueIds(value.absent || []).sort(),
    ctx: { ...emptyCtx(), ...(value.ctx || {}), extras: value.ctx?.extras || [] },
    note: value.note || '',
    flare: value.flare || null,
    noSymptoms: !!value.noSymptoms,
    legacyNoSymptoms: !!value.legacyNoSymptoms
  });
  return JSON.stringify(pick(comparableDraft)) === JSON.stringify(pick(entry));
}
