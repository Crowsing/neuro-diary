import { describe, it, expect, beforeEach } from 'vitest';
import { appReducer } from './reducer';
import type { Env } from './reducer';
import { baseState, emptyData, load, save, STORAGE_KEY } from './persist';
import { initDraft } from '../lib/checkin';
import type { AppData, AppState, CheckinDraft, Ctx, DoneEntry } from '../lib/types';
import { entryState } from '../lib/entry';

// ---------------------------------------------------------------------------
// Фікстури (Object.freeze — ловимо мутації стану)
// ---------------------------------------------------------------------------

const NOW = new Date('2026-07-21T12:00:00');
const TODAY = '2026-07-21';
const YEST = '2026-07-20';

const env: Env = { now: NOW, newId: () => 'c-test' };
const red = appReducer(env);

function deepFreeze<T>(o: T): T {
  if (o && typeof o === 'object' && !Object.isFrozen(o)) {
    Object.freeze(o);
    Object.getOwnPropertyNames(o).forEach((k) => deepFreeze((o as Record<string, unknown>)[k]));
  }
  return o;
}

const ctx0: Ctx = { stress: null, sleepQ: null, sleepH: null, activity: null, actType: '', heat: null, extras: [] };

function done(p: Partial<DoneEntry> = {}): DoneEntry {
  return {
    status: 'done', wb: 7, sym: {}, absent: [], ctx: ctx0,
    note: '', flare: null, noSymptoms: false, filledLater: false, ...p
  };
}

function mkData(p: Partial<AppData> = {}): AppData {
  return {
    entries: {}, cycleStarts: [], active: ['fatigue', 'mood', 'armWeak'], archived: [], custom: [],
    groups: [], symptomGroupIds: {}, cycleOn: true, lock: false, ...p,
    schemaVersion: 4
  };
}

function mkState(p: Partial<AppState> = {}): AppState {
  return deepFreeze({ ...baseState(), view: 'app' as const, data: mkData(), ...p });
}

function d0(p: Partial<CheckinDraft> = {}): CheckinDraft {
  return { ...initDraft(), ...p };
}

function withCheckin(d: CheckinDraft, p: Partial<AppState> = {}, back: 'day' | null = null): AppState {
  return mkState({ sub: 'checkin', checkin: { date: TODAY, d, back }, ...p });
}

// ---------------------------------------------------------------------------
// CHECKIN_PATCH (udr, 1177–1183) — дзеркальна чернетка
// ---------------------------------------------------------------------------

describe('CHECKIN_PATCH — атомарний патч чернетки', () => {
  it('без done-запису дзеркалить чернетку в entries[date]', () => {
    const st = withCheckin(d0());
    const next = red(st, { type: 'CHECKIN_PATCH', patch: { wb: 5, wbSkip: false } });
    expect(next.checkin!.d.wb).toBe(5);
    expect(next.data.entries[TODAY]).toEqual({ status: 'draft', d: { ...d0(), wb: 5 } });
  });

  it('оновлює наявну draft-копію', () => {
    const st = withCheckin(d0({ wb: 4 }), {
      data: mkData({ entries: { [TODAY]: { status: 'draft', d: d0({ wb: 4 }) } } })
    });
    const next = red(st, { type: 'CHECKIN_PATCH', patch: { note: 'привіт' } });
    const e = next.data.entries[TODAY];
    expect(e.status).toBe('draft');
    expect(e.status === 'draft' && e.d.note).toBe('привіт');
    expect(e.status === 'draft' && e.d.wb).toBe(4);
  });

  it('НЕ дзеркалить, якщо запис за цю дату вже done (інваріант AC2)', () => {
    const doneE = done({ wb: 9 });
    const st = withCheckin(d0(), { data: mkData({ entries: { [TODAY]: doneE } }) });
    const next = red(st, { type: 'CHECKIN_PATCH', patch: { wb: 2 } });
    expect(next.checkin!.d.wb).toBe(2);
    expect(next.data.entries[TODAY]).toBe(doneE); // не перезаписано й не обгорнуто
  });

  it('без відкритого чек-іну стан не змінюється', () => {
    const st = mkState();
    expect(red(st, { type: 'CHECKIN_PATCH', patch: { wb: 1 } })).toBe(st);
  });
});

describe('CHECKIN_SYM_PATCH / CHECKIN_CTX_PATCH — вкладені патчі', () => {
  it('мержить значення симптому на 3 рівнях і дзеркалить у entries', () => {
    const st = withCheckin(d0({ sel: ['fatigue'], sym: { fatigue: { int: 2 } } }));
    const next = red(st, { type: 'CHECKIN_SYM_PATCH', id: 'fatigue', patch: { extra: ['Фізична'] } });
    expect(next.checkin!.d.sym.fatigue).toEqual({ int: 2, extra: ['Фізична'] });
    const e = next.data.entries[TODAY];
    expect(e.status === 'draft' && e.d.sym.fatigue).toEqual({ int: 2, extra: ['Фізична'] });
  });

  it('створює значення для нового симптому', () => {
    const st = withCheckin(d0({ sel: ['mood'] }));
    const next = red(st, { type: 'CHECKIN_SYM_PATCH', id: 'mood', patch: { int: 5 } });
    expect(next.checkin!.d.sym.mood).toEqual({ int: 5 });
  });

  it('мержить контекст дня', () => {
    const st = withCheckin(d0());
    const next = red(st, { type: 'CHECKIN_CTX_PATCH', patch: { stress: 4, heat: true } });
    expect(next.checkin!.d.ctx).toEqual({ ...ctx0, stress: 4, heat: true });
  });
});

