// Система типів ядра. Архівний прототип docs/prototype/nd-v2.dc.html лишається
// історичною довідкою до походження частини моделі, але не чинним контрактом.

// ---------------------------------------------------------------------------
// Симптоми
// ---------------------------------------------------------------------------

export type SymType = 'bool' | 'scale';

export interface SymptomExtra {
  label: string;
  opts: string[];
  multi: boolean;
}

export interface SymptomDef {
  id: string;
  name: string;
  type: SymType;
  side?: string[];
  extra?: SymptomExtra;
  episodes?: boolean;
  impact?: boolean;
  /** Лише у власних (custom) симптомів, доданих із каталогу або вручну. */
  cat?: string;
}

/** Значення симптому в записі дня (e.sym[id]) або в чернетці чек-іна (d.sym[id]). */
export interface SymValue {
  int?: number | null;
  side?: string | null;
  extra?: string[];
  ep?: number;
  impact?: string | null;
  comment?: string;
  /** Лише в чернетці чек-іна: розгорнуто блок «більше деталей». У done-записі видаляється (fin, рядок 1189). */
  more?: boolean;
}

// ---------------------------------------------------------------------------
// Контекст дня, загострення, записи
// ---------------------------------------------------------------------------

export interface Ctx {
  stress: number | null;
  sleepQ: number | null;
  sleepH: number | null;
  activity: boolean | null;
  actType: string;
  heat: boolean | null;
  extras: string[];
}

export interface Flare {
  isNew: boolean;
  dur24: boolean;
  temp: boolean;
  note: string;
  /** Optional organizational labels only; never a causal or diagnostic link. */
  groupIds?: string[];
}

export interface DoneEntry {
  status: 'done';
  wb: number | null;
  sym: Record<string, SymValue>;
  /** Symptom ids explicitly confirmed absent for this exact observation. */
  absent: string[];
  /** v3 compatibility metadata only. Never derive per-symptom absence from it. */
  confirmed?: boolean;
  ctx: Ctx;
  note: string;
  flare: Flare | null;
  noSymptoms: boolean;
  /** v3 noSymptoms had no exact tracked-id snapshot. */
  legacyNoSymptoms?: boolean;
  filledLater: boolean;
}

export interface DraftEntry {
  status: 'draft';
  d: CheckinDraft;
}

export type Entry = DoneEntry | DraftEntry;

// ---------------------------------------------------------------------------
// Дані застосунку (state.data — genDemo, рядок 1029)
// ---------------------------------------------------------------------------

export interface TrackingGroup {
  id: string;
  name: string;
  archived: boolean;
}

export interface AppData {
  schemaVersion: 4;
  /** ISO-дата (YYYY-MM-DD) → запис. */
  entries: Record<string, Entry>;
  /** ISO-дати позначених початків циклу, відсортовані. */
  cycleStarts: string[];
  /** Активні id симптомів у порядку відображення. */
  active: string[];
  archived: string[];
  custom: SymptomDef[];
  /** Manual many-to-many organization. Symptom values remain stored once by id. */
  groups: TrackingGroup[];
  symptomGroupIds: Record<string, string[]>;
  cycleOn: boolean;
  lock: boolean;
}

// ---------------------------------------------------------------------------
// Чек-ін (startCheckin: 1164–1176; демо-чернетка: 1014)
// ---------------------------------------------------------------------------

export interface CheckinDraft {
  wb: number | null;
  wbSkip: boolean;
  sel: string[];
  sym: Record<string, SymValue>;
  /** Explicitly absent symptom ids; present ids always win during finalization. */
  absent: string[];
  /** Current organizational filter; null means all active symptoms. */
  groupId: string | null;
  ctx: Ctx;
  note: string;
  flare: Flare | null;
  confirmed: boolean;
  noSymptoms: boolean;
  /** Migrated day-level statement whose historical symptom snapshot is unknown. */
  legacyNoSymptoms?: boolean;
  /** 1..6 (3 — покроково по симптомах; 4/5 — опційні «сателіти»). */
  step: number;
  symIdx?: number;
  ctxMore?: boolean;
}

export interface CheckinState {
  date: string;
  d: CheckinDraft;
  back: 'day' | null;
}

// ---------------------------------------------------------------------------
// Стан вкладок
// ---------------------------------------------------------------------------

export type View = 'ob' | 'app';
export type Tab = 'today' | 'history' | 'trends' | 'report' | 'set';
export type Sub =
  | 'checkin'
  | 'day'
  | 'sym'
  | 'catalog'
  | 'groups'
  | 'privacy'
  | 'safety'
  | 'crisis'
  | null;

export type HistFilter = 'all' | 'flare' | 'menses' | 'notes' | 'draft';

export type Period = 7 | 30 | 90;
export type TrendsMode = 'chart' | 'table';

export interface TrendsState {
  period: Period;
  mode: TrendsMode;
  sym: string;
  groupId: string | null;
  /** Індекс вибраного дня на графіку (0..period-1) або null. */
  sel: number | null;
}

export interface ReportState {
  step: 1 | 2 | 3;
  period: Period;
  /** Empty means all groups. */
  groupIds: string[];
  syms: string[];
  includeGroupNames: boolean;
  ctx: boolean;
  cycle: boolean;
  notes: boolean;
  flares: boolean;
  name: string;
  dob: string;
}

export type CatFrom = 'set' | 'checkin' | null;
export type CrisisAns = '' | 'yes' | 'no';

// ---------------------------------------------------------------------------
// Діалоги (_vDlg: 1513–1568; відкриття: 1158, 1280, 1508, 1509, 1717)
// ---------------------------------------------------------------------------

export type MensesSel = 'today' | 'yest' | 'custom';

export type DialogState =
  | { type: 'menses'; sel: MensesSel; custom: string; dup: boolean; invalid?: boolean }
  | { type: 'delEntry'; iso: string }
  | { type: 'delGroup'; id: string }
  | { type: 'discardEdit' }
  | { type: 'delData' }
  | { type: 'pdf' }
  | { type: 'flare'; iso: string; f: Flare; had: boolean };

export type ToastState = string | null;

// ---------------------------------------------------------------------------
// Повний стан застосунку (base-state: 967–971 + dialog/toast/data)
// ---------------------------------------------------------------------------

export interface AppState {
  view: View;
  /** 0..4 */
  obStep: number;
  obSyms: string[] | null;
  obCycle: boolean;
  safetyMore: boolean;
  tab: Tab;
  sub: Sub;
  checkin: CheckinState | null;
  selDay: string | null;
  /** 0 або відʼємний зсув місяця календаря історії. */
  histShift: number;
  histFilter: HistFilter;
  historyGroupId: string | null;
  catQ: string;
  catFrom: CatFrom;
  catalogGroupId: string | null;
  newName: string;
  newType: SymType;
  newGroupIds: string[];
  groupName: string;
  groupError: string;
  editingGroupId: string | null;
  trends: TrendsState;
  crisisAns: CrisisAns;
  report: ReportState;
  dialog: DialogState | null;
  toast: ToastState;
  data: AppData;
}

/** Згруповані поля стану вкладки «Історія» (у прототипі лежать пласко в state). */
export type HistState = Pick<AppState, 'histShift' | 'histFilter' | 'selDay'>;
