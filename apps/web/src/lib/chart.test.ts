import { describe, it, expect } from 'vitest';
import { model, type ChartDeps } from './chart';
import { makeSymDef } from './utils';
import type { Ctx, DoneEntry, DraftEntry, Entry, SymValue } from './types';

const now = new Date('2026-07-21T12:00:00');
const symDef = makeSymDef([]);
const ctx0: Ctx = { stress: null, sleepQ: null, sleepH: null, activity: null, actType: '', heat: null, extras: [] };

function done(sym: Record<string, SymValue>, p: Partial<DoneEntry> = {}): DoneEntry {
  return {
    status: 'done', wb: null, sym, absent: [], ctx: ctx0,
    note: '', flare: null, noSymptoms: Object.keys(sym).length === 0, filledLater: false, ...p
  };
}

const draftE: DraftEntry = {
  status: 'draft',
  d: {
    wb: 6, wbSkip: false, sel: ['fatigue'], sym: { fatigue: { int: 3 } },
    absent: [], groupId: null,
    ctx: { ...ctx0, heat: true }, note: '', flare: null,
    confirmed: false, noSymptoms: false, step: 3
  }
};

function deps(entries: Record<string, Entry>, p: Partial<ChartDeps> = {}): ChartDeps {
  return { now, entries, cycleStarts: [], symDef, ...p };
}

describe('model — графік симптому', () => {
  it('серія [2, null, 3] → 2 сегменти (розрив, без інтерполяції)', () => {
    const m = model('fatigue', 3, 100, 100, deps({
      '2026-07-19': done({ fatigue: { int: 2 } }),
      '2026-07-21': done({ fatigue: { int: 3 } })
    }));
    expect(m.days.map((d) => d.v)).toEqual([2, null, 3]);
    expect(m.segs).toHaveLength(2);
    expect(m.segs[0].d).toBe('M 12.0 57.6 L 12.0 57.6');
    expect(m.segs[1].d).toBe('M 88.0 42.4 L 88.0 42.4');
  });

  it('суцільна серія → один сегмент, крапки додаються через L', () => {
    const m = model('fatigue', 3, 100, 100, deps({
      '2026-07-19': done({ fatigue: { int: 2 } }),
      '2026-07-20': done({ fatigue: { int: 5 } }),
      '2026-07-21': done({ fatigue: { int: 3 } })
    }));
    expect(m.segs).toHaveLength(1);
    expect(m.segs[0].d).toBe('M 12.0 57.6 L 12.0 57.6 L 50.0 12.0 L 88.0 42.4');
  });

  it('explicit absent day → point v=0; completed unknown stays a gap', () => {
    const m = model('fatigue', 3, 100, 100, deps({
      '2026-07-19': done({}),
      '2026-07-20': done({}, { absent: ['fatigue'] }),
      '2026-07-21': done({ fatigue: { int: 3 } })
    }));
    expect(m.days.map((d) => d.v)).toEqual([null, 0, 3]);
    expect(m.pts).toHaveLength(2);
    expect(m.pts[0]).toEqual({ x: 50, y: 88, v: 0, iso: '2026-07-20', i: 1, fill: 'var(--color-bg)' });
    expect(m.pts[1].fill).toBe('var(--color-accent)');
    expect({ known: m.known, present: m.present, absent: m.absent, unknown: m.unknownCompleted }).toEqual({ known: 2, present: 1, absent: 1, unknown: 1 });
  });

  it('present scale symptom without legacy intensity is known present but remains a numeric gap', () => {
    const m = model('fatigue', 2, 100, 100, deps({
      '2026-07-20': done({ fatigue: {} }),
      '2026-07-21': done({})
    }));
    expect(m.days.map((day) => ({ v: day.v, observation: day.observation }))).toEqual([
      { v: null, observation: 'present' },
      { v: null, observation: 'unknown' }
    ]);
    expect({ known: m.known, present: m.present, absent: m.absent, unknown: m.unknownCompleted })
      .toEqual({ known: 1, present: 1, absent: 0, unknown: 1 });
    expect(m.pts).toEqual([]);
  });

  it('пропущений день → жодної точки; чернетка → v=null і не рахується заповненим', () => {
    const m = model('fatigue', 3, 100, 100, deps({
      '2026-07-19': draftE,
      '2026-07-21': done({ fatigue: { int: 1 } })
    }));
    expect(m.days.map((d) => d.v)).toEqual([null, null, 1]);
    expect(m.pts).toHaveLength(1);
    expect(m.filled).toBe(1);
  });

  it('cover — «Заповнено 2 із 3 днів»', () => {
    const m = model('fatigue', 3, 100, 100, deps({
      '2026-07-19': done({ fatigue: { int: 2 } }),
      '2026-07-21': done({ fatigue: { int: 3 } })
    }));
    expect(m.cover).toBe('Заповнено 2 із 3 днів');
  });

  it('шкали: wb → 10, bool → 1, scale і спец-ключі → 5', () => {
    expect(model('wb', 3, 100, 100, deps({})).max).toBe(10);
    expect(model('numb', 3, 100, 100, deps({})).max).toBe(1);
    expect(model('fatigue', 3, 100, 100, deps({})).max).toBe(5);
    expect(model('stress', 3, 100, 100, deps({})).max).toBe(5);
  });

  it('спец-ключі читаються з ctx/wb', () => {
    const m = model('stress', 2, 100, 100, deps({
      '2026-07-21': done({}, { ctx: { ...ctx0, stress: 4 } })
    }));
    expect(m.days.map((d) => d.v)).toEqual([null, 4]);
  });

  it('маркери днів: heat/flare/menses; у чернетки heat не читається', () => {
    const m = model('fatigue', 3, 100, 100, deps({
      '2026-07-19': draftE,
      '2026-07-20': done({}, { ctx: { ...ctx0, heat: true }, flare: { isNew: true, dur24: false, temp: false, note: '' } }),
      '2026-07-21': done({ fatigue: { int: 3 } })
    }, { cycleStarts: ['2026-07-21'] }));
    expect(m.days.map((d) => d.heat)).toEqual([false, true, false]);
    expect(m.days.map((d) => d.flare)).toEqual([false, true, false]);
    expect(m.days.map((d) => d.menses)).toEqual([false, false, true]);
  });

  it('геометрія: pad=12, x рівномірний, y інвертований; n=1 → центр', () => {
    const m = model('fatigue', 3, 100, 100, deps({}));
    expect(m.x(0)).toBe(12);
    expect(m.x(2)).toBe(88);
    expect(m.y(5)).toBe(12);
    expect(m.y(0)).toBe(88);
    const m1 = model('fatigue', 1, 100, 100, deps({}));
    expect(m1.x(0)).toBe(50);
  });

  it('порядок днів: 0 — найдавніший, n-1 — сьогодні', () => {
    const m = model('fatigue', 3, 100, 100, deps({}));
    expect(m.days.map((d) => d.iso)).toEqual(['2026-07-19', '2026-07-20', '2026-07-21']);
    expect(m.n).toBe(3);
  });
});
