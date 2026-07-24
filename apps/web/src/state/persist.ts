import type {
  AppData,
  AppState,
  CheckinDraft,
  Ctx,
  DoneEntry,
  Entry,
  Flare,
  Period,
  ReportState,
  TrackingGroup
} from '../lib/types';
import { initDraft, normalizeDraft } from '../lib/checkin';
import {
  UNGROUPED_ID,
  cleanSymptomGroupIds,
  normalizeReportSelection,
  reportableSymptomIds,
  uniqueIds,
  visibleGroups
} from '../lib/groups';

export const STORAGE_KEY = 'nd_demo_v3';
export const SCHEMA_VERSION = 4 as const;

type UnknownRecord = Record<string, unknown>;

function record(value: unknown): UnknownRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as UnknownRecord : {};
}

function strings(value: unknown): string[] {
  return uniqueIds(Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []);
}

function nullableNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function migrateCtx(value: unknown, legacy: boolean): Ctx {
  const raw = record(value);
  const sleepH = nullableNumber(raw.sleepH);
  return {
    stress: nullableNumber(raw.stress),
    sleepQ: nullableNumber(raw.sleepQ),
    // v3 initialized every untouched draft at 7h and had no explicit "not filled" state.
    sleepH: legacy && sleepH === 7 ? null : sleepH,
    activity: legacy && raw.activity === false ? null : (typeof raw.activity === 'boolean' ? raw.activity : null),
    actType: typeof raw.actType === 'string' ? raw.actType : '',
    heat: legacy && raw.heat === false ? null : (typeof raw.heat === 'boolean' ? raw.heat : null),
    extras: strings(raw.extras)
  };
}

function migrateDraft(value: unknown, legacy: boolean): CheckinDraft {
  const raw = record(value);
  const base = initDraft();
  const draft: CheckinDraft = {
    ...base,
    wb: nullableNumber(raw.wb),
    wbSkip: raw.wbSkip === true,
    sel: strings(raw.sel),
    sym: record(raw.sym) as CheckinDraft['sym'],
    absent: legacy ? [] : strings(raw.absent),
    groupId: typeof raw.groupId === 'string' ? raw.groupId : null,
    ctx: migrateCtx(raw.ctx, legacy),
    note: typeof raw.note === 'string' ? raw.note : '',
    flare: raw.flare && typeof raw.flare === 'object' ? raw.flare as CheckinDraft['flare'] : null,
    confirmed: raw.confirmed === true,
    noSymptoms: raw.noSymptoms === true,
    step: typeof raw.step === 'number' ? raw.step : 1,
    symIdx: typeof raw.symIdx === 'number' ? raw.symIdx : 0,
    ctxMore: raw.ctxMore === true
  };
  if ((legacy && raw.noSymptoms === true) || raw.legacyNoSymptoms === true) draft.legacyNoSymptoms = true;
  return normalizeDraft(draft);
}

function migrateEntry(value: unknown, legacy: boolean): Entry | null {
  const raw = record(value);
  if (raw.status === 'draft') return { status: 'draft', d: migrateDraft(raw.d, legacy) };
  if (raw.status !== 'done') return null;
  const sym = record(raw.sym) as DoneEntry['sym'];
  const absent = (legacy ? [] : strings(raw.absent)).filter((id) => !Object.prototype.hasOwnProperty.call(sym, id));
  const entry: DoneEntry = {
    status: 'done',
    wb: nullableNumber(raw.wb),
    sym,
    absent,
    ctx: migrateCtx(raw.ctx, legacy),
    note: typeof raw.note === 'string' ? raw.note : '',
    flare: raw.flare && typeof raw.flare === 'object' ? raw.flare as DoneEntry['flare'] : null,
    noSymptoms: raw.noSymptoms === true,
    filledLater: raw.filledLater === true
  };
  if (typeof raw.confirmed === 'boolean') entry.confirmed = raw.confirmed;
  if (legacy && entry.noSymptoms) entry.legacyNoSymptoms = true;
  else if (raw.legacyNoSymptoms === true) entry.legacyNoSymptoms = true;
  return entry;
}

function migrateGroups(value: unknown): TrackingGroup[] {
  const seen = new Set<string>();
  return (Array.isArray(value) ? value : []).flatMap((item) => {
    const raw = record(item);
    const id = typeof raw.id === 'string' ? raw.id : '';
    const name = typeof raw.name === 'string' ? raw.name.trim() : '';
    if (!id || !name || [...name].length > 60 || seen.has(id)) return [];
    seen.add(id);
    return [{ id, name, archived: raw.archived === true }];
  });
}

