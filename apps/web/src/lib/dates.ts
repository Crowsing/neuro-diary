// Датні хелпери — портовано з docs/prototype/nd-v2.dc.html, рядки 992–1000.
// Усі функції чисті: «сьогодні» передається явно параметром now.

/** Родовий відмінок місяців (get MON, рядок 992). */
export const MON: string[] = ['січня','лютого','березня','квітня','травня','червня','липня','серпня','вересня','жовтня','листопада','грудня'];

/** Називний відмінок місяців (get MONN, рядок 993). */
export const MONN: string[] = ['Січень','Лютий','Березень','Квітень','Травень','Червень','Липень','Серпень','Вересень','Жовтень','Листопад','Грудень'];

/** Дні тижня, індекс 0 = понеділок (get WDL, рядок 994). */
export const WDL: string[] = ['понеділок','вівторок','середа','четвер','п’ятниця','субота','неділя'];

/** Двозначне доповнення нулем (p2, рядок 995). */
export function p2(n: number): string {
  return String(n).padStart(2, '0');
}

/** ISO-дата (YYYY-MM-DD) зі зсувом off днів від now (isoOff, рядок 996). Не мутує now. */
export function isoOff(now: Date, off: number): string {
  const d = new Date(now);
  d.setDate(d.getDate() + off);
  return d.getFullYear() + '-' + p2(d.getMonth() + 1) + '-' + p2(d.getDate());
}

/** Date опівдні локального часу для ISO-дати (dOf, рядок 997). */
export function dOf(iso: string): Date {
  return new Date(iso + 'T12:00:00');
}

/** Strict calendar-date validation for reducer guards and imported user input. */
export function isIsoDate(iso: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(iso)) return false;
  const d = dOf(iso);
  return !Number.isNaN(d.getTime())
    && d.getFullYear() + '-' + p2(d.getMonth() + 1) + '-' + p2(d.getDate()) === iso;
}

/** Кількість днів від a до b (diffDays, рядок 998); b>a → додатне. */
export function diffDays(a: string, b: string): number {
  return Math.round((dOf(b).getTime() - dOf(a).getTime()) / 864e5);
}

/** «21.07» (fmtShort, рядок 999) — день без нуля, місяць із нулем. */
export function fmtShort(iso: string): string {
  const d = dOf(iso);
  return d.getDate() + '.' + p2(d.getMonth() + 1);
}

/** «вівторок, 21 липня» (fmtLong, рядок 1000). */
export function fmtLong(iso: string): string {
  const d = dOf(iso);
  return WDL[(d.getDay() + 6) % 7] + ', ' + d.getDate() + ' ' + MON[d.getMonth()];
}