// ---------------------------------------------------------------------------
// CHECKIN_EXIT (exitCheckin, 1198–1206)
// ---------------------------------------------------------------------------

describe('CHECKIN_EXIT', () => {
  it('saveDraft:true зберігає чернетку і показує тост', () => {
    const st = withCheckin(d0({ wb: 3 }));
    const next = red(st, { type: 'CHECKIN_EXIT', saveDraft: true });
    expect(next.checkin).toBeNull();
    expect(next.sub).toBeNull();
    expect(next.data.entries[TODAY]).toEqual({ status: 'draft', d: d0({ wb: 3 }) });
    expect(next.toast).toBe('Чернетку збережено');
  });

  it('saveDraft:true НЕ затирає done-запис', () => {
    const doneE = done({ wb: 8 });
    const st = withCheckin(d0({ wb: 1 }), { data: mkData({ entries: { [TODAY]: doneE } }) });
    const next = red(st, { type: 'CHECKIN_EXIT', saveDraft: true });
    expect(next.data.entries[TODAY]).toBe(doneE);
    expect(next.checkin).toBeNull();
    expect(next.toast).toBeNull();
  });

  it('saveDraft:false не пише чернетку і не показує тост', () => {
    const st = withCheckin(d0({ wb: 3 }));
    const next = red(st, { type: 'CHECKIN_EXIT', saveDraft: false });
    expect(next.data.entries[TODAY]).toBeUndefined();
    expect(next.toast).toBeNull();
  });

  it('back:"day" повертає на екран дня', () => {
    const st = withCheckin(d0(), {}, 'day');
    const next = red(st, { type: 'CHECKIN_EXIT', saveDraft: false });
    expect(next.sub).toBe('day');
    expect(next.tab).toBe('history');
    expect(next.selDay).toBe(TODAY);
  });

  it('back:null не чіпає tab/selDay (лише sub:null)', () => {
    const st = withCheckin(d0(), { tab: 'history', selDay: YEST });
    const next = red(st, { type: 'CHECKIN_EXIT', saveDraft: false });
    expect(next.sub).toBeNull();
    expect(next.tab).toBe('history');
    expect(next.selDay).toBe(YEST);
  });
});

// ---------------------------------------------------------------------------
// CHECKIN_FINISH (fin, 1187–1196)
// ---------------------------------------------------------------------------

describe('CHECKIN_FINISH', () => {
  const draft = d0({
    wb: 7, sel: ['fatigue'],
    sym: { fatigue: { int: 3, extra: ['Фізична'], more: true }, mood: { int: 2 } },
    note: 'нотатка', confirmed: true, step: 6
  });

  it('формує done-запис: лише sel-симптоми, без more', () => {
    const st = withCheckin(draft);
    const next = red(st, { type: 'CHECKIN_FINISH' });
    expect(next.data.entries[TODAY]).toEqual({
      status: 'done', wb: 7,
      sym: { fatigue: { int: 3, extra: ['Фізична'] } },
      absent: [], ctx: ctx0, note: 'нотатка', flare: null,
      noSymptoms: false, filledLater: false
    });
    expect(next.checkin).toBeNull();
    expect(next.sub).toBeNull();
    expect(next.tab).toBe('today');
    expect(next.selDay).toBeNull();
    expect(next.toast).toBe('Збережено');
  });

  it('минула дата без prev → filledLater:true; back:"day" → нав. на день', () => {
    const st = mkState({ sub: 'checkin', checkin: { date: YEST, d: draft, back: 'day' } });
    const next = red(st, { type: 'CHECKIN_FINISH' });
    const e = next.data.entries[YEST];
    expect(e.status === 'done' && e.filledLater).toBe(true);
    expect(next.sub).toBe('day');
    expect(next.tab).toBe('history');
    expect(next.selDay).toBe(YEST);
  });

  it('prev done зберігає свій filledLater', () => {
    const st = mkState({
      sub: 'checkin',
      checkin: { date: YEST, d: draft, back: null },
      data: mkData({ entries: { [YEST]: done({ filledLater: false }) } })
    });
    const next = red(st, { type: 'CHECKIN_FINISH' });
    const e = next.data.entries[YEST];
    expect(e.status === 'done' && e.filledLater).toBe(false);
  });

  it('wbSkip → wb:null; explicit absent snapshot can retain noSymptoms', () => {
    const st = withCheckin(d0({ wbSkip: true, wb: 9, absent: ['fatigue'], noSymptoms: true, step: 6 }));
    const next = red(st, { type: 'CHECKIN_FINISH' });
    const e = next.data.entries[TODAY];
    expect(e.status === 'done' && e.wb).toBeNull();
    expect(e.status === 'done' && e.noSymptoms).toBe(true);
    expect(e.status === 'done' && e.absent).toEqual(['fatigue']);
  });
});

// ---------------------------------------------------------------------------
// CHECKIN_START (startCheckin, 1164–1175)
// ---------------------------------------------------------------------------

