// День циклу — портовано з docs/prototype/nd-v2.dc.html, cycleDay, рядок 1031.

import { diffDays } from './dates';

/**
 * День циклу для дати iso: 1 у день останнього старту, що не пізніший за iso.
 * Немає стартів ≤ iso → null; понад 60 днів від старту → null.
 */
export function cycleDay(iso: string, cycleStarts: string[]): number | null {
  const st = (cycleStarts || []).filter((s) => s <= iso).sort();
  if (!st.length) return null;
  const d = diffDays(st[st.length - 1], iso) + 1;
  return d <= 60 ? d : null;
}
