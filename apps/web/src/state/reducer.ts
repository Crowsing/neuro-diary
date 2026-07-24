// Чистий reducer чинного стану застосунку. Історичну відповідність частині
// обробників архівного прототипу задокументовано в ACTIONS.md.
// Побічних ефектів немає: «сьогодні» та генерація id інʼєктуються через Env.

import type { AppData, AppState, CheckinDraft, Entry, ReportState, Sub, Tab } from '../lib/types';
import type { Action } from './actions';
import { isoOff, fmtShort, isIsoDate } from '../lib/dates';
import { resolveDraft, finalizeEntry, missingIntensityIds, noSymptomsEntry, normalizeDraft } from '../lib/checkin';
import { baseState, emptyData } from './persist';
import {
  UNGROUPED_ID,
  cleanSymptomGroupIds,
  normalizeReportSelection,
  reportableSymptomIds,
  uniqueIds,
  validateGroupName,
  visibleGroups
} from '../lib/groups';
import { makeSymDef } from '../lib/utils';

/** Оточення reducer-а: фіксоване «сьогодні» і генератор id ('c'+Date.now() у прототипі). */
export interface Env {
  now: Date;
  newId: () => string;
}

/** Заміна/додавання одного запису дня (immutable). */
function withEntry(state: AppState, iso: string, e: Entry): AppState {
  return { ...state, data: { ...state.data, entries: { ...state.data.entries, [iso]: e } } };
}

/**
 * udr (рядки 1177–1183): атомарний патч чернетки чек-іну.
 * Оновлює checkin.d І дзеркальну draft-копію в data.entries[date] —
 * але ЛИШЕ якщо запис за цю дату не done (інваріант AC2, draft persistence).
 */
function udr(state: AppState, patch: Partial<CheckinDraft>): AppState {
  const c = state.checkin;
  if (!c) return state;
  const d = normalizeDraft({ ...c.d, ...patch });
  const ent = state.data.entries[c.date];
  let data = state.data;
  if (!ent || ent.status !== 'done') {
    data = { ...data, entries: { ...data.entries, [c.date]: { status: 'draft', d } } };
  }
  return { ...state, checkin: { ...c, d }, data };
}

function normalizeReport(report: ReportState, data: AppData, now: Date): ReportState {
  const visible = new Set(visibleGroups(data).map((group) => group.id));
  const groupIds = uniqueIds(report.groupIds || []).filter((id) => id === UNGROUPED_ID || visible.has(id));
  const available = reportableSymptomIds(data, report.period, now, groupIds);
  return { ...report, groupIds, syms: normalizeReportSelection(report.syms || [], available) };
}

function withNormalizedReport(state: AppState, data: AppData, now: Date): AppState {
  return { ...state, data, report: normalizeReport(state.report, data, now) };
}

function stripGroupFromFlare(flare: CheckinDraft['flare'], groupId: string) {
  if (!flare?.groupIds?.includes(groupId)) return flare;
  return { ...flare, groupIds: flare.groupIds.filter((id) => id !== groupId) };
}

/** Clear invisible draft filters; on delete also remove organizational flare labels. */
function cleanEntryGroupReferences(entries: AppData['entries'], groupId: string, deleted: boolean) {
  return Object.fromEntries(Object.entries(entries).map(([iso, entry]) => {
    if (entry.status === 'done') {
      if (!deleted) return [iso, entry];
      const flare = stripGroupFromFlare(entry.flare, groupId);
      return [iso, flare === entry.flare ? entry : { ...entry, flare }];
    }
    const groupFilter = entry.d.groupId === groupId ? null : entry.d.groupId;
    const flare = deleted ? stripGroupFromFlare(entry.d.flare, groupId) : entry.d.flare;
    if (groupFilter === entry.d.groupId && flare === entry.d.flare) return [iso, entry];
    return [iso, {
      ...entry,
      d: {
        ...entry.d,
        groupId: groupFilter,
        flare
      }
    }];
  }));
}

function cleanOpenCheckinGroupReferences(checkin: AppState['checkin'], groupId: string, deleted: boolean) {
  if (!checkin) return null;
  return {
    ...checkin,
    d: {
      ...checkin.d,
      groupId: checkin.d.groupId === groupId ? null : checkin.d.groupId,
      flare: deleted ? stripGroupFromFlare(checkin.d.flare, groupId) : checkin.d.flare
    }
  };
}