describe('CHECKIN_START — пріоритет джерел чернетки', () => {
  it('нова дата → initDraft, sub:"checkin"', () => {
    const st = mkState();
    const next = red(st, { type: 'CHECKIN_START', iso: TODAY, back: null });
    expect(next.sub).toBe('checkin');
    expect(next.checkin).toEqual({ date: TODAY, d: initDraft(), back: null });
  });

  it('відкритий чек-ін тієї самої дати → його чернетка', () => {
    const d = d0({ wb: 2, step: 4 });
    const st = withCheckin(d);
    const next = red(st, { type: 'CHECKIN_START', iso: TODAY, back: null });
    expect(next.checkin!.d).toBe(d);
  });

  it('draft-запис → його чернетка', () => {
    const d = d0({ wb: 6, step: 3 });
    const st = mkState({ data: mkData({ entries: { [TODAY]: { status: 'draft', d } } }) });
    const next = red(st, { type: 'CHECKIN_START', iso: TODAY, back: null });
    expect(next.checkin!.d).toBe(d);
  });

  it('done-запис → draftFromDone (крок 6)', () => {
    const st = mkState({
      data: mkData({ entries: { [TODAY]: done({ wb: 8, sym: { fatigue: { int: 3, extra: ['Фізична'] } } }) } })
    });
    const next = red(st, { type: 'CHECKIN_START', iso: TODAY, back: 'day' });
    const d = next.checkin!.d;
    expect(d.step).toBe(6);
    expect(d.sel).toEqual(['fatigue']);
    expect(d.sym.fatigue!.more).toBe(true);
    expect(next.checkin!.back).toBe('day');
  });
});

// ---------------------------------------------------------------------------
// Сьогодні
// ---------------------------------------------------------------------------

describe('TODAY_NO_SYMPTOMS', () => {
  it('пише done-запис «симптомів не було» за сьогодні + тост', () => {
    const st = mkState();
    const next = red(st, { type: 'TODAY_NO_SYMPTOMS' });
    const e = next.data.entries[TODAY];
    expect(e).toEqual({
      status: 'done', wb: null, sym: {}, absent: ['fatigue', 'mood', 'armWeak'], ctx: ctx0,
      note: '', flare: null, noSymptoms: true, filledLater: false
    });
    expect(next.toast).toBe('Записано: відстежуваних симптомів цього дня не було');
  });
});

// ---------------------------------------------------------------------------
// Діалог менструації (1525–1540)
// ---------------------------------------------------------------------------

describe('MENSES_SET / MENSES_CONFIRM', () => {
  const dlg = { type: 'menses' as const, sel: 'today' as const, custom: TODAY, dup: false };

  it('MENSES_SET sel → скидає dup', () => {
    const st = mkState({ dialog: { ...dlg, dup: true } });
    const next = red(st, { type: 'MENSES_SET', sel: 'yest' });
    expect(next.dialog).toEqual({ ...dlg, sel: 'yest', dup: false, invalid: false });
  });

  it('MENSES_SET custom → sel:"custom"', () => {
    const st = mkState({ dialog: dlg });
    const next = red(st, { type: 'MENSES_SET', custom: '2026-07-10' });
    expect(next.dialog).toEqual({ type: 'menses', sel: 'custom', custom: '2026-07-10', dup: false, invalid: false });
  });

  it('підтвердження додає дату, сортує, вмикає cycleOn, закриває діалог', () => {
    const st = mkState({ dialog: { ...dlg, sel: 'yest' }, data: mkData({ cycleStarts: ['2026-07-25'], cycleOn: false }) });
    const next = red(st, { type: 'MENSES_CONFIRM' });
    expect(next.data.cycleStarts).toEqual([YEST, '2026-07-25']);
    expect(next.data.cycleOn).toBe(true);
    expect(next.dialog).toBeNull();
    expect(next.toast).toBe('Початок циклу позначено: 20.07');
  });

  it('дубль дати → лише прапор dup, без повторного додавання', () => {
    const st = mkState({ dialog: dlg, data: mkData({ cycleStarts: [TODAY] }) });
    const next = red(st, { type: 'MENSES_CONFIRM' });
    expect(next.dialog).toEqual({ ...dlg, dup: true });
    expect(next.data.cycleStarts).toEqual([TODAY]);
    expect(next.toast).toBeNull();
  });

  it('порожня custom-дата → доступна помилка без запису', () => {
    const st = mkState({ dialog: { ...dlg, sel: 'custom', custom: '' } });
    const next = red(st, { type: 'MENSES_CONFIRM' });
    expect(next.dialog).toEqual({ ...dlg, sel: 'custom', custom: '', invalid: true, dup: false });
    expect(next.data.cycleStarts).toEqual([]);
  });

  it('майбутня або неіснуюча custom-дата відхиляється в reducer', () => {
    for (const custom of ['2026-07-22', '2026-02-30']) {
      const st = mkState({ dialog: { ...dlg, sel: 'custom', custom } });
      const next = red(st, { type: 'MENSES_CONFIRM' });
      expect(next.dialog).toEqual({ ...dlg, sel: 'custom', custom, invalid: true, dup: false });
      expect(next.data.cycleStarts).toEqual([]);
    }
  });
});

describe('MENSES_REMOVE', () => {
  it('прибирає позначку + тост', () => {
    const st = mkState({ data: mkData({ cycleStarts: [YEST, TODAY] }) });
    const next = red(st, { type: 'MENSES_REMOVE', iso: YEST });
    expect(next.data.cycleStarts).toEqual([TODAY]);
    expect(next.toast).toBe('Позначку прибрано, день циклу перераховано');
  });
});

// ---------------------------------------------------------------------------
// Видалення (1542–1551)
// ---------------------------------------------------------------------------

describe('ENTRY_DELETE', () => {
  it('прибирає ключ, закриває діалог, тост', () => {
    const st = mkState({
      dialog: { type: 'delEntry', iso: YEST },
      data: mkData({ entries: { [YEST]: done(), [TODAY]: done() } })
    });
    const next = red(st, { type: 'ENTRY_DELETE', iso: YEST });
    expect(Object.keys(next.data.entries)).toEqual([TODAY]);
    expect(next.dialog).toBeNull();
    expect(next.toast).toBe('Запис видалено');
  });
});

