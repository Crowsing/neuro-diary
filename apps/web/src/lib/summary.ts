// Рядки-резюме — портовано з docs/prototype/nd-v2.dc.html:
// sum (рядки 1032–1053), det (1311–1321), ctxLine (1570–1577).
// Формат, розділювачі й закінчення — дослівно з прототипу.

import type { Ctx, Entry, SymptomDef, SymValue } from './types';
import { INT } from '../constants/symptoms';
import { cycleDay } from './cycle';

/**
 * Однорядкове резюме done-запису (sum, рядки 1032–1053).
 * Порядок частин: самопочуття → симптоми (у порядку ключів e.sym) → «симптомів
 * не було» → стрес → сон → спека → день циклу. Розділювач «; », наприкінці «.».
 * Для відсутнього/draft-запису — порожній рядок.
 *
 * @param symDef      makeSymDef(data.custom)
 * @param cycleOn     data.cycleOn
 * @param cycleStarts data.cycleStarts
 */
export function sum(
  e: Entry | null | undefined,
  iso: string,
  symDef: (id: string) => SymptomDef | undefined,
  cycleOn: boolean,
  cycleStarts: string[]
): string {
  if (!e || e.status !== 'done') return '';
  const parts: string[] = [];
  if (e.wb) parts.push('самопочуття ' + e.wb + '/10');
  Object.keys(e.sym || {}).forEach((id) => {
    const s = symDef(id); if (!s) return; const v = e.sym[id];
    let t = s.name.charAt(0).toLowerCase() + s.name.slice(1);
    if (v.int && s.type === 'scale') t += ' ' + v.int + '/5';
    if (v.side) t += ', ' + v.side.toLowerCase();
    if (v.extra && v.extra.length) t += ' (' + v.extra.join(', ').toLowerCase() + ')';
    if (v.ep) t += ', епізодів: ' + v.ep;
    parts.push(t);
  });
  if (e.noSymptoms && e.absent.length) parts.push('не було жодного з відстежуваних симптомів');
  else if (e.legacyNoSymptoms) parts.push('стара загальна позначка «симптомів не було» без точного переліку');
  else if (e.absent.length) parts.push('підтверджено відсутність: ' + e.absent.length);
  // Прототип (рядок 1046): const c=e.ctx||{} — толерантність до legacy-запису без ctx.
  const c: Partial<Ctx> = e.ctx || {};
  if (c.stress) parts.push('стрес ' + c.stress + '/5');
  if (c.sleepQ) parts.push('сон ' + c.sleepQ + '/5' + (c.sleepH ? ' (' + c.sleepH + ' год)' : ''));
  else if (c.sleepH != null) parts.push('сон ' + c.sleepH + ' год');
  if (c.heat) parts.push('було дуже спекотно');
  const cd = cycleOn ? cycleDay(iso, cycleStarts) : null;
  if (cd) parts.push('цикл — день ' + cd);
  return parts.join('; ') + '.';
}

/**
 * Детальний рядок значення симптому (det, рядки 1311–1321): для екранів огляду
 * чек-іна та дня. Порожнє значення → «—».
 */
export function det(sv: SymValue, s: SymptomDef): string {
  const p: string[] = [];
  if (s.type === 'scale' && sv.int) p.push(sv.int + '/5 — ' + INT[sv.int]);
  if (s.type === 'bool') p.push('було');
  if (sv.side) p.push(sv.side.toLowerCase());
  if (sv.extra && sv.extra.length) p.push(sv.extra.join(', ').toLowerCase());
  if (sv.ep) p.push('епізодів: ' + sv.ep);
  if (sv.impact) p.push('вплив: ' + sv.impact.toLowerCase());
  if (sv.comment) p.push('«' + sv.comment + '»');
  return p.join('; ') || '—';
}

/**
 * Короткий рядок контексту дня (ctxLine, рядки 1570–1577): «стрес X/5 · сон
 * Y/5 (H год) · спека». Для відсутнього/draft-запису — порожній рядок
 * (у прототипі draft не має верхньорівневого ctx).
 */
export function ctxLine(e: Entry | null | undefined): string {
  if (!e || e.status !== 'done' || !e.ctx) return '';
  const c = e.ctx, p: string[] = [];
  if (c.stress) p.push('стрес ' + c.stress + '/5');
  if (c.sleepQ) p.push('сон ' + c.sleepQ + '/5' + (c.sleepH ? ' (' + c.sleepH + ' год)' : ''));
  if (c.heat) p.push('спека');
  return p.join(' · ');
}