function cleanFlareGroupIds(flare: Flare | null, valid: Set<string>): Flare | null {
  if (!flare || flare.groupIds === undefined) return flare;
  return { ...flare, groupIds: uniqueIds(Array.isArray(flare.groupIds) ? flare.groupIds : []).filter((id) => valid.has(id)) };
}

function cleanEntryGroupReferences(
  entry: Entry,
  visible: Set<string>,
  valid: Set<string>
): Entry {
  if (entry.status === 'done') return { ...entry, flare: cleanFlareGroupIds(entry.flare, valid) };
  return {
    ...entry,
    d: {
      ...entry.d,
      groupId: entry.d.groupId && visible.has(entry.d.groupId) ? entry.d.groupId : null,
      flare: cleanFlareGroupIds(entry.d.flare, valid)
    }
  };
}

export function emptyData(): AppData {
  return {
    schemaVersion: SCHEMA_VERSION,
    entries: {},
    cycleStarts: [],
    active: [],
    archived: [],
    custom: [],
    groups: [],
    symptomGroupIds: {},
    cycleOn: false,
    lock: false
  };
}

export function baseState(): Omit<AppState, 'data'> {
  return {
    view: 'ob', obStep: 0, obSyms: [], obCycle: false, safetyMore: false,
    tab: 'today', sub: null,
    checkin: null, selDay: null, histShift: 0, histFilter: 'all', historyGroupId: null,
    catQ: '', catFrom: null, catalogGroupId: null, newName: '', newType: 'bool', newGroupIds: [],
    groupName: '', groupError: '', editingGroupId: null,
    trends: { period: 30, mode: 'chart', sym: '', groupId: null, sel: null }, crisisAns: '',
    report: {
      step: 1, period: 30, groupIds: [], syms: [], includeGroupNames: false,
      ctx: true, cycle: false, notes: false, flares: true, name: '', dob: ''
    },
    dialog: null,
    toast: null
  };
}

function period(value: unknown): Period {
  return value === 7 || value === 90 ? value : 30;
}

function validFilter(value: unknown, data: AppData): string | null {
  if (value === UNGROUPED_ID) return value;
  return typeof value === 'string' && visibleGroups(data).some((group) => group.id === value) ? value : null;
}

