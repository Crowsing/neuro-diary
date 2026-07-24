// Головний SVG-графік симптому — порт docs/prototype/nd-v2.dc.html:
// шаблон рядки 609–618; обчислення chGrid/chSegs/chPts/chHeat/chMenses/chFlare/
// — _vTrends, рядки 1596–1597, 1628–1635.
// Сітка, полілінії з розривами на пропущених днях, точки (absent v=0 — коло
// з фоновою заливкою), маркери спеки/менструації/важливої зміни. SVG навмисно
// presentational; повна клавіатурна навігація днями доступна в таблиці.

import type { ChartModel } from '../../../lib/chart';

export interface SymptomChartProps {
  /** model(symId, period, 308, 150, deps). */
  m: ChartModel;
  /** Позначки шкали для горизонтальної сітки (marks, рядок 1596). */
  marks: number[];
}

export default function SymptomChart({ m, marks }: SymptomChartProps) {
  // chGrid (рядок 1628).
  const grid = marks.map((v) => +m.y(v).toFixed(1));
  // chHeat / chMenses / chFlare (рядки 1632–1634).
  const heat = m.days.filter((d) => d.heat).map((d) => ({ iso: d.iso, x: +m.x(d.i).toFixed(1) }));
  const menses = m.days.filter((d) => d.menses).map((d) => ({ iso: d.iso, x: +m.x(d.i).toFixed(1) }));
  const flare = m.days.filter((d) => d.flare).map((d) => {
    const x = m.x(d.i);
    return { iso: d.iso, d: 'M ' + (x - 4.5) + ' 20 L ' + (x + 4.5) + ' 20 L ' + x + ' 11 Z' };
  });
  return (
    <svg viewBox="0 0 308 150" style={{ display: 'block', width: '100%', height: 'auto' }} aria-hidden="true" focusable="false">
      {grid.map((y, gi) => (
        <line key={gi} x1="12" x2="296" y1={y} y2={y} stroke="var(--color-divider)" strokeWidth="1" />
      ))}
      {m.segs.map((s, si) => (
        <path key={si} d={s.d} fill="none" stroke="var(--color-accent)" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
      ))}
      {m.pts.map((p) => (
        <circle key={p.iso} cx={p.x} cy={p.y} r="3" fill={p.fill} stroke="var(--color-accent)" strokeWidth="1.8" />
      ))}
      {heat.map((h) => (
        <circle key={h.iso} cx={h.x} cy="8" r="3" fill="var(--color-accent-400)" />
      ))}
      {menses.map((mk) => (
        <circle key={mk.iso} cx={mk.x} cy="143" r="3.2" fill="none" stroke="var(--color-accent-700)" strokeWidth="1.8" />
      ))}
      {flare.map((f) => (
        <path key={f.iso} d={f.d} fill="var(--color-accent-600)" />
      ))}
    </svg>
  );
}
