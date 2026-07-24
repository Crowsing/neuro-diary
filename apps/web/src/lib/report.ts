// Чисті деривації звіту — портовано з docs/prototype/nd-v2.dc.html,
// _vReport, рядки 1648–1719. Тексти дослівно.

import type { DoneEntry, Entry, SymptomDef } from './types';
import { fmtShort, isoOff, p2 } from './dates';
import type { ChartDeps } from './chart';
import { entryState } from './entry';
import { uniqueIds } from './groups';

/** ISO-дати періоду в порядку спадання: сьогодні, вчора, … (period днів) (рядок 1656). */
export function daysDesc(period: number, now: Date): string[] {
  const out: string[] = [];
  for (let o = 0; o > -period; o--) out.push(isoOff(now, o));
  return out;
}

/** «Останні N днів: DD.MM — DD.MM» (perLbl, рядок 1652). */
export function periodLabel(period: number, now: Date): string {
  return 'Останні ' + period + ' днів: ' + fmtShort(isoOff(now, -(period - 1))) + ' — ' + fmtShort(isoOff(now, 0));
}

/** «Сформовано D.MM.YYYY» (pdfGen, рядок 1703). */
export function generatedLabel(now: Date): string {
  return 'Сформовано ' + now.getDate() + '.' + p2(now.getMonth() + 1) + '.' + now.getFullYear();
}

/** Рядок таблиці записів: дата, симптом, інтенсивність, локалізація, коментарі. */
export interface ReportRow {
  iso: string;
  sid: string;
  d: string;
  s: string;
  i: string;
  l: string;
  c: string;
}

/**
 * Таблиця записів звіту: по днях від сьогодні вглиб, лише present-значення
 * вибраних симптомів. Preview і print отримують той самий повний масив.
 */
export function buildRows(
  period: number,
  syms: string[],
  entries: Record<string, Entry>,
  symDef: (id: string) => SymptomDef | undefined,
  now: Date
): ReportRow[] {
  const rows: ReportRow[] = [];
  const selected = uniqueIds(syms);
  daysDesc(period, now).forEach((iso) => {
    const e = entries[iso]; if (!e || e.status !== 'done') return;
    selected.forEach((sid) => {
      if (!Object.prototype.hasOwnProperty.call(e.sym || {}, sid)) return;
      const s = symDef(sid); if (!s) return;
      const v = e.sym[sid];
      rows.push({
        iso,
        sid,
        d: fmtShort(iso), s: s.name,
        i: s.type === 'scale' ? (v.int != null ? v.int + '/5' : 'не заповнено') : 'був',
        l: [v.side, (v.extra || []).join(', ')].filter(Boolean).join('; ') || '—',
        c: [v.ep ? v.ep + ' еп.' : '', v.impact || '', v.comment || ''].filter(Boolean).join('; ') || '—'
      });
    });
  });
  return rows;
}

export interface ReportSummary {
  id: string;
  t: string;
  present: number;
  absent: number;
  unknown: number;
  known: number;
}

/** Per-symptom summary. Only present + explicit absent form the denominator. */
export function buildSums(period: number, syms: string[], deps: ChartDeps): ReportSummary[] {
  const out: ReportSummary[] = [];
  const completed = daysDesc(period, deps.now)
    .map((iso) => deps.entries[iso])
    .filter((entry): entry is DoneEntry => entry?.status === 'done');
  uniqueIds(syms).forEach((sid) => {
    const s = deps.symDef(sid); if (!s) return;
    const presentEntries = completed.filter((entry) => entryState(entry, sid) === 'present');
    const present = presentEntries.length;
    const absent = completed.filter((entry) => entryState(entry, sid) === 'absent').length;
    const unknown = completed.length - present - absent;
    const known = present + absent;
    const values = s.type === 'scale'
      ? presentEntries.map((entry) => entry.sym[sid]?.int).filter((value): value is number => typeof value === 'number')
      : [];
    const intensity = values.length
      ? ' · середня інтенсивність у дні зі значенням ' +
        (values.reduce((sum, value) => sum + value, 0) / values.length).toFixed(1) +
        '/5 · максимум ' + Math.max(...values) + '/5'
      : (s.type === 'scale' && present > 0 ? ' · інтенсивність не заповнено' : '');
    out.push({
      id: sid,
      present,
      absent,
      unknown,
      known,
      t: s.name + ': було ' + present + ' із ' + known +
        ' відомих спостережень · підтверджено не було ' + absent +
        ' · не заповнено у завершені дні ' + unknown + intensity
    });
  });
  return out;
}

export interface TopRow { best: number; d: string; t: string; }
export interface TopResult { rows: TopRow[]; hasIntensityData: boolean; }
export const TOP_NO_DATA_ROW = { d: '—', t: 'недостатньо заповнених значень інтенсивності' };
export const TOP_NO_HIGH_ROW = { d: '—', t: 'значень інтенсивності 3–5 не було' };

/**
 * Топ «найважчих» днів: лише шкальні симптоми із числовим значенням ≥3;
 * бінарні симптоми не перетворюються на фальшиву інтенсивність;
 * сортування за спаданням best, максимум 3 дні.
 */