describe('DATA_DELETE — усі scope-и', () => {
  const st = () => mkState({
    dialog: { type: 'delData' },
    sub: 'checkin',
    checkin: { date: TODAY, d: d0(), back: null },
    report: { ...baseState().report, name: 'Олена', dob: '1990-01-01' },
    data: mkData({ entries: { [YEST]: done() }, cycleStarts: [YEST] })
  });

  it('cycle: лише cycleStarts', () => {
    const next = red(st(), { type: 'DATA_DELETE', scope: 'cycle' });
    expect(next.data.cycleStarts).toEqual([]);
    expect(next.data.entries[YEST]).toBeDefined();
    expect(next.checkin).not.toBeNull();
    expect(next.dialog).toBeNull();
    expect(next.toast).toBe('Дані циклу видалено');
  });

  it('all: очищає весь personal/domain state, а не лише записи', () => {
    const next = red(st(), { type: 'DATA_DELETE', scope: 'all' });
    expect(next.data).toEqual(emptyData());
    expect(next.checkin).toBeNull();
    expect(next.report.name).toBe('');
    expect(next.report.dob).toBe('');
    expect(next.dialog).toBeNull();
    expect(next.toast).toBe('Усі дані видалено');
  });
});

// ---------------------------------------------------------------------------
// Діалог загострення (1556–1565)
// ---------------------------------------------------------------------------

describe('FLARE_* — діалог загострення', () => {
  const f0 = { isNew: false, dur24: false, temp: false, note: '' };
  const dlg = { type: 'flare' as const, iso: YEST, f: f0, had: false };

  it('TOGGLE перемикає прапор', () => {
    const st = mkState({ dialog: dlg });
    const next = red(st, { type: 'FLARE_DLG_TOGGLE', flag: 'isNew' });
    expect(next.dialog).toEqual({ ...dlg, f: { ...f0, isNew: true } });
  });

  it('NOTE оновлює нотатку', () => {
    const st = mkState({ dialog: dlg });
    const next = red(st, { type: 'FLARE_DLG_NOTE', note: 'текст' });
    expect(next.dialog).toEqual({ ...dlg, f: { ...f0, note: 'текст' } });
  });

  it('SAVE пише flare у done-запис + закриває + тост', () => {
    const st = mkState({
      dialog: { ...dlg, f: { ...f0, isNew: true } },
      data: mkData({ entries: { [YEST]: done() } })
    });
    const next = red(st, { type: 'FLARE_SAVE' });
    const e = next.data.entries[YEST];
    expect(e.status === 'done' && e.flare).toEqual({ ...f0, isNew: true });
    expect(next.dialog).toBeNull();
    expect(next.toast).toBe('Позначку збережено');
  });

  it('SAVE без запису → лише закриття + тост', () => {
    const st = mkState({ dialog: dlg });
    const next = red(st, { type: 'FLARE_SAVE' });
    expect(next.data.entries[YEST]).toBeUndefined();
    expect(next.dialog).toBeNull();
    expect(next.toast).toBe('Позначку збережено');
  });

  it('DELETE прибирає flare', () => {
    const st = mkState({
      dialog: { ...dlg, had: true },
      data: mkData({ entries: { [YEST]: done({ flare: { ...f0, isNew: true } }) } })
    });
    const next = red(st, { type: 'FLARE_DELETE' });
    const e = next.data.entries[YEST];
    expect(e.status === 'done' && e.flare).toBeNull();
    expect(next.toast).toBe('Позначку прибрано');
  });
});

// ---------------------------------------------------------------------------
// Навігація
// ---------------------------------------------------------------------------

describe('навігація', () => {
  it('NAV_TAB скидає sub/selDay/crisisAns', () => {
    const st = mkState({ sub: 'day', selDay: YEST, crisisAns: 'yes' });
    const next = red(st, { type: 'NAV_TAB', tab: 'trends' });
    expect(next).toEqual({ ...st, tab: 'trends', sub: null, selDay: null, crisisAns: '' });
  });

  it('NAV_SUB застосовує лише передані поля', () => {
    const st = mkState({ tab: 'trends', selDay: YEST });
    const next = red(st, { type: 'NAV_SUB', sub: 'sym', trendsSym: 'mood' });
    expect(next.sub).toBe('sym');
    expect(next.selDay).toBe(YEST); // не чіпаємо
    expect(next.trends).toEqual({ ...st.trends, sym: 'mood', sel: null });
  });

  it('NAV_SUB із явним selDay:null скидає день', () => {
    const st = mkState({ sub: 'day', selDay: YEST });
    const next = red(st, { type: 'NAV_SUB', sub: null, selDay: null, tab: 'history' });
    expect(next.selDay).toBeNull();
    expect(next.tab).toBe('history');
  });

  it('CAT_BACK: із чек-іну → назад у чек-ін', () => {
    const st = mkState({ sub: 'catalog', catFrom: 'checkin', catQ: 'біль' });
    const next = red(st, { type: 'CAT_BACK' });
    expect(next.sub).toBe('checkin');
    expect(next.catQ).toBe('');
  });

  it('CAT_BACK: із налаштувань → tab:"set"', () => {
    const st = mkState({ sub: 'catalog', catFrom: 'set', catQ: 'x', tab: 'set' });
    const next = red(st, { type: 'CAT_BACK' });
    expect(next.sub).toBeNull();
    expect(next.tab).toBe('set');
    expect(next.catQ).toBe('');
  });

  it('CRISIS_BACK: із чек-іном → sub:"checkin"', () => {
    const st = withCheckin(d0(), { sub: 'crisis', crisisAns: 'yes' });
    const next = red(st, { type: 'CRISIS_BACK' });
    expect(next.sub).toBe('checkin');
    expect(next.crisisAns).toBe('');
  });

  it('CRISIS_BACK: без чек-іну → sub:null', () => {
    const st = mkState({ sub: 'crisis', crisisAns: 'no' });
    const next = red(st, { type: 'CRISIS_BACK' });
    expect(next.sub).toBeNull();
    expect(next.crisisAns).toBe('');
  });
});