export function migrateState(rawValue: unknown, now: Date, resetIdentity = true): AppState {
  const raw = record(rawValue);
  const rawData = record(raw.data);
  const legacy = rawData.schemaVersion !== SCHEMA_VERSION;
  const groups = migrateGroups(rawData.groups);
  const entries: Record<string, Entry> = {};
  Object.entries(record(rawData.entries)).forEach(([iso, value]) => {
    const entry = migrateEntry(value, legacy);
    if (entry) entries[iso] = entry;
  });
  const validGroupSet = new Set(groups.map((group) => group.id));
  const visibleGroupSet = new Set(groups.filter((group) => !group.archived).map((group) => group.id));
  Object.entries(entries).forEach(([iso, entry]) => {
    entries[iso] = cleanEntryGroupReferences(entry, visibleGroupSet, validGroupSet);
  });
  const data: AppData = {
    ...emptyData(),
    entries,
    cycleStarts: strings(rawData.cycleStarts).sort(),
    active: strings(rawData.active),
    archived: strings(rawData.archived),
    custom: Array.isArray(rawData.custom) ? rawData.custom as AppData['custom'] : [],
    groups,
    symptomGroupIds: cleanSymptomGroupIds(record(rawData.symptomGroupIds) as Record<string, string[]>, groups),
    cycleOn: rawData.cycleOn === true,
    lock: rawData.lock === true
  };

  const base = baseState();
  const rawTrends = record(raw.trends);
  const rawReport = record(raw.report);
  const reportPeriod = period(rawReport.period);
  const groupIds = strings(rawReport.groupIds).filter((id) => validFilter(id, data) !== null);
  const available = reportableSymptomIds(data, reportPeriod, now, groupIds);
  const report: ReportState = {
    ...base.report,
    step: rawReport.step === 2 || rawReport.step === 3 ? rawReport.step : 1,
    period: reportPeriod,
    groupIds,
    syms: normalizeReportSelection(strings(rawReport.syms), available),
    includeGroupNames: rawReport.includeGroupNames === true,
    ctx: rawReport.ctx !== false,
    cycle: rawReport.cycle === true,
    notes: rawReport.notes === true,
    flares: rawReport.flares !== false,
    name: resetIdentity ? '' : (typeof rawReport.name === 'string' ? rawReport.name : ''),
    dob: resetIdentity ? '' : (typeof rawReport.dob === 'string' ? rawReport.dob : '')
  };

  const state: AppState = {
    ...base,
    view: raw.view === 'app' ? 'app' : 'ob',
    obStep: typeof raw.obStep === 'number' && Number.isFinite(raw.obStep)
      ? Math.max(0, Math.min(4, Math.trunc(raw.obStep)))
      : 0,
    obSyms: Array.isArray(raw.obSyms) ? strings(raw.obSyms) : [],
    obCycle: raw.obCycle === true,
    safetyMore: raw.safetyMore === true,
    tab: ['today', 'history', 'trends', 'report', 'set'].includes(String(raw.tab)) ? raw.tab as AppState['tab'] : 'today',
    sub: ['checkin', 'day', 'sym', 'catalog', 'groups', 'privacy', 'safety', 'crisis'].includes(String(raw.sub)) ? raw.sub as AppState['sub'] : null,
    checkin: raw.checkin ? {
      date: typeof record(raw.checkin).date === 'string' ? record(raw.checkin).date as string : '',
      d: migrateDraft(record(raw.checkin).d, legacy),
      back: record(raw.checkin).back === 'day' ? 'day' : null
    } : null,
    selDay: typeof raw.selDay === 'string' ? raw.selDay : null,
    histShift: typeof raw.histShift === 'number' ? Math.min(0, raw.histShift) : 0,
    histFilter: ['all', 'flare', 'menses', 'notes', 'draft'].includes(String(raw.histFilter)) ? raw.histFilter as AppState['histFilter'] : 'all',
    historyGroupId: validFilter(raw.historyGroupId, data),
    catQ: typeof raw.catQ === 'string' ? raw.catQ : '',
    catFrom: raw.catFrom === 'checkin' || raw.catFrom === 'set' ? raw.catFrom : null,
    catalogGroupId: validFilter(raw.catalogGroupId, data),
    newName: typeof raw.newName === 'string' ? raw.newName : '',
    newType: raw.newType === 'scale' ? 'scale' : 'bool',
    newGroupIds: strings(raw.newGroupIds).filter((id) => visibleGroupSet.has(id)),
    groupName: '', groupError: '', editingGroupId: null,
    trends: {
      period: period(rawTrends.period),
      mode: rawTrends.mode === 'table' ? 'table' : 'chart',
      sym: typeof rawTrends.sym === 'string' ? rawTrends.sym : '',
      groupId: validFilter(rawTrends.groupId, data),
      sel: typeof rawTrends.sel === 'number' ? rawTrends.sel : null
    },
    crisisAns: raw.crisisAns === 'yes' || raw.crisisAns === 'no' ? raw.crisisAns : '',
    report,
    dialog: null,
    toast: null,
    data
  };

  if (state.checkin) {
    state.checkin = {
      ...state.checkin,
      d: {
        ...state.checkin.d,
        groupId: state.checkin.d.groupId && visibleGroupSet.has(state.checkin.d.groupId) ? state.checkin.d.groupId : null,
        flare: cleanFlareGroupIds(state.checkin.d.flare, validGroupSet)
      }
    };
  }
  return state;
}

export function load(now: Date): AppState {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
    return migrateState(raw, now, true);
  } catch {
    return migrateState(null, now, true);
  }
}

export interface SaveResult {
  ok: boolean;
  error?: 'unavailable';
}

export function persistedState(state: AppState): Record<string, unknown> {
  const stored: Record<string, unknown> = {
    ...state,
    report: { ...state.report, name: '', dob: '' }
  };
  delete stored.dialog;
  delete stored.toast;
  return stored;
}

export function save(state: AppState): SaveResult {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(persistedState(state)));
    return { ok: true };
  } catch {
    return { ok: false, error: 'unavailable' };
  }
}

export function parseStateJson(json: string, now: Date): AppState {
  return migrateState(JSON.parse(json), now, false);
}
