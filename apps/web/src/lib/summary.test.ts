import { describe, it, expect } from 'vitest';
import { sum, det, ctxLine } from './summary';
import { makeSymDef } from './utils';
import { SYM } from '../constants/symptoms';
import type { Ctx, DoneEntry, DraftEntry, SymptomDef } from './types';

const symDef = makeSymDef([]);
const ctx0: Ctx = { stress: null, sleepQ: null, sleepH: null, activity: null, actType: '', heat: null, extras: [] };

function done(p: Partial<DoneEntry> = {}): DoneEntry {
  return {
    status: 'done', wb: null, sym: {}, absent: [], ctx: ctx0,
    note: '', flare: null, noSymptoms: false, filledLater: false, ...p
  };
}

const draft: DraftEntry = {
  status: 'draft',
  d: {
    wb: 6, wbSkip: false, sel: ['fatigue'], sym: { fatigue: { int: 3 } },
    absent: [], groupId: null,
    ctx: { ...ctx0, stress: 4 }, note: '', flare: null,
    confirmed: false, noSymptoms: false, step: 3
  }
};

describe('sum — резюме дня', () => {
  it('еталонний запис → точний рядок (факти, без інтерпретацій)', () => {
    const e = done({
      wb: 7,
      sym: {
        fatigue: { int: 3, extra: ['Фізична'] },
        armWeak: { int: 4, side: 'Права' }
      },
      ctx: { ...ctx0, stress: 4, sleepQ: 2, sleepH: 6.5, heat: true }
    });
    expect(sum(e, '2026-07-21', symDef, true, ['2026-07-10'])).toBe(
      'самопочуття 7/10; втома 3/5 (фізична); слабкість у руці/руках 4/5, права; стрес 4/5; сон 2/5 (6.5 год); було дуже спекотно; цикл — день 12.'
    );
  });

  it('bool-симптом: без «/5», з епізодами', () => {
    const e = done({ sym: { cramps: { side: 'Обидві', ep: 2 } } });
    expect(sum(e, '2026-07-21', symDef, false, [])).toBe(
      'м’язові спазми / крампи в ногах, обидві, епізодів: 2.'
    );
  });

  it('quick absence summary uses its exact snapshot', () => {
    const e = done({ noSymptoms: true, absent: ['fatigue', 'numb'] });
    expect(sum(e, '2026-07-21', symDef, false, [])).toBe('не було жодного з відстежуваних симптомів.');
  });

  it('cycleOn=false → без частини про цикл', () => {
    const e = done({ wb: 5 });
    expect(sum(e, '2026-07-21', symDef, false, ['2026-07-10'])).toBe('самопочуття 5/10.');
  });

  it('сон без годин → без дужок', () => {
    const e = done({ ctx: { ...ctx0, sleepQ: 3 } });
    expect(sum(e, '2026-07-21', symDef, false, [])).toBe('сон 3/5.');
  });

  it('невідомий symDef → симптом пропущено', () => {
    const e = done({ wb: 6, sym: { zzz: { int: 4 } } });
    expect(sum(e, '2026-07-21', symDef, false, [])).toBe('самопочуття 6/10.');
  });

  it('draft і відсутній запис → порожній рядок', () => {
    expect(sum(draft, '2026-07-19', symDef, true, [])).toBe('');
    expect(sum(null, '2026-07-19', symDef, true, [])).toBe('');
    expect(sum(undefined, '2026-07-19', symDef, true, [])).toBe('');
  });
});

describe('det — детальний рядок значення', () => {
  const fatigue = SYM.find((s) => s.id === 'fatigue') as SymptomDef;
  const cramps = SYM.find((s) => s.id === 'cramps') as SymptomDef;

  it('scale з int → «3/5 — помірно»', () => {
    expect(det({ int: 3 }, fatigue)).toBe('3/5 — помірно');
  });

  it('bool з усіма деталями → повний рядок', () => {
    expect(det(
      { side: 'Ліва', extra: ['Болючі'], ep: 2, impact: 'Помітно', comment: 'після прогулянки' },
      cramps
    )).toBe('було; ліва; болючі; епізодів: 2; вплив: помітно; «після прогулянки»');
  });

  it('scale без int і деталей → «—»', () => {
    expect(det({}, fatigue)).toBe('—');
  });

  it('scale: int + extra', () => {
    expect(det({ int: 5, extra: ['Фізична', 'Когнітивна'] }, fatigue)).toBe(
      '5/5 — дуже сильно, суттєво заважає; фізична, когнітивна'
    );
  });
});

describe('ctxLine — рядок контексту', () => {
  it('стрес + сон із годинами + спека', () => {
    const e = done({ ctx: { ...ctx0, stress: 4, sleepQ: 2, sleepH: 6.5, heat: true } });
    expect(ctxLine(e)).toBe('стрес 4/5 · сон 2/5 (6.5 год) · спека');
  });

  it('сон без годин', () => {
    const e = done({ ctx: { ...ctx0, sleepQ: 3 } });
    expect(ctxLine(e)).toBe('сон 3/5');
  });

  it('порожній контекст → порожній рядок', () => {
    expect(ctxLine(done())).toBe('');
  });

  it('draft і відсутній запис → порожній рядок', () => {
    expect(ctxLine(draft)).toBe('');
    expect(ctxLine(null)).toBe('');
    expect(ctxLine(undefined)).toBe('');
  });
});