// ---------------------------------------------------------------------------
// Онбординг
// ---------------------------------------------------------------------------

describe('онбординг', () => {
  it('OB_SET патчить поля', () => {
    const st = mkState({ view: 'ob' });
    const next = red(st, { type: 'OB_SET', patch: { obStep: 2, obSyms: ['fatigue'] } });
    expect(next.obStep).toBe(2);
    expect(next.obSyms).toEqual(['fatigue']);
  });

  it('OB_FINISH with no explicit selection keeps the active list empty', () => {
    const st = mkState({ view: 'ob', obSyms: null, obCycle: true });
    const next = red(st, { type: 'OB_FINISH' });
    expect(next.view).toBe('app');
    expect(next.data.active).toEqual([]);
    expect(next.data.cycleOn).toBe(true);
  });

  it('OB_FINISH з obSyms підставляє вибрані', () => {
    const st = mkState({ view: 'ob', obSyms: ['mood'] });
    const next = red(st, { type: 'OB_FINISH' });
    expect(next.data.active).toEqual(['mood']);
  });
});

// ---------------------------------------------------------------------------
// Каталог і налаштування
// ---------------------------------------------------------------------------

describe('каталог', () => {
  it('CAT_MOVE міняє сусідів місцями', () => {
    const st = mkState();
    const next = red(st, { type: 'CAT_MOVE', index: 0, dir: 1 });
    expect(next.data.active).toEqual(['mood', 'fatigue', 'armWeak']);
  });

  it('CAT_MOVE за межами → без змін', () => {
    const st = mkState();
    expect(red(st, { type: 'CAT_MOVE', index: 0, dir: -1 })).toBe(st);
    expect(red(st, { type: 'CAT_MOVE', index: 2, dir: 1 })).toBe(st);
  });

  it('CAT_ARCHIVE / CAT_RESTORE', () => {
    const st = mkState();
    const a = red(st, { type: 'CAT_ARCHIVE', id: 'mood' });
    expect(a.data.active).toEqual(['fatigue', 'armWeak']);
    expect(a.data.archived).toEqual(['mood']);
    expect(a.toast).toBe('Переміщено в архів — історія збережена');
    const r = red(deepFreeze(a), { type: 'CAT_RESTORE', id: 'mood' });
    expect(r.data.archived).toEqual([]);
    expect(r.data.active).toEqual(['fatigue', 'armWeak', 'mood']);
  });

  it('CAT_ADD_CUSTOM: тримить назву, скидає newName, id з env.newId', () => {
    const st = mkState({ newName: '  Тремор  ', newType: 'scale' });
    const next = red(st, { type: 'CAT_ADD_CUSTOM' });
    expect(next.data.custom).toEqual([{ id: 'c-test', name: 'Тремор', cat: 'Власні', type: 'scale' }]);
    expect(next.data.active).toEqual(['fatigue', 'mood', 'armWeak', 'c-test']);
    expect(next.newName).toBe('');
    expect(next.toast).toBe('Симптом додано');
  });

  it('CAT_ADD_CUSTOM: порожня назва → лише тост', () => {
    const st = mkState({ newName: '   ' });
    const next = red(st, { type: 'CAT_ADD_CUSTOM' });
    expect(next.data).toBe(st.data);
    expect(next.toast).toBe('Введіть назву симптому');
  });

  it('CAT_ADD_FROM_LIB: bool-симптом із категорією', () => {
    const st = mkState();
    const next = red(st, { type: 'CAT_ADD_FROM_LIB', name: 'Тремор', cat: 'Рух і мʼязи' });
    expect(next.data.custom).toEqual([{ id: 'c-test', name: 'Тремор', cat: 'Рух і мʼязи', type: 'bool' }]);
    expect(next.data.active).toContain('c-test');
    expect(next.toast).toBe('Додано до активного списку');
  });

  it('CAT_SET патчить catQ/newName/newType', () => {
    const st = mkState();
    const next = red(st, { type: 'CAT_SET', patch: { catQ: 'зір', newType: 'scale' } });
    expect(next.catQ).toBe('зір');
    expect(next.newType).toBe('scale');
  });
});

describe('DATA_PATCH', () => {
  it('cycleOn — без тосту', () => {
    const st = mkState();
    const next = red(st, { type: 'DATA_PATCH', patch: { cycleOn: false } });
    expect(next.data.cycleOn).toBe(false);
    expect(next.toast).toBeNull();
  });

  it('lock → тост про увімкнення/вимкнення', () => {
    const st = mkState();
    const on = red(st, { type: 'DATA_PATCH', patch: { lock: true } });
    expect(on.toast).toBe('App lock увімкнено (демо)');
    const off = red(deepFreeze(on), { type: 'DATA_PATCH', patch: { lock: false } });
    expect(off.toast).toBe('App lock вимкнено (демо)');
  });
});

