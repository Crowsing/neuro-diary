import { describe, expect, it } from 'vitest';
import { dataToCsv, stateToJson } from './export';
import { baseState, emptyData, parseStateJson } from '../state/persist';

describe('dataToCsv', () => {
  it('preserves existing columns and appends explicit absence/current groups', () => {
    const data = emptyData();
    data.groups = [{ id: 'g1', name: 'Стан, «А»', archived: false }];
    data.symptomGroupIds = { fatigue: ['g1'], numb: ['g1'] };
    data.entries['2026-01-15'] = {
      status: 'done', wb: 6, absent: ['numb'], noSymptoms: false, filledLater: false,
      sym: { fatigue: { int: 3, comment: 'Після "роботи"' } },
      ctx: { stress: 4, sleepQ: 2, sleepH: null, activity: false, actType: '', heat: null, extras: [] },
      note: 'Рядок, один\nрядок "два"', flare: null
    };

    const csv = dataToCsv(data);
    expect(csv.split('\r\n')[0]).toBe(
      'date,status,wellbeing,confirmed,noSymptoms,symptoms,context,note,flare,absentSymptoms,symptomGroups'
    );
    expect(csv).toContain('""fatigue""');
    expect(csv).toContain('""numb""');
    expect(csv).toContain('Стан, «А»');
    expect(csv).toContain('Рядок, один\nрядок ""два""');
    expect(csv).toContain('""sleepH"":null');
    expect(csv).toContain('""activity"":false');
    expect(csv).toContain('""heat"":null');
  });

  it('derives symptomGroups from current organization, not the entry date', () => {
    const data = emptyData();
    data.groups = [{ id: 'g1', name: 'Нова назва', archived: false }];
    data.symptomGroupIds = { fatigue: ['g1'] };
    data.entries['2020-01-01'] = {
      status: 'done', wb: null, sym: { fatigue: { int: 2 } }, absent: [],
      ctx: { stress: null, sleepQ: null, sleepH: null, activity: null, actType: '', heat: null, extras: [] },
      note: '', flare: null, noSymptoms: false, filledLater: false
    };
    expect(dataToCsv(data)).toContain('Нова назва');
  });

  it('exports only selected draft symptoms, not stale recovery details', () => {
    const data = emptyData();
    data.entries['2026-01-15'] = {
      status: 'draft',
      d: {
        wb: null, wbSkip: false, sel: ['mood'],
        sym: { fatigue: { int: 5 }, mood: { int: 2 } }, absent: [], groupId: null,
        ctx: { stress: null, sleepQ: null, sleepH: null, activity: null, actType: '', heat: null, extras: [] },
        note: '', flare: null, confirmed: false, noSymptoms: false, step: 2
      }
    };
    const csv = dataToCsv(data);
    expect(csv).toContain('""mood""');
    expect(csv).not.toContain('""fatigue""');
  });
});

describe('stateToJson', () => {
  it('round-trips v4 groups, explicit absence, nullable context, drafts, and report settings', () => {
    const now = new Date('2026-01-15T12:00:00');
    const data = emptyData();
    data.active = ['fatigue'];
    data.archived = ['numb'];
    data.groups = [{ id: 'g1', name: 'Неврологічна група', archived: false }];
    data.symptomGroupIds = { fatigue: ['g1'], numb: ['g1'] };
    data.entries['2026-01-15'] = {
      status: 'done', wb: 6, sym: { fatigue: { int: 3 } }, absent: ['numb'],
      ctx: { stress: null, sleepQ: 2, sleepH: null, activity: false, actType: '', heat: null, extras: [] },
      note: 'Нотатка', flare: null, noSymptoms: false, filledLater: false
    };
    data.entries['2026-01-14'] = {
      status: 'draft',
      d: {
        wb: null, wbSkip: false, sel: [], sym: {}, absent: ['fatigue'], groupId: 'g1',
        ctx: { stress: null, sleepQ: null, sleepH: null, activity: null, actType: '', heat: null, extras: [] },
        note: '', flare: null, confirmed: false, noSymptoms: false, step: 2, symIdx: 0, ctxMore: false
      }
    };
    const state = {
      ...baseState(),
      view: 'app' as const,
      report: {
        ...baseState().report,
        step: 3 as const,
        period: 30 as const,
        groupIds: ['g1'],
        syms: ['fatigue', 'numb'],
        includeGroupNames: true,
        name: 'Олена',
        dob: '1990-02-03'
      },
      data,
      dialog: { type: 'pdf' as const },
      toast: 'Збережено'
    };

    const json = stateToJson(state);
    const raw = JSON.parse(json);
    const roundTrip = parseStateJson(json, now);
    expect(raw.dialog).toBeUndefined();
    expect(raw.toast).toBeUndefined();
    expect(raw.obRemOn).toBeUndefined();
    expect(raw.obTime).toBeUndefined();
    expect(raw.data.remOn).toBeUndefined();
    expect(raw.data.remTime).toBeUndefined();
    expect(raw.data.schemaVersion).toBe(4);
    expect(roundTrip.data).toEqual(data);
    expect(roundTrip.report).toEqual(state.report);
    expect(roundTrip.report.name).toBe('Олена');
    expect(roundTrip.report.dob).toBe('1990-02-03');
  });
});
