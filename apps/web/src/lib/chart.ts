// Модель графіка — портовано з docs/prototype/nd-v2.dc.html, model, рядки 1064–1078.
// Полілінія рветься на null-днях (segs), absent-день (v=0) — окрема точка на нулі,
// пропущений день — жодної точки. Координати заокруглені до 0.1, як у прототипі.

import type { Entry, SymptomDef } from './types';
import { entryState, entryVal } from './entry';
import type { ObservationState } from './entry';
import { isoOff } from './dates';

/** Залежності моделі: явні відповідники this.* із прототипу. */
export interface ChartDeps {
  /** «Сьогодні» — останній день серії. */
  now: Date;
  /** data.entries */
  entries: Record<string, Entry>;
  /** data.cycleStarts */
  cycleStarts: string[];
  /** makeSymDef(data.custom) */
  symDef: (id: string) => SymptomDef | undefined;
}

export interface ChartDay {
  iso: string;
  /** Індекс дня 0..n-1 (0 — найдавніший, n-1 — сьогодні). */
  i: number;
  /** Тристан entryVal: null = unknown, 0 = absent, ≥1 = present. */
  v: number | null;
  /** Observation truth is independent from whether a numeric intensity exists. */
  observation: ObservationState;
  e: Entry | undefined;
  heat: boolean;
  flare: boolean;
  menses: boolean;
}

/** Сегмент полілінії: d — SVG-path «M x y L x y …». */
export interface ChartSeg { d: string; }

export interface ChartPt {
  x: number;
  y: number;
  v: number;
  iso: string;
  i: number;
  /** var(--color-bg) для v=0 (absent), інакше var(--color-accent). */
  fill: string;
}

export interface ChartModel {
  days: ChartDay[];
  segs: ChartSeg[];
  pts: ChartPt[];
  x: (i: number) => number;
  y: (v: number) => number;
  w: number;
  h: number;
  max: number;
  /** Completed days, regardless of whether this symptom was answered. */
  filled: number;
  /** present + explicit absent for this symptom. */
  known: number;
  present: number;
  absent: number;
  unknownCompleted: number;
  n: number;
  /** «Заповнено X із N днів». */
  cover: string;
}

/**
 * Модель графіка симптому за period днів до now включно (model, рядки 1064–1078).
 * symId — id симптому або спец-ключ 'wb' | 'stress' | 'sleepQ'.
 * Шкала: wb → 10, bool-симптом → 1, інакше → 5. Внутрішній відступ pad=12.
 */
export function model(symId: string, period: number, w: number, h: number, deps: ChartDeps): ChartModel {
  const n = period, pad = 12, iw = w - 2 * pad, ih = h - 2 * pad;
  const def = deps.symDef(symId);
  const max = symId === 'wb' ? 10 : (def && def.type === 'bool' ? 1 : 5);
  const days: ChartDay[] = [];
  for (let i = 0; i < n; i++) {
    const o = i - (n - 1); const iso = isoOff(deps.now, o); const e: Entry | undefined = deps.entries[iso];
    const v = entryVal(e, symId, def);
    const observation: ObservationState = symId === 'wb' || symId === 'stress' || symId === 'sleepQ'
      ? (v === null ? 'unknown' : 'present')
      : entryState(e, symId);
    days.push({
      iso, i, v, observation, e,
      heat: !!(e && e.status === 'done' && e.ctx && e.ctx.heat),
      flare: !!(e && e.status === 'done' && e.flare),
      menses: (deps.cycleStarts || []).includes(iso)
    });
  }
  const x = (i: number) => pad + (n > 1 ? i * iw / (n - 1) : iw / 2);
  const y = (v: number) => pad + (1 - v / max) * ih;
  const segs: ChartSeg[] = []; let cur: ChartSeg | null = null;
  days.forEach((d, i) => {
    if (d.v === null) { cur = null; return; }
    const pt = x(i).toFixed(1) + ' ' + y(d.v).toFixed(1);
    if (!cur) { cur = { d: 'M ' + pt + ' L ' + pt }; segs.push(cur); } else cur.d += ' L ' + pt;
  });
  const pts: ChartPt[] = days
    .filter((d): d is ChartDay & { v: number } => d.v !== null)
    .map((d) => ({
      x: +x(d.i).toFixed(1), y: +y(d.v).toFixed(1), v: d.v, iso: d.iso, i: d.i,
      fill: d.v === 0 ? 'var(--color-bg)' : 'var(--color-accent)'
    }));
  const filled = days.filter((d) => d.e && d.e.status === 'done').length;
  const known = days.filter((d) => d.observation !== 'unknown').length;
  const present = days.filter((d) => d.observation === 'present').length;
  const absent = days.filter((d) => d.observation === 'absent').length;
  const unknownCompleted = days.filter((d) => d.e?.status === 'done' && d.observation === 'unknown').length;
  return {
    days, segs, pts, x, y, w, h, max, filled, known, present, absent, unknownCompleted, n,
    cover: 'Заповнено ' + filled + ' із ' + n + ' днів'
  };
}
