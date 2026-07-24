import { describe, expect, it } from 'vitest';
import {
  TOP_NO_DATA_ROW,
  TOP_NO_HIGH_ROW,
  avgOf,
  buildRows,
  buildSums,
  buildTop,
  ctxSummary,
  cycleLine,
  cycleStartsInPeriod,
  daysDesc,
  filledEntries,
  flaresInPeriod,
  generatedLabel,
  notesInPeriod,
  periodLabel
} from './report';
import { makeSymDef } from './utils';
import { isoOff } from './dates';
import type { ChartDeps } from './chart';
import type { Ctx, DoneEntry, DraftEntry, Entry, SymValue } from './types';

const now = new Date('2026-07-21T12:00:00');
const symDef = makeSymDef([]);
const ctx0: Ctx = {
  stress: null, sleepQ: null, sleepH: null, activity: null, actType: '', heat: null, extras: []
};

function done(sym: Record<string, SymValue>, p: Partial<DoneEntry> = {}): DoneEntry {
  return {
    status: 'done', wb: null, sym, absent: [], ctx: ctx0,
    note: '', flare: null, noSymptoms: false, filledLater: false, ...p
  };
}

const draftE: DraftEntry = {
  status: 'draft',
  d: {
    wb: 6, wbSkip: false, sel: ['fatigue'], sym: { fatigue: { int: 5 } },
    absent: [], groupId: null, ctx: { ...ctx0, stress: 5 }, note: '', flare: null,
    confirmed: false, noSymptoms: false, step: 3
  }
};

function deps(entries: Record<string, Entry>): ChartDeps {
  return { now, entries, cycleStarts: [], symDef };
}

describe('report labels', () => {
  it('builds descending dates and deterministic period/generated labels', () => {
    expect(daysDesc(3, now)).toEqual(['2026-07-21', '2026-07-20', '2026-07-19']);
    expect(periodLabel(7, now)).toBe('Останні 7 днів: 15.07 — 21.07');
    expect(generatedLabel(now)).toBe('Сформовано 21.07.2026');
  });
});

describe('buildRows — one complete preview/print dataset', () => {
  it.each([
    { days: 13, symptoms: ['fatigue'], expected: 13 },
    { days: 30, symptoms: ['fatigue'], expected: 30 },
    { days: 50, symptoms: ['fatigue', 'headache'], expected: 100 }
  ])('keeps all $expected rows', ({ days, symptoms, expected }) => {
    const entries: Record<string, Entry> = {};
    for (let offset = 0; offset > -days; offset--) {
      entries[isoOff(now, offset)] = done({ fatigue: { int: 2 }, headache: { int: 3 } });
    }
    const rows = buildRows(90, symptoms, entries, symDef, now);
    expect(rows).toHaveLength(expected);
  });

  it('deduplicates shared selections and keeps row details', () => {
    const entries: Record<string, Entry> = {
      '2026-07-21': done({
        armWeak: { int: 4, side: 'Права', ep: 2, impact: 'Помітно', comment: 'зранку' },
        numb: { side: 'Ліва', extra: ['Руки'] },
        fatigue: {}
      })
    };
    const rows = buildRows(7, ['armWeak', 'numb', 'fatigue', 'armWeak'], entries, symDef, now);
    expect(rows).toEqual([
      { iso: '2026-07-21', sid: 'armWeak', d: '21.07', s: 'Слабкість у руці/руках', i: '4/5', l: 'Права', c: '2 еп.; Помітно; зранку' },
      { iso: '2026-07-21', sid: 'numb', d: '21.07', s: 'Оніміння кінцівок або пальців', i: 'був', l: 'Ліва; Руки', c: '—' },
      { iso: '2026-07-21', sid: 'fatigue', d: '21.07', s: 'Втома', i: 'не заповнено', l: '—', c: '—' }
    ]);
  });

  it('skips drafts, absent values, and unselected present symptoms', () => {
    const entries: Record<string, Entry> = {
      '2026-07-20': draftE,
      '2026-07-21': done({ mood: { int: 2 } }, { absent: ['fatigue'] })
    };
    expect(buildRows(7, ['fatigue'], entries, symDef, now)).toEqual([]);
  });
});

describe('buildSums — known denominator', () => {
  const entries: Record<string, Entry> = {
    '2026-07-19': done({ fatigue: { int: 2 }, numb: { side: 'Ліва' } }),
    '2026-07-20': done({}, { absent: ['fatigue', 'numb'] }),
    '2026-07-21': done({ fatigue: { int: 4 } })
  };

  it('uses present + explicit absent, never every completed day', () => {
    expect(buildSums(3, ['numb'], deps(entries))).toEqual([{
      id: 'numb', present: 1, absent: 1, unknown: 1, known: 2,
      t: 'Оніміння кінцівок або пальців: було 1 із 2 відомих спостережень · підтверджено не було 1 · не заповнено у завершені дні 1'
    }]);
  });

  it('keeps intensity statistics restricted to present numeric values', () => {
    expect(buildSums(3, ['fatigue'], deps(entries))).toEqual([{
      id: 'fatigue', present: 2, absent: 1, unknown: 0, known: 3,
      t: 'Втома: було 2 із 3 відомих спостережень · підтверджено не було 1 · не заповнено у завершені дні 0 · середня інтенсивність у дні зі значенням 3.0/5 · максимум 4/5'
    }]);
  });

  it('reports completed unknown separately and deduplicates the selection', () => {
    expect(buildSums(3, ['mood', 'mood'], deps(entries))).toEqual([{
      id: 'mood', present: 0, absent: 0, unknown: 3, known: 0,
      t: 'Пригніченість настрою: було 0 із 0 відомих спостережень · підтверджено не було 0 · не заповнено у завершені дні 3'
    }]);
  });
});