export const appReducer =
  (env: Env) =>
  (state: AppState, action: Action): AppState => {
    switch (action.type) {
      // -----------------------------------------------------------------
      // Навігація
      // -----------------------------------------------------------------
      case 'NAV_TAB': // goTab, рядок 1079
        return { ...state, tab: action.tab, sub: null, selDay: null, crisisAns: '' };

      case 'NAV_SUB': { // відкриття/закриття суб-екранів, див. ACTIONS.md
        if (action.selDay && action.selDay > isoOff(env.now, 0)) return state;
        const next = { ...state, sub: action.sub };
        if (action.tab !== undefined) next.tab = action.tab;
        if (action.selDay !== undefined) next.selDay = action.selDay;
        if (action.catFrom !== undefined) next.catFrom = action.catFrom;
        if (action.crisisAns !== undefined) next.crisisAns = action.crisisAns;
        if (action.safetyMore !== undefined) next.safetyMore = action.safetyMore;
        if (action.trendsSym !== undefined) {
          next.trends = { ...state.trends, sym: action.trendsSym, sel: null }; // рядок 1592
        }
        return next;
      }

      case 'CAT_BACK': // catBack, рядок 1281
        return state.catFrom === 'checkin'
          ? { ...state, sub: 'checkin', catQ: '' }
          : { ...state, sub: null, tab: 'set', catQ: '' };

      case 'CRISIS_BACK': // crBack, рядок 1304
        return state.checkin
          ? { ...state, sub: 'checkin', crisisAns: '' }
          : { ...state, sub: null, crisisAns: '' };

      case 'CRISIS_ANSWER': // crYes/crNo, рядок 1306
        return { ...state, crisisAns: action.ans };

      case 'SAFETY_MORE': // sfMoreOn, рядок 1301
        return { ...state, safetyMore: true };

      // -----------------------------------------------------------------
      // Онбординг
      // -----------------------------------------------------------------
      case 'OB_SET': // рядки 1121–1129
        return { ...state, ...action.patch };

      case 'OB_FINISH': { // finishOb, рядки 1080–1083
        const d = state.data;
        return {
          ...state,
          view: 'app',
          data: {
            ...d,
            active: uniqueIds(state.obSyms || []),
            cycleOn: state.obCycle
          }
        };
      }

      // -----------------------------------------------------------------
      // Сьогодні + чек-ін
      // -----------------------------------------------------------------
      case 'TODAY_NO_SYMPTOMS': { // todNoSym, рядки 1154–1155
        const t = isoOff(env.now, 0);
        const active = uniqueIds(state.data.active);
        if (!active.length) return state;
        return { ...withEntry(state, t, noSymptomsEntry(active)), toast: 'Записано: відстежуваних симптомів цього дня не було' };
      }

      case 'CHECKIN_START': { // startCheckin, рядки 1164–1175
        if (action.iso > isoOff(env.now, 0)) return state;
        const d = resolveDraft(action.iso, state.checkin, state.data.entries[action.iso]);
        return { ...state, sub: 'checkin', checkin: { date: action.iso, d, back: action.back || null } };
      }

      case 'CHECKIN_PATCH': // udr, рядки 1177–1183
        return udr(state, action.patch);

      case 'CHECKIN_SYM_PATCH': { // usym, рядок 1185
        const c = state.checkin;
        if (!c) return state;
        return udr(state, { sym: { ...c.d.sym, [action.id]: { ...(c.d.sym[action.id] || {}), ...action.patch } } });
      }

      case 'CHECKIN_CTX_PATCH': { // uctx, рядок 1186
        const c = state.checkin;
        if (!c) return state;
        return udr(state, { ctx: { ...c.d.ctx, ...action.patch } });
      }

      case 'CHECKIN_FINISH': { // fin, рядки 1187–1196
        const c = state.checkin;
        if (!c) return state;
        if (c.date > isoOff(env.now, 0)) return state;
        if (missingIntensityIds(c.d, makeSymDef(state.data.custom)).length) return state;
        const prev = state.data.entries[c.date];
        const e = finalizeEntry(c.d, c.date, prev, isoOff(env.now, 0));
        const nav = c.back === 'day'
          ? { sub: 'day' as Sub, tab: 'history' as Tab, selDay: c.date }
          : { sub: null, tab: 'today' as Tab, selDay: null };
        const saved = withEntry(state, c.date, e);
        return { ...withNormalizedReport(saved, saved.data, env.now), checkin: null, ...nav, toast: 'Збережено' };
      }

      case 'CHECKIN_EXIT': { // exitCheckin, рядки 1198–1206
        const c = state.checkin;
        if (!c) return state;
        const prev = state.data.entries[c.date];
        let data = state.data;
        if (action.saveDraft && (!prev || prev.status !== 'done')) {
          data = { ...data, entries: { ...data.entries, [c.date]: { status: 'draft', d: c.d } } };
        }
        const nav = c.back === 'day'
          ? { sub: 'day' as Sub, tab: 'history' as Tab, selDay: c.date }
          : { sub: null };
        const next: AppState = { ...state, data, checkin: null, ...nav };
        return action.saveDraft && (!prev || prev.status !== 'done') ? { ...next, toast: 'Чернетку збережено' } : next;
      }

      // -----------------------------------------------------------------
      // Діалоги
      // -----------------------------------------------------------------
      case 'DIALOG_OPEN': // рядки 1158, 1280, 1508, 1509, 1717
        return { ...state, dialog: action.dialog };

      case 'DIALOG_CLOSE': // close, рядок 1524
        return { ...state, dialog: null };

      case 'MENSES_SET': { // рядки 1528–1531
        const dl = state.dialog;
        if (!dl || dl.type !== 'menses') return state;
        if (action.custom !== undefined) {
          return { ...state, dialog: { ...dl, sel: 'custom', custom: action.custom, dup: false, invalid: false } };
        }
        if (action.sel) return { ...state, dialog: { ...dl, sel: action.sel, dup: false, invalid: false } };
        return state;
      }

      case 'MENSES_CONFIRM': { // mensConfirm, рядки 1534–1540
        const dl = state.dialog;
        if (!dl || dl.type !== 'menses') return state;
        const t = isoOff(env.now, 0);
        const yd = isoOff(env.now, -1);
        const date = dl.sel === 'today' ? t : (dl.sel === 'yest' ? yd : dl.custom);
        if (!date) return { ...state, dialog: { ...dl, invalid: true, dup: false } };
        if (!isIsoDate(date) || date > t) return { ...state, dialog: { ...dl, invalid: true, dup: false } };
        if ((state.data.cycleStarts || []).includes(date)) {
          return { ...state, dialog: { ...dl, dup: true } };
        }
        return {
          ...state,
          data: { ...state.data, cycleStarts: [...(state.data.cycleStarts || []), date].sort(), cycleOn: true },
          dialog: null,
          toast: 'Початок циклу позначено: ' + fmtShort(date)
        };
      }

      case 'MENSES_REMOVE': // dayDelMenses, рядок 1510
        return {
          ...state,
          data: { ...state.data, cycleStarts: state.data.cycleStarts.filter((x) => x !== action.iso) },
          toast: 'Позначку прибрано, день циклу перераховано'
        };

      case 'ENTRY_DELETE': { // delEC, рядок 1545
        const E = { ...state.data.entries };
        delete E[action.iso];
        const data = { ...state.data, entries: E };
        return { ...withNormalizedReport(state, data, env.now), dialog: null, toast: 'Запис видалено' };
      }

      case 'DATA_DELETE': {
        if (action.scope === 'cycle') {
          return { ...state, data: { ...state.data, cycleStarts: [] }, dialog: null, toast: 'Дані циклу видалено' };
        }
        if (action.scope === 'all') {
          return {
            ...baseState(),
            view: 'app',
            tab: 'set',
            data: emptyData(),
            dialog: null,
            toast: 'Усі дані видалено'
          };
        }
        return state;
      }

      case 'FLARE_DLG_TOGGLE': { // dfChips, рядок 1558
        const dl = state.dialog;
        if (!dl || dl.type !== 'flare') return state;
        return { ...state, dialog: { ...dl, f: { ...dl.f, [action.flag]: !dl.f[action.flag] } } };
      }

      case 'FLARE_DLG_NOTE': { // dfNoteOn, рядок 1560
        const dl = state.dialog;
        if (!dl || dl.type !== 'flare') return state;
        return { ...state, dialog: { ...dl, f: { ...dl.f, note: action.note } } };
      }

      case 'FLARE_DLG_GROUPS': {
        const dl = state.dialog;
        if (!dl || dl.type !== 'flare') return state;
        const valid = new Set(state.data.groups.map((group) => group.id));
        return { ...state, dialog: { ...dl, f: { ...dl.f, groupIds: uniqueIds(action.groupIds).filter((id) => valid.has(id)) } } };
      }

      case 'FLARE_SAVE': { // dfSave + put, рядки 1562–1564
        const dl = state.dialog;
        if (!dl || dl.type !== 'flare') return state;
        const e = state.data.entries[dl.iso];
        // У прототипі put() перевіряє лише наявність запису; діалог загострення
        // відкривається тільки для done-записів (dayCanFlare/dayHasFlare, 1507–1508).
        if (!e || e.status !== 'done') return { ...state, dialog: null, toast: 'Позначку збережено' };
        return { ...withEntry(state, dl.iso, { ...e, flare: { ...dl.f } }), dialog: null, toast: 'Позначку збережено' };
      }

      case 'FLARE_DELETE': { // dfDelOn + put, рядки 1562–1565
        const dl = state.dialog;
        if (!dl || dl.type !== 'flare') return state;
        const e = state.data.entries[dl.iso];
        if (!e || e.status !== 'done') return { ...state, dialog: null, toast: 'Позначку прибрано' };
        return { ...withEntry(state, dl.iso, { ...e, flare: null }), dialog: null, toast: 'Позначку прибрано' };
      }

      // -----------------------------------------------------------------
      // Каталог симптомів і налаштування
      // -----------------------------------------------------------------
      case 'CAT_MOVE': { // move, рядки 1270–1271
        const a = [...state.data.active];
        const i = action.index;
        const j = i + action.dir;
        if (i < 0 || i >= a.length || j < 0 || j >= a.length) return state;
        const tmp = a[i];
        a[i] = a[j];
        a[j] = tmp;
        return { ...state, data: { ...state.data, active: a } };
      }

      case 'CAT_ARCHIVE': // arch, рядок 1286
        return { ...withNormalizedReport({ ...state }, {
            ...state.data,
            active: state.data.active.filter((x) => x !== action.id),
            archived: uniqueIds([...state.data.archived, action.id])
          }, env.now), toast: 'Переміщено в архів — історія збережена' };

      case 'CAT_RESTORE': // restore, рядок 1290 (без тосту)
        return withNormalizedReport(state, {
            ...state.data,
            archived: state.data.archived.filter((x) => x !== action.id),
            active: uniqueIds([...state.data.active, action.id])
          }, env.now);

      case 'CAT_ADD_CUSTOM': { // addCustom, рядки 1294–1297
        const n = (state.newName || '').trim();
        if (!n) return { ...state, toast: 'Введіть назву симптому' };
        const id = env.newId();
        return {
          ...state,
          data: {
            ...state.data,
            custom: [...(state.data.custom || []), { id, name: n, cat: 'Власні', type: state.newType }],
            active: [...state.data.active, id],
            symptomGroupIds: state.newGroupIds.length
              ? { ...state.data.symptomGroupIds, [id]: uniqueIds(state.newGroupIds) }
              : state.data.symptomGroupIds
          },
          newName: '', newGroupIds: [],
          toast: 'Симптом додано'
        };
      }

      case 'CAT_ADD_FROM_LIB': { // addFromCat, рядки 1257–1261
        const id = env.newId();
        return {
          ...state,
          data: {
            ...state.data,
            custom: [...(state.data.custom || []), { id, name: action.name, cat: action.cat, type: 'bool' }],
            active: [...state.data.active, id],
            symptomGroupIds: state.catalogGroupId && state.catalogGroupId !== UNGROUPED_ID
              ? { ...state.data.symptomGroupIds, [id]: [state.catalogGroupId] }
              : state.data.symptomGroupIds
          },
          toast: 'Додано до активного списку'
        };
      }

      case 'CAT_SET': // рядки 1282, 1292, 1293
        return { ...state, ...action.patch };

      case 'GROUP_FORM_SET':
        return { ...state, groupName: action.name, groupError: '' };

      case 'GROUP_CREATE': {
        const error = validateGroupName(state.groupName, state.data.groups);
        if (error) return { ...state, groupError: error };
        const group = { id: 'g-' + env.newId() + '-' + state.data.groups.length, name: state.groupName.trim(), archived: false };
        return { ...state, data: { ...state.data, groups: [...state.data.groups, group] }, groupName: '', groupError: '' };
      }

      case 'GROUP_EDIT_START': {
        const group = state.data.groups.find((item) => item.id === action.id);
        return { ...state, editingGroupId: action.id, groupName: group?.name || '', groupError: '' };
      }

      case 'GROUP_RENAME': {
        if (!state.editingGroupId) return state;
        const error = validateGroupName(state.groupName, state.data.groups, state.editingGroupId);
        if (error) return { ...state, groupError: error };
        return {
          ...state,
          data: { ...state.data, groups: state.data.groups.map((group) => group.id === state.editingGroupId ? { ...group, name: state.groupName.trim() } : group) },
          editingGroupId: null, groupName: '', groupError: ''
        };
      }

      case 'GROUP_MOVE': {
        const groups = [...state.data.groups];
        const nextIndex = action.index + action.dir;
        if (action.index < 0 || action.index >= groups.length || nextIndex < 0 || nextIndex >= groups.length) return state;
        [groups[action.index], groups[nextIndex]] = [groups[nextIndex], groups[action.index]];
        return { ...state, data: { ...state.data, groups } };
      }

      case 'GROUP_ARCHIVE':
      case 'GROUP_RESTORE': {
        const archived = action.type === 'GROUP_ARCHIVE';
        let data = { ...state.data, groups: state.data.groups.map((group) => group.id === action.id ? { ...group, archived } : group) };
        if (archived) data = { ...data, entries: cleanEntryGroupReferences(data.entries, action.id, false) };
        const clear = archived ? action.id : '';
        return withNormalizedReport({
          ...state,
          checkin: archived ? cleanOpenCheckinGroupReferences(state.checkin, action.id, false) : state.checkin,
          newGroupIds: archived ? state.newGroupIds.filter((id) => id !== action.id) : state.newGroupIds,
          historyGroupId: state.historyGroupId === clear ? null : state.historyGroupId,
          catalogGroupId: state.catalogGroupId === clear ? null : state.catalogGroupId,
          trends: state.trends.groupId === clear ? { ...state.trends, groupId: null } : state.trends
        }, data, env.now);
      }

      case 'GROUP_DELETE': {
        const groups = state.data.groups.filter((group) => group.id !== action.id);
        const data = {
          ...state.data,
          groups,
          symptomGroupIds: cleanSymptomGroupIds(state.data.symptomGroupIds, groups),
          entries: cleanEntryGroupReferences(state.data.entries, action.id, true)
        };
        return {
          ...withNormalizedReport({
            ...state,
            checkin: cleanOpenCheckinGroupReferences(state.checkin, action.id, true),
            newGroupIds: state.newGroupIds.filter((id) => id !== action.id),
            editingGroupId: state.editingGroupId === action.id ? null : state.editingGroupId,
            historyGroupId: state.historyGroupId === action.id ? null : state.historyGroupId,
            catalogGroupId: state.catalogGroupId === action.id ? null : state.catalogGroupId,
            trends: state.trends.groupId === action.id ? { ...state.trends, groupId: null } : state.trends
          }, data, env.now),
          dialog: null,
          toast: 'Групу видалено. Записи симптомів збережено.'
        };
      }

      case 'SYMPTOM_GROUPS_SET': {
        const valid = new Set(state.data.groups.map((group) => group.id));
        const groupIds = uniqueIds(action.groupIds).filter((id) => valid.has(id));
        const symptomGroupIds = { ...state.data.symptomGroupIds };
        if (groupIds.length) symptomGroupIds[action.id] = groupIds;
        else delete symptomGroupIds[action.id];
        return withNormalizedReport(state, { ...state.data, symptomGroupIds }, env.now);
      }

      case 'HISTORY_GROUP_SET':
        return { ...state, historyGroupId: action.groupId };

      case 'DATA_PATCH': { // рядки 1274, 1299
        const next: AppState = { ...state, data: { ...state.data, ...action.patch } };
        if (action.patch.lock !== undefined) {
          // 1299: tset за СТАРИМ значенням D.lock — еквівалентно новому з інверсією.
          next.toast = action.patch.lock ? 'App lock увімкнено (демо)' : 'App lock вимкнено (демо)';
        }
        return next;
      }

      // -----------------------------------------------------------------
      // Історія, тренди, звіт
      // -----------------------------------------------------------------
      case 'HIST_CAL_PREV': // calPrev, рядок 1487
        return { ...state, histShift: state.histShift - 1 };

      case 'HIST_CAL_NEXT': // calNext, рядок 1488
        return { ...state, histShift: Math.min(0, state.histShift + 1) };

      case 'HIST_FILTER': // histChips, рядок 1490
        return { ...state, histFilter: action.filter };

      case 'TRENDS_SET': { // рядки 1580, 1625, 1631
        const trends = { ...state.trends, ...action.patch };
        if (action.patch.period !== undefined && !('sel' in action.patch)) trends.sel = null; // 1580
        return { ...state, trends };
      }

      case 'REPORT_SET': // setR, рядок 1685
        return { ...state, report: normalizeReport({ ...state.report, ...action.patch }, state.data, env.now) };

      // -----------------------------------------------------------------
      // Тост
      // -----------------------------------------------------------------
      case 'TOAST_SHOW': // tset, рядок 1005
        return { ...state, toast: action.text };

      case 'TOAST_HIDE':
        return { ...state, toast: null };
    }
  };