export function buildTop(
  period: number,
  syms: string[],
  entries: Record<string, Entry>,
  symDef: (id: string) => SymptomDef | undefined,
  now: Date
): TopResult {
  const out: TopRow[] = [];
  let hasIntensityData = false;
  const selected = uniqueIds(syms);
  daysDesc(period, now).forEach((iso) => {
    const e = entries[iso]; if (!e || e.status !== 'done') return;
    let best = 0; const parts: string[] = [];
    selected.forEach((sid) => {
      const v = e.sym && e.sym[sid]; const s = symDef(sid); if (!v || !s) return;
      if (s.type !== 'scale' || typeof v.int !== 'number') return;
      hasIntensityData = true;
      const iv = v.int;
      if (iv > best) best = iv;
      if (iv >= 3) parts.push(s.name.toLowerCase() + ' ' + iv + '/5');
    });
    if (best >= 3) out.push({ best, d: fmtShort(iso), t: parts.join(', ') });
  });
  return { rows: out.sort((a, b) => b.best - a.best).slice(0, 3), hasIntensityData };
}

/** Done-записи періоду (filledE, рядок 1680), у порядку спадання дат. */
export function filledEntries(period: number, entries: Record<string, Entry>, now: Date): DoneEntry[] {
  return daysDesc(period, now)
    .map((iso) => entries[iso])
    .filter((e): e is DoneEntry => !!e && e.status === 'done');
}

/**
 * Середнє контекстного поля по заповнених записах (avgOf, рядок 1681):
 * незаповнені значення (null/0) ігноруються; порожньо → «—».
 */
export function avgOf(filledE: DoneEntry[], k: 'stress' | 'sleepQ'): string {
  const a = filledE.map((e) => e.ctx && e.ctx[k]).filter((x): x is number => typeof x === 'number');
  return a.length ? (a.reduce((x, z) => x + z, 0) / a.length).toFixed(1) : '—';
}

function nullableAverage(filledE: DoneEntry[], k: 'sleepH'): string {
  const values = filledE.map((entry) => entry.ctx?.[k]).filter((value): value is number => typeof value === 'number');
  return values.length ? (values.reduce((sum, value) => sum + value, 0) / values.length).toFixed(1) + ' год' : 'не заповнено';
}

function booleanSummary(filledE: DoneEntry[], k: 'activity' | 'heat'): string {
  const yes = filledE.filter((entry) => entry.ctx?.[k] === true).length;
  const no = filledE.filter((entry) => entry.ctx?.[k] === false).length;
  return 'так ' + yes + ', ні ' + no + ', не заповнено ' + (filledE.length - yes - no);
}

/**
 * Контекст за завершені дні: середні рахуються лише за заповненими
 * числовими значеннями; true, false і null відображаються окремо.
 */
export function ctxSummary(filledE: DoneEntry[]): string {
  const stress = avgOf(filledE, 'stress');
  const sleepQ = avgOf(filledE, 'sleepQ');
  return 'Стрес: ' + (stress === '—' ? 'не заповнено' : 'середній ' + stress + '/5') +
    ' · Якість сну: ' + (sleepQ === '—' ? 'не заповнено' : 'середня ' + sleepQ + '/5') +
    ' · Години сну: ' + nullableAverage(filledE, 'sleepH') +
    ' · Фізична активність: ' + booleanSummary(filledE, 'activity') +
    ' · Спека / перегрів: ' + booleanSummary(filledE, 'heat');
}

/** Позначені загострення періоду (flares, рядок 1682): дата + нотатка або фолбек. */
export function flaresInPeriod(period: number, entries: Record<string, Entry>, now: Date): { d: string; t: string; groupIds: string[] }[] {
  return daysDesc(period, now)
    .filter((iso) => { const e = entries[iso]; return !!(e && e.status === 'done' && e.flare); })
    .map((iso) => {
      const e = entries[iso] as DoneEntry;
      return {
        d: fmtShort(iso),
        t: ((e.flare && e.flare.note) || 'позначено користувачкою'),
        groupIds: uniqueIds(e.flare?.groupIds || [])
      };
    });
}

/** Нотатки періоду (notes, рядок 1683): лише done-записи з непорожньою нотаткою. */
export function notesInPeriod(period: number, entries: Record<string, Entry>, now: Date): { d: string; t: string }[] {
  return daysDesc(period, now)
    .filter((iso) => { const e = entries[iso]; return !!(e && e.status === 'done' && e.note); })
    .map((iso) => ({ d: fmtShort(iso), t: (entries[iso] as DoneEntry).note }));
}

/** Початки циклу в періоді (cStarts, рядок 1684): isoOff(-(period-1)) ≤ s ≤ сьогодні. */
export function cycleStartsInPeriod(period: number, cycleStarts: string[], now: Date): string[] {
  const today = isoOff(now, 0);
  return cycleStarts.filter((s) => s >= isoOff(now, -(period - 1)) && s <= today);
}

/** Рядок блоку циклу (pdfCycleT, рядок 1715). cStarts — cycleStartsInPeriod(...). */
export function cycleLine(cStarts: string[]): string {
  return cStarts.length
    ? ('Початок менструації: ' + cStarts.map((s) => fmtShort(s)).join(', ') + '. День циклу рахується від останньої позначеної дати.')
    : 'У цьому періоді позначених початків циклу немає.';
}