describe('buildTop — honest empty states', () => {
  it('sorts high scale values and deduplicates selected symptoms', () => {
    const entries: Record<string, Entry> = {
      '2026-07-17': done({ fatigue: { int: 3 } }),
      '2026-07-18': done({ fatigue: { int: 5 } }),
      '2026-07-19': done({ fatigue: { int: 2 } }),
      '2026-07-20': done({ fatigue: { int: 3 } }),
      '2026-07-21': done({ fatigue: { int: 4 }, headache: { int: 3 } })
    };
    const result = buildTop(7, ['fatigue', 'headache', 'fatigue'], entries, symDef, now);
    expect(result.hasIntensityData).toBe(true);
    expect(result.rows).toHaveLength(3);
    expect(result.rows[0]).toEqual({ best: 5, d: '18.07', t: 'втома 5/5' });
    expect(result.rows[1]).toEqual({ best: 4, d: '21.07', t: 'втома 4/5, головний біль 3/5' });
  });

  it('distinguishes insufficient intensity data from known low values', () => {
    const boolOnly = buildTop(7, ['numb'], { '2026-07-21': done({ numb: {} }) }, symDef, now);
    expect(boolOnly).toEqual({ rows: [], hasIntensityData: false });
    expect(TOP_NO_DATA_ROW.t).toContain('недостатньо');

    const low = buildTop(7, ['fatigue'], { '2026-07-21': done({ fatigue: { int: 2 } }) }, symDef, now);
    expect(low).toEqual({ rows: [], hasIntensityData: true });
    expect(TOP_NO_HIGH_ROW.t).toContain('3–5');
  });
});

describe('day-level context', () => {
  const entries: Record<string, Entry> = {
    '2026-07-18': draftE,
    '2026-07-19': done({}, { ctx: { ...ctx0, stress: 4, sleepQ: 2, sleepH: 6, activity: false, heat: true } }),
    '2026-07-20': done({}, { ctx: { ...ctx0, sleepQ: 3, activity: true } }),
    '2026-07-21': done({}, { ctx: { ...ctx0, stress: 2, sleepH: 8, heat: false } })
  };

  it('uses only done entries and ignores null in numeric averages', () => {
    const filled = filledEntries(7, entries, now);
    expect(filled).toHaveLength(3);
    expect(avgOf(filled, 'stress')).toBe('3.0');
    expect(avgOf(filled, 'sleepQ')).toBe('2.5');
    expect(avgOf([], 'stress')).toBe('—');
  });

  it('distinguishes explicit false from not filled', () => {
    expect(ctxSummary(filledEntries(7, entries, now))).toBe(
      'Стрес: середній 3.0/5 · Якість сну: середня 2.5/5 · Години сну: 7.0 год · ' +
      'Фізична активність: так 1, ні 1, не заповнено 1 · Спека / перегрів: так 1, ні 1, не заповнено 1'
    );
  });
});

describe('day-level report samples', () => {
  it('returns flare labels, notes, and cycle dates in-period only', () => {
    const entries: Record<string, Entry> = {
      '2026-07-10': done({}, { note: 'поза періодом' }),
      '2026-07-19': done({}, { flare: { isNew: true, dur24: true, temp: false, note: 'сильніша слабкість', groupIds: ['g1'] } }),
      '2026-07-20': done({}, { note: 'спекотно', flare: { isNew: false, dur24: false, temp: false, note: '' } }),
      '2026-07-21': done({})
    };
    expect(flaresInPeriod(7, entries, now)).toEqual([
      { d: '20.07', t: 'позначено користувачкою', groupIds: [] },
      { d: '19.07', t: 'сильніша слабкість', groupIds: ['g1'] }
    ]);
    expect(notesInPeriod(7, entries, now)).toEqual([{ d: '20.07', t: 'спекотно' }]);
    expect(cycleStartsInPeriod(7, ['2026-07-10', '2026-07-15', '2026-07-21', '2026-07-22'], now))
      .toEqual(['2026-07-15', '2026-07-21']);
  });

  it('formats both cycle states', () => {
    expect(cycleLine(['2026-07-15', '2026-07-21'])).toBe(
      'Початок менструації: 15.07, 21.07. День циклу рахується від останньої позначеної дати.'
    );
    expect(cycleLine([])).toBe('У цьому періоді позначених початків циклу немає.');
  });
});