describe('v4 group and historical invariants', () => {
  const historical = done({ sym: { fatigue: { int: 3 } }, absent: ['mood'] });
  const groupedData = () => mkData({
    entries: { [YEST]: historical },
    groups: [{ id: 'a', name: 'A', archived: false }, { id: 'b', name: 'B', archived: false }],
    symptomGroupIds: { fatigue: ['a', 'b'], mood: ['a'] }
  });

  it('archive/restore/regroup never changes historical observation values', () => {
    const state = mkState({ data: groupedData() });
    const archived = red(state, { type: 'CAT_ARCHIVE', id: 'fatigue' });
    const regrouped = red(archived, { type: 'SYMPTOM_GROUPS_SET', id: 'fatigue', groupIds: ['b'] });
    const restored = red(regrouped, { type: 'CAT_RESTORE', id: 'fatigue' });
    expect(restored.data.entries[YEST]).toBe(historical);
    expect(entryState(restored.data.entries[YEST], 'fatigue')).toBe('present');
    expect(entryState(restored.data.entries[YEST], 'mood')).toBe('absent');
  });

  it('delete group removes only the group and mappings', () => {
    const state = mkState({ data: groupedData(), dialog: { type: 'delGroup', id: 'a' } });
    const next = red(state, { type: 'GROUP_DELETE', id: 'a' });
    expect(next.data.groups.map((group) => group.id)).toEqual(['b']);
    expect(next.data.symptomGroupIds).toEqual({ fatigue: ['b'] });
    expect(next.data.entries[YEST]).toBe(historical);
  });

  it('archive clears invisible draft filters; delete also removes only organizational flare labels', () => {
    const flare = { isNew: true, dur24: false, temp: false, note: 'факт', groupIds: ['a', 'b'] };
    const draft = d0({ groupId: 'a', flare });
    const state = mkState({
      checkin: { date: TODAY, d: draft, back: null },
      newGroupIds: ['a', 'b'],
      data: mkData({
        entries: {
          [YEST]: done({ sym: { fatigue: { int: 3 } }, flare }),
          [TODAY]: { status: 'draft', d: draft }
        },
        groups: [{ id: 'a', name: 'A', archived: false }, { id: 'b', name: 'B', archived: false }],
        symptomGroupIds: { fatigue: ['a', 'b'] }
      })
    });
    const archived = red(state, { type: 'GROUP_ARCHIVE', id: 'a' });
    expect(archived.checkin?.d.groupId).toBeNull();
    expect(archived.newGroupIds).toEqual(['b']);
    expect(archived.data.entries[TODAY].status === 'draft' && archived.data.entries[TODAY].d.groupId).toBeNull();
    expect(archived.data.entries[YEST].status === 'done' && archived.data.entries[YEST].flare?.groupIds).toEqual(['a', 'b']);

    const deleted = red(archived, { type: 'GROUP_DELETE', id: 'a' });
    expect(deleted.data.entries[YEST].status === 'done' && deleted.data.entries[YEST].sym.fatigue.int).toBe(3);
    expect(deleted.data.entries[YEST].status === 'done' && deleted.data.entries[YEST].flare?.groupIds).toEqual(['b']);
    expect(deleted.data.entries[TODAY].status === 'draft' && deleted.data.entries[TODAY].d.flare?.groupIds).toEqual(['b']);
    expect(deleted.checkin?.d.flare?.groupIds).toEqual(['b']);
  });

  it('deleting the last observation of an archived symptom normalizes report selection', () => {
    const state = mkState({
      report: { ...baseState().report, period: 7, syms: ['fatigue'] },
      data: mkData({
        active: [], archived: ['fatigue'],
        entries: { [YEST]: done({ sym: { fatigue: { int: 3 } } }) }
      })
    });
    const next = red(state, { type: 'ENTRY_DELETE', iso: YEST });
    expect(next.report.syms).toEqual([]);
  });

  it('duplicate group names are rejected case-insensitively', () => {
    const state = mkState({ data: groupedData(), groupName: ' a ' });
    const next = red(state, { type: 'GROUP_CREATE' });
    expect(next.data.groups).toHaveLength(2);
    expect(next.groupError).toMatch(/вже існує/i);
  });
});

describe('future-date reducer guards', () => {
  const FUTURE = '2026-07-22';
  it('cannot open a future day or start a future check-in', () => {
    const state = mkState();
    expect(red(state, { type: 'CHECKIN_START', iso: FUTURE, back: null })).toBe(state);
    expect(red(state, { type: 'NAV_SUB', sub: 'day', selDay: FUTURE })).toBe(state);
  });

  it('rejects a future custom cycle date', () => {
    const state = mkState({ dialog: { type: 'menses', sel: 'custom', custom: FUTURE, dup: false } });
    const next = red(state, { type: 'MENSES_CONFIRM' });
    expect(next.data.cycleStarts).toEqual([]);
    expect(next.dialog).toMatchObject({ type: 'menses', invalid: true });
  });
});

// ---------------------------------------------------------------------------
// Історія / тренди / звіт / тост
// ---------------------------------------------------------------------------

