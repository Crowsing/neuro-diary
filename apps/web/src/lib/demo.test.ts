import { describe, it, expect } from 'vitest';
import { genDemo } from './demo';
import { isoOff } from './dates';
import { SYM } from '../constants/symptoms';
import type { DoneEntry, DraftEntry } from './types';

const now = new Date('2026-07-21T12:00:00');

describe('genDemo', () => {
  const data = genDemo(now);

  it('детермінований: два виклики дають глибоко рівний результат', () => {
    expect(genDemo(now)).toEqual(genDemo(now));
  });

  it('27 записів: дні -30..-1 без пропусків [-27,-19,-11]', () => {
    const missed = [-27, -19, -11];
    expect(Object.keys(data.entries)).toHaveLength(27);
    for (let o = -30; o <= -1; o++) {
      const iso = isoOff(now, o);
      if (missed.includes(o)) expect(data.entries[iso]).toBeUndefined();
      else expect(data.entries[iso]).toBeDefined();
    }
  });

  it('немає записів поза діапазоном -30..-1', () => {
    const allowed = new Set<string>();
    for (let o = -30; o <= -1; o++) allowed.add(isoOff(now, o));
    for (const iso of Object.keys(data.entries)) expect(allowed.has(iso)).toBe(true);
  });

  it('чернетка на -2: step 3, лише fatigue 3/5 (Фізична)', () => {
    const e = data.entries[isoOff(now, -2)] as DraftEntry;
    expect(e.status).toBe('draft');
    expect(e.d.step).toBe(3);
    expect(e.d.sel).toEqual(['fatigue']);
    expect(e.d.sym.fatigue).toEqual({ int: 3, extra: ['Фізична'] });
    expect(e.d.wb).toBe(6);
    expect(e.d.confirmed).toBe(false);
  });

  it('flare на -8 з точною нотаткою; armWeak 4/5 права', () => {
    const e = data.entries[isoOff(now, -8)] as DoneEntry;
    expect(e.status).toBe('done');
    expect(e.flare).toEqual({
      isNew: true,
      dur24: true,
      temp: false,
      note: 'Слабкість правої руки помітно сильніша за звичну.'
    });
    expect(e.sym.armWeak).toEqual({ int: 4, side: 'Права' });
    expect(e.note).toBe('Дуже спекотно, майже не спала. Права рука слабша, ніж зазвичай.');
  });

  it('flare лише на -8', () => {
    for (const [iso, e] of Object.entries(data.entries)) {
      if (e.status !== 'done') continue;
      if (iso === isoOff(now, -8)) expect(e.flare).not.toBeNull();
      else expect(e.flare).toBeNull();
    }
  });

  it('спека на [-22,-15,-8,-5] із «Задушливе приміщення», інакше без', () => {
    const heat = [-22, -15, -8, -5];
    for (let o = -30; o <= -1; o++) {
      const e = data.entries[isoOff(now, o)];
      if (!e || e.status !== 'done') continue;
      if (heat.includes(o)) {
        expect(e.ctx.heat).toBe(true);
        expect(e.ctx.extras).toEqual(['Задушливе приміщення']);
      } else {
        expect(e.ctx.heat).toBe(false);
        expect(e.ctx.extras).toEqual([]);
      }
    }
  });

  it('нотатки на -12, -20, -4 дослівно', () => {
    expect((data.entries[isoOff(now, -12)] as DoneEntry).note).toBe('Почалися місячні, тягнучий біль унизу живота.');
    expect((data.entries[isoOff(now, -20)] as DoneEntry).note).toBe('Спокійний день, багато гуляла.');
    expect((data.entries[isoOff(now, -4)] as DoneEntry).note).toBe('Стресовий день на роботі, зʼїла обід аж о 16:00.');
  });

  it('cycleStarts = [isoOff(now,-12)]', () => {
    expect(data.cycleStarts).toEqual([isoOff(now, -12)]);
  });

  it('базові налаштування і активний список', () => {
    expect(data.schemaVersion).toBe(4);
    expect(data.active).toEqual(SYM.map((s) => s.id));
    expect(data.active).toHaveLength(13);
    expect(data.archived).toEqual([]);
    expect(data.custom).toEqual([]);
    expect(data.cycleOn).toBe(true);
    expect(data).not.toHaveProperty('remOn');
    expect(data).not.toHaveProperty('remTime');
    expect(data.lock).toBe(false);
  });

  it('усі done-записи мають точний absent snapshot і значення в межах', () => {
    for (const e of Object.values(data.entries)) {
      if (e.status !== 'done') continue;
      expect(new Set([...Object.keys(e.sym), ...e.absent])).toEqual(new Set(SYM.map((s) => s.id)));
      expect(e.absent.some((id) => id in e.sym)).toBe(false);
      expect(e.wb).toBeGreaterThanOrEqual(2);
      expect(e.wb).toBeLessThanOrEqual(9);
      expect(e.ctx.stress).toBeGreaterThanOrEqual(1);
      expect(e.ctx.stress).toBeLessThanOrEqual(5);
      expect(e.ctx.sleepQ).toBeGreaterThanOrEqual(1);
      expect(e.ctx.sleepQ).toBeLessThanOrEqual(5);
      expect(e.ctx.sleepH).toBeGreaterThanOrEqual(4);
      expect(e.ctx.sleepH).toBeLessThanOrEqual(9.5);
      expect(e.noSymptoms).toBe(Object.keys(e.sym).length === 0);
      expect(e.filledLater).toBe(false);
    }
  });

  it('головний біль рівно на [-25,-16,-9,-3]', () => {
    const days = [-25, -16, -9, -3];
    for (let o = -30; o <= -1; o++) {
      const e = data.entries[isoOff(now, o)];
      if (!e || e.status !== 'done') continue;
      expect('headache' in e.sym).toBe(days.includes(o));
    }
  });
});
