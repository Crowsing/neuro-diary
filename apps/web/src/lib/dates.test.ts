import { describe, it, expect } from 'vitest';
import { p2, isoOff, dOf, diffDays, fmtShort, fmtLong, isIsoDate, MON, MONN, WDL } from './dates';

const now = new Date('2026-07-21T12:00:00');

describe('p2', () => {
  it('доповнює нулем до двох знаків', () => {
    expect(p2(5)).toBe('05');
    expect(p2(12)).toBe('12');
    expect(p2(0)).toBe('00');
  });
});

describe('isoOff', () => {
  it('зсув 0 — сьогодні', () => {
    expect(isoOff(now, 0)).toBe('2026-07-21');
  });

  it('зсув назад у межах місяця', () => {
    expect(isoOff(now, -1)).toBe('2026-07-20');
    expect(isoOff(now, -20)).toBe('2026-07-01');
  });

  it('перетинає межу місяця назад', () => {
    expect(isoOff(now, -21)).toBe('2026-06-30');
    expect(isoOff(now, -30)).toBe('2026-06-21');
  });

  it('перетинає межу місяця вперед', () => {
    expect(isoOff(now, 10)).toBe('2026-07-31');
    expect(isoOff(now, 11)).toBe('2026-08-01');
  });

  it('перетинає межу року назад', () => {
    expect(isoOff(now, -202)).toBe('2025-12-31');
    expect(isoOff(now, -203)).toBe('2025-12-30');
  });

  it('перетинає межу року вперед', () => {
    expect(isoOff(now, 163)).toBe('2026-12-31');
    expect(isoOff(now, 164)).toBe('2027-01-01');
  });

  it('не мутує now', () => {
    const copy = new Date(now);
    isoOff(now, -100);
    expect(now.getTime()).toBe(copy.getTime());
  });
});

describe('dOf', () => {
  it('повертає локальний полудень заданої дати', () => {
    const d = dOf('2026-07-21');
    expect(d.getFullYear()).toBe(2026);
    expect(d.getMonth()).toBe(6);
    expect(d.getDate()).toBe(21);
    expect(d.getHours()).toBe(12);
  });
});

describe('isIsoDate', () => {
  it('приймає лише реальні календарні дати YYYY-MM-DD', () => {
    expect(isIsoDate('2026-02-28')).toBe(true);
    expect(isIsoDate('2026-02-30')).toBe(false);
    expect(isIsoDate('2026-2-03')).toBe(false);
    expect(isIsoDate('not-a-date')).toBe(false);
  });
});

describe('diffDays', () => {
  it('рахує дні вперед і назад', () => {
    expect(diffDays('2026-07-01', '2026-07-21')).toBe(20);
    expect(diffDays('2026-07-21', '2026-07-01')).toBe(-20);
    expect(diffDays('2026-07-21', '2026-07-21')).toBe(0);
  });

  it('через межу року', () => {
    expect(diffDays('2025-12-31', '2026-01-01')).toBe(1);
    expect(diffDays('2025-12-01', '2026-01-31')).toBe(61);
  });

  it('через переведення годинника (весна 2026)', () => {
    expect(diffDays('2026-03-28', '2026-03-30')).toBe(2);
  });
});

describe('fmtShort', () => {
  it('день без нуля, місяць із нулем', () => {
    expect(fmtShort('2026-07-05')).toBe('5.07');
    expect(fmtShort('2026-11-30')).toBe('30.11');
  });
});

describe('fmtLong', () => {
  it('день тижня + число + місяць у родовому', () => {
    expect(fmtLong('2026-07-21')).toBe('вівторок, 21 липня');
    expect(fmtLong('2026-01-01')).toBe('четвер, 1 січня');
    expect(fmtLong('2026-07-19')).toBe('неділя, 19 липня');
  });
});

describe('константи', () => {
  it('MON/MONN/WDL мають правильну довжину і зразкові значення', () => {
    expect(MON).toHaveLength(12);
    expect(MONN).toHaveLength(12);
    expect(WDL).toHaveLength(7);
    expect(MON[6]).toBe('липня');
    expect(MONN[6]).toBe('Липень');
    expect(WDL[4]).toBe('п’ятниця');
  });
});