describe('історія, тренди, звіт', () => {
  it('HIST_CAL_PREV/NEXT: histShift не піднімається вище 0', () => {
    const st = mkState({ histShift: -1 });
    expect(red(st, { type: 'HIST_CAL_PREV' }).histShift).toBe(-2);
    expect(red(st, { type: 'HIST_CAL_NEXT' }).histShift).toBe(0);
    expect(red(mkState({ histShift: 0 }), { type: 'HIST_CAL_NEXT' }).histShift).toBe(0);
  });

  it('HIST_FILTER', () => {
    expect(red(mkState(), { type: 'HIST_FILTER', filter: 'flare' }).histFilter).toBe('flare');
  });

  it('TRENDS_SET: зміна періоду скидає sel', () => {
    const st = mkState({ trends: { period: 30, mode: 'chart', sym: 'fatigue', groupId: null, sel: 5 } });
    const next = red(st, { type: 'TRENDS_SET', patch: { period: 7 } });
    expect(next.trends).toEqual({ period: 7, mode: 'chart', sym: 'fatigue', groupId: null, sel: null });
  });

  it('TRENDS_SET: mode/sel не чіпають решту', () => {
    const st = mkState({ trends: { period: 30, mode: 'chart', sym: 'fatigue', groupId: null, sel: 5 } });
    expect(red(st, { type: 'TRENDS_SET', patch: { mode: 'table' } }).trends.sel).toBe(5);
    expect(red(st, { type: 'TRENDS_SET', patch: { sel: 2 } }).trends.sel).toBe(2);
  });

  it('REPORT_SET патчить report', () => {
    const st = mkState();
    const next = red(st, { type: 'REPORT_SET', patch: { step: 2, name: 'Оля' } });
    expect(next.report.step).toBe(2);
    expect(next.report.name).toBe('Оля');
    expect(next.report.syms).toEqual(st.report.syms);
  });

  it('TOAST_SHOW / TOAST_HIDE; DIALOG_OPEN / DIALOG_CLOSE', () => {
    const st = mkState();
    const shown = red(st, { type: 'TOAST_SHOW', text: 'Демо' });
    expect(shown.toast).toBe('Демо');
    expect(red(deepFreeze(shown), { type: 'TOAST_HIDE' }).toast).toBeNull();
    const opened = red(st, { type: 'DIALOG_OPEN', dialog: { type: 'pdf' } });
    expect(opened.dialog).toEqual({ type: 'pdf' });
    expect(red(deepFreeze(opened), { type: 'DIALOG_CLOSE' }).dialog).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Персист (load/save, ключ nd_demo_v3)
// ---------------------------------------------------------------------------

describe('persist', () => {
  let store: Record<string, string>;

  beforeEach(() => {
    store = {};
    (globalThis as { localStorage?: unknown }).localStorage = {
      getItem: (k: string) => (k in store ? store[k] : null),
      setItem: (k: string, v: string) => { store[k] = String(v); },
      removeItem: (k: string) => { delete store[k]; },
      clear: () => { for (const k of Object.keys(store)) delete store[k]; }
    };
  });

  it('порожнє сховище → базовий стан без вигаданих health records', () => {
    const st = load(NOW);
    expect(st.view).toBe('ob');
    expect(st.dialog).toBeNull();
    expect(st.toast).toBeNull();
    expect(st.data).toEqual(emptyData());
  });

  it('save серіалізує все, крім dialog/toast/report identity; checkin зберігається', () => {
    const st = mkState({
      checkin: { date: TODAY, d: d0({ wb: 4 }), back: null },
      dialog: { type: 'pdf' },
      toast: 'Збережено'
    });
    const withIdentity = { ...st, report: { ...st.report, name: 'Олена', dob: '1990-02-03' } };
    expect(save(withIdentity)).toEqual({ ok: true });
    const raw = JSON.parse(store[STORAGE_KEY]);
    expect(raw.dialog).toBeUndefined();
    expect(raw.toast).toBeUndefined();
    expect(raw.checkin).toEqual({ date: TODAY, d: d0({ wb: 4 }), back: null });
    expect(raw.data.entries).toEqual({});
    expect(raw.report.name).toBe('');
    expect(raw.report.dob).toBe('');
  });

  it('load мержить збережене поверх базового і скидає dialog/toast', () => {
    const savedState = mkState({
      tab: 'trends',
      checkin: { date: TODAY, d: d0({ wb: 4 }), back: null },
      dialog: { type: 'pdf' },
      toast: 'x'
    });
    save(savedState);
    const st = load(NOW);
    expect(st.tab).toBe('trends');
    expect(st.checkin).toEqual({ date: TODAY, d: d0({ wb: 4 }), back: null });
    expect(st.dialog).toBeNull();
    expect(st.toast).toBeNull();
    expect(st.data).toEqual(savedState.data);
  });

  for (const desk of [true, false]) {
    it(`legacy desk:${desk} ігнорується без втрати інших даних`, () => {
      const savedState = { ...mkState({ tab: 'report' }), desk };
      store[STORAGE_KEY] = JSON.stringify(savedState);
      const st = load(NOW);
      expect('desk' in st).toBe(false);
      expect(st.tab).toBe('report');
      expect(st.data).toEqual(savedState.data);
      save(st);
      expect(JSON.parse(store[STORAGE_KEY]).desk).toBeUndefined();
    });
  }

  it('legacy reminder keys are ignored, obStep 5 returns to cycle, and v4 health data stays intact', () => {
    const healthEntry = done({
      sym: { fatigue: { int: 4 } },
      absent: ['mood'],
      ctx: { ...ctx0, sleepH: 7, activity: false, heat: false }
    });
    store[STORAGE_KEY] = JSON.stringify({
      view: 'ob',
      obStep: 5,
      obSyms: ['fatigue'],
      obCycle: true,
      obRemOn: true,
      obTime: '08:00',
      data: {
        ...mkData({
          entries: { [YEST]: healthEntry },
          groups: [{ id: 'g1', name: 'Група', archived: false }],
          symptomGroupIds: { fatigue: ['g1'] }
        }),
        remOn: true,
        remTime: '08:00'
      }
    });

    const state = load(NOW);
    expect(state.obStep).toBe(4);
    expect(state.obSyms).toEqual(['fatigue']);
    expect(state.obCycle).toBe(true);
    expect(state.data.groups).toEqual([{ id: 'g1', name: 'Група', archived: false }]);
    expect(state.data.symptomGroupIds).toEqual({ fatigue: ['g1'] });
    const entry = state.data.entries[YEST];
    expect(entry.status === 'done' && entry.sym.fatigue.int).toBe(4);
    expect(entry.status === 'done' && entry.absent).toEqual(['mood']);
    expect(entry.status === 'done' && entry.ctx).toMatchObject({ sleepH: 7, activity: false, heat: false });
    expect('remOn' in state.data).toBe(false);
    expect('remTime' in state.data).toBe(false);
    expect('obRemOn' in state).toBe(false);
    expect('obTime' in state).toBe(false);

    save(state);
    const persisted = JSON.parse(store[STORAGE_KEY]);
    expect(persisted.data.remOn).toBeUndefined();
    expect(persisted.data.remTime).toBeUndefined();
    expect(persisted.obRemOn).toBeUndefined();
    expect(persisted.obTime).toBeUndefined();
  });

  it('битий JSON → базовий стан', () => {
    store[STORAGE_KEY] = '{не json';
    const st = load(NOW);
    expect(st.view).toBe('ob');
    expect(st.data).toEqual(emptyData());
  });

  it('v3→v4 preserves entries/drafts/cycle/custom but never infers per-symptom absence', () => {
    store[STORAGE_KEY] = JSON.stringify({
      view: 'app',
      data: {
        entries: {
          [YEST]: { status: 'done', wb: 6, sym: { fatigue: { int: 3 } }, confirmed: true, noSymptoms: false, ctx: { stress: null, sleepQ: null, sleepH: 7, activity: false, actType: '', heat: false, extras: [] }, note: '', flare: null, filledLater: false },
          [TODAY]: { status: 'draft', d: { wb: 5, wbSkip: false, sel: ['mood'], sym: { mood: { int: 2 } }, ctx: { stress: null, sleepQ: null, sleepH: 7, activity: false, actType: '', heat: false, extras: [] }, note: 'draft', flare: null, confirmed: false, noSymptoms: false, step: 3 } }
        },
        cycleStarts: ['2026-07-01'], active: ['fatigue', 'mood'], archived: ['armWeak'],
        custom: [{ id: 'c-old', name: 'Старий', type: 'bool' }], cycleOn: true, remOn: true, remTime: '20:00', lock: false
      }
    });
    const state = load(NOW);
    expect(state.data.schemaVersion).toBe(4);
    expect(state.data.cycleStarts).toEqual(['2026-07-01']);
    expect(state.data.custom).toHaveLength(1);
    expect(state.data.active).toEqual(['fatigue', 'mood']);
    expect('remOn' in state.data).toBe(false);
    expect('remTime' in state.data).toBe(false);
    const old = state.data.entries[YEST];
    expect(old.status === 'done' && old.absent).toEqual([]);
    expect(old.status === 'done' && old.sym.fatigue.int).toBe(3);
    const draft = state.data.entries[TODAY];
    expect(draft.status === 'draft' && draft.d.absent).toEqual([]);
    expect(draft.status === 'draft' && draft.d.note).toBe('draft');
    expect(draft.status === 'draft' && draft.d.ctx).toMatchObject({ sleepH: null, activity: null, heat: null });
  });

  it('v3→v4 preserves legacy noSymptoms as a day-level statement for done entries and drafts', () => {
    store[STORAGE_KEY] = JSON.stringify({
      view: 'app',
      data: {
        entries: {
          [YEST]: { status: 'done', sym: {}, noSymptoms: true, ctx: {}, note: '', flare: null },
          [TODAY]: { status: 'draft', d: { sel: [], sym: {}, noSymptoms: true, ctx: {}, step: 6 } }
        }
      }
    });
    const state = load(NOW);
    const old = state.data.entries[YEST];
    const draft = state.data.entries[TODAY];
    expect(old.status === 'done' && old.legacyNoSymptoms).toBe(true);
    expect(old.status === 'done' && old.absent).toEqual([]);
    expect(draft.status === 'draft' && draft.d.legacyNoSymptoms).toBe(true);
    expect(draft.status === 'draft' && draft.d.absent).toEqual([]);
  });

  it('v4 load cleans dangling/archived draft filters and dangling flare labels without deleting entries', () => {
    const raw = mkState({
      newGroupIds: ['archived', 'missing'],
      checkin: { date: TODAY, d: d0({ groupId: 'missing', flare: { isNew: false, dur24: false, temp: false, note: '', groupIds: ['active', 'missing'] } }), back: null },
      data: mkData({
        groups: [{ id: 'active', name: 'Active', archived: false }, { id: 'archived', name: 'Archived', archived: true }],
        entries: { [TODAY]: { status: 'draft', d: d0({ groupId: 'archived', flare: { isNew: false, dur24: false, temp: false, note: '', groupIds: ['archived', 'missing'] } }) } }
      })
    });
    store[STORAGE_KEY] = JSON.stringify(raw);
    const state = load(NOW);
    const entry = state.data.entries[TODAY];
    expect(entry.status === 'draft' && entry.d.groupId).toBeNull();
    expect(entry.status === 'draft' && entry.d.flare?.groupIds).toEqual(['archived']);
    expect(state.checkin?.d.groupId).toBeNull();
    expect(state.checkin?.d.flare?.groupIds).toEqual(['active']);
    expect(state.newGroupIds).toEqual([]);
  });

  it('save reports storage failure instead of returning false success', () => {
    (globalThis as { localStorage?: unknown }).localStorage = {
      getItem: () => null,
      setItem: () => { throw new DOMException('quota', 'QuotaExceededError'); }
    };
    expect(save(mkState())).toEqual({ ok: false, error: 'unavailable' });
  });
});
