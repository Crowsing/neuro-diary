// Типізовані action-и чинного стану застосунку. Архівне походження окремих
// обробників із docs/prototype/nd-v2.dc.html задокументоване в ACTIONS.md.

import type {
  AppData,
  AppState,
  CheckinDraft,
  CrisisAns,
  Ctx,
  DialogState,
  HistFilter,
  ReportState,
  Sub,
  SymValue,
  Tab,
  TrendsState
} from '../lib/types';
import type { FlareFlagKey } from '../constants/copy';

// ---------------------------------------------------------------------------
// Навігація
// ---------------------------------------------------------------------------

/** goTab (рядок 1079): tab + скидання sub/selDay/crisisAns. */
export interface NavTabAction { type: 'NAV_TAB'; tab: Tab; }

/**
 * Відкриття/закриття суб-екрана (рядки 1161, 1273, 1278–1279, 1298, 1300, 1302,
 * 1396, 1426, 1445, 1469, 1492, 1592, 1623, 1636, 1645).
 * Додаткові поля застосовуються лише якщо передані (undefined = не чіпати).
 * trendsSym → trends: {...trends, sym, sel:null} (рядок 1592).
 */
export interface NavSubAction {
  type: 'NAV_SUB';
  sub: Sub;
  tab?: Tab;
  selDay?: string | null;
  catFrom?: 'set' | 'checkin' | null;
  crisisAns?: CrisisAns;
  safetyMore?: boolean;
  trendsSym?: string;
}

/** catBack (рядок 1281): розгалуження за state.catFrom — усередині reducer-а. */
export interface CatBackAction { type: 'CAT_BACK'; }

/** crBack (рядок 1304): розгалуження за state.checkin — усередині reducer-а. */
export interface CrisisBackAction { type: 'CRISIS_BACK'; }

/** crYes/crNo (рядок 1306). */
export interface CrisisAnswerAction { type: 'CRISIS_ANSWER'; ans: CrisisAns; }

/** sfMoreOn (рядок 1301): safetyMore = true. */
export interface SafetyMoreAction { type: 'SAFETY_MORE'; }

// ---------------------------------------------------------------------------
// Онбординг
// ---------------------------------------------------------------------------

/** Патч ob-полів (рядки 1121–1129). obSyms-toggle обчислює екран: toggle(obSyms ?? SYM.map(id), id). */
export interface ObSetAction {
  type: 'OB_SET';
  patch: Partial<Pick<AppState, 'obStep' | 'obSyms' | 'obCycle'>>;
}

/** finishOb (рядки 1080–1083; виклики 1129, 1130). */
export interface ObFinishAction { type: 'OB_FINISH'; }

// ---------------------------------------------------------------------------
// Сьогодні + чек-ін
// ---------------------------------------------------------------------------

/** todNoSym (рядки 1154–1155): entries[сьогодні] = noSymptomsEntry() + тост. */
export interface TodayNoSymptomsAction { type: 'TODAY_NO_SYMPTOMS'; }

/** startCheckin (рядки 1164–1175; виклики 1152, 1157, 1506). */
export interface CheckinStartAction { type: 'CHECKIN_START'; iso: string; back?: 'day' | null; }

/**
 * udr (рядки 1177–1183): атомарний патч чернетки — оновлює і checkin.d,
 * і дзеркальну draft-копію в data.entries[date] (лише якщо запис не done).
 */
export interface CheckinPatchAction { type: 'CHECKIN_PATCH'; patch: Partial<CheckinDraft>; }

/** usym (рядок 1185): патч значення симптому в чернетці (через udr). */
export interface CheckinSymPatchAction { type: 'CHECKIN_SYM_PATCH'; id: string; patch: Partial<SymValue>; }

/** uctx (рядок 1186): патч контексту дня в чернетці (через udr). */
export interface CheckinCtxPatchAction { type: 'CHECKIN_CTX_PATCH'; patch: Partial<Ctx>; }

/** fin (рядки 1187–1196): finalizeEntry + навігація за back + тост «Збережено». */
export interface CheckinFinishAction { type: 'CHECKIN_FINISH'; }

/** exitCheckin (рядки 1198–1206): saveDraft → дзеркальна чернетка (якщо не done) + тост. */
export interface CheckinExitAction { type: 'CHECKIN_EXIT'; saveDraft: boolean; }

// ---------------------------------------------------------------------------
// Діалоги
// ---------------------------------------------------------------------------

/** Відкриття діалогу з готовим обʼєктом (рядки 1158, 1280, 1508, 1509, 1717). */
export interface DialogOpenAction { type: 'DIALOG_OPEN'; dialog: DialogState; }

/** Закриття без дії (close у _vDlg, рядок 1524). */
export interface DialogCloseAction { type: 'DIALOG_CLOSE'; }

/**
 * Діалог менструації (рядки 1528–1531): custom !== undefined → sel:'custom' + custom;
 * інакше sel. Обидва варіанти скидають dup:false.
 */
export interface MensesSetAction { type: 'MENSES_SET'; sel?: 'today' | 'yest'; custom?: string; }

/** mensConfirm (рядки 1534–1540): дубль дати → лише прапор dup:true. */
export interface MensesConfirmAction { type: 'MENSES_CONFIRM'; }

/** dayDelMenses (рядок 1510): прибрати позначку початку циклу. */
export interface MensesRemoveAction { type: 'MENSES_REMOVE'; iso: string; }

/** delEC (рядок 1545): видалити запис дня + закрити діалог + тост. */
export interface EntryDeleteAction { type: 'ENTRY_DELETE'; iso: string; }

export interface DataDeleteAction { type: 'DATA_DELETE'; scope: 'cycle' | 'all'; }

/** dfChips (рядок 1558): перемкнути прапор у діалозі загострення. */
export interface FlareDlgToggleAction { type: 'FLARE_DLG_TOGGLE'; flag: FlareFlagKey; }

/** dfNoteOn (рядок 1560). */
export interface FlareDlgNoteAction { type: 'FLARE_DLG_NOTE'; note: string; }
export interface FlareDlgGroupsAction { type: 'FLARE_DLG_GROUPS'; groupIds: string[]; }

/** dfSave (рядки 1562–1564): записати flare у done-запис + закрити + тост. */
export interface FlareSaveAction { type: 'FLARE_SAVE'; }

/** dfDelOn (рядок 1565): flare = null + закрити + тост. */
export interface FlareDeleteAction { type: 'FLARE_DELETE'; }

// ---------------------------------------------------------------------------
// Каталог симптомів і налаштування
// ---------------------------------------------------------------------------

/** move (рядки 1270–1271): перестановка в active; вихід за межі — без змін. */
export interface CatMoveAction { type: 'CAT_MOVE'; index: number; dir: -1 | 1; }

/** arch (рядок 1286). */
export interface CatArchiveAction { type: 'CAT_ARCHIVE'; id: string; }

/** restore (рядок 1290) — без тосту. */
export interface CatRestoreAction { type: 'CAT_RESTORE'; id: string; }

/** addCustom (рядки 1294–1297): порожня назва → лише тост «Введіть назву симптому». */
export interface CatAddCustomAction { type: 'CAT_ADD_CUSTOM'; }

/**
 * addFromCat (рядки 1257–1261): додати з бібліотеки (type:'bool').
 * Гілку «вже додано» (рядок 1265) екран обробляє сам: TOAST_SHOW «Уже у вашому списку».
 */
export interface CatAddFromLibAction { type: 'CAT_ADD_FROM_LIB'; name: string; cat: string; }

/** Привʼязки інпутів каталогу (рядки 1282, 1292, 1293). */
export interface CatSetAction {
  type: 'CAT_SET';
  patch: Partial<Pick<AppState, 'catQ' | 'newName' | 'newType' | 'newGroupIds' | 'catalogGroupId'>>;
}

export interface GroupFormSetAction { type: 'GROUP_FORM_SET'; name: string; }
export interface GroupCreateAction { type: 'GROUP_CREATE'; }
export interface GroupEditStartAction { type: 'GROUP_EDIT_START'; id: string | null; }
export interface GroupRenameAction { type: 'GROUP_RENAME'; }
export interface GroupMoveAction { type: 'GROUP_MOVE'; index: number; dir: -1 | 1; }
export interface GroupArchiveAction { type: 'GROUP_ARCHIVE'; id: string; }
export interface GroupRestoreAction { type: 'GROUP_RESTORE'; id: string; }
export interface GroupDeleteAction { type: 'GROUP_DELETE'; id: string; }
export interface SymptomGroupsSetAction { type: 'SYMPTOM_GROUPS_SET'; id: string; groupIds: string[]; }
export interface HistoryGroupSetAction { type: 'HISTORY_GROUP_SET'; groupId: string | null; }

/** Патч верхньорівневих полів data (рядки 1274, 1299). */
export interface DataPatchAction {
  type: 'DATA_PATCH';
  patch: Partial<Pick<AppData, 'cycleOn' | 'lock'>>;
}

// ---------------------------------------------------------------------------
// Історія, тренди, звіт
// ---------------------------------------------------------------------------

/** calPrev (рядок 1487). */
export interface HistCalPrevAction { type: 'HIST_CAL_PREV'; }

/** calNext (рядок 1488): histShift = min(0, histShift+1). */
export interface HistCalNextAction { type: 'HIST_CAL_NEXT'; }

/** histChips (рядок 1490). */
export interface HistFilterAction { type: 'HIST_FILTER'; filter: HistFilter; }

/**
 * Патч trends (рядки 1580, 1625, 1631). Зміна period без явного sel
 * скидає sel:null (як у прототипі, рядок 1580).
 */
export interface TrendsSetAction { type: 'TRENDS_SET'; patch: Partial<TrendsState>; }

/** setR (рядок 1685; виклики 1690–1701): патч report. Toggle-и обчислює екран (!r.ctx тощо). */
export interface ReportSetAction { type: 'REPORT_SET'; patch: Partial<ReportState>; }

// ---------------------------------------------------------------------------
// Тост
// ---------------------------------------------------------------------------

/** tset (рядок 1005) для «чистих» тостів без зміни даних (1265, 1303, 1307, 1520, 1521, 1553). */
export interface ToastShowAction { type: 'TOAST_SHOW'; text: string; }

/** Авто-приховання через 2600 мс (таймер у store.tsx). */
export interface ToastHideAction { type: 'TOAST_HIDE'; }

// ---------------------------------------------------------------------------
// Обʼєднання
// ---------------------------------------------------------------------------

export type Action =
  | NavTabAction
  | NavSubAction
  | CatBackAction
  | CrisisBackAction
  | CrisisAnswerAction
  | SafetyMoreAction
  | ObSetAction
  | ObFinishAction
  | TodayNoSymptomsAction
  | CheckinStartAction
  | CheckinPatchAction
  | CheckinSymPatchAction
  | CheckinCtxPatchAction
  | CheckinFinishAction
  | CheckinExitAction
  | DialogOpenAction
  | DialogCloseAction
  | MensesSetAction
  | MensesConfirmAction
  | MensesRemoveAction
  | EntryDeleteAction
  | DataDeleteAction
  | FlareDlgToggleAction
  | FlareDlgNoteAction
  | FlareDlgGroupsAction
  | FlareSaveAction
  | FlareDeleteAction
  | CatMoveAction
  | CatArchiveAction
  | CatRestoreAction
  | CatAddCustomAction
  | CatAddFromLibAction
  | CatSetAction
  | GroupFormSetAction
  | GroupCreateAction
  | GroupEditStartAction
  | GroupRenameAction
  | GroupMoveAction
  | GroupArchiveAction
  | GroupRestoreAction
  | GroupDeleteAction
  | SymptomGroupsSetAction
  | HistoryGroupSetAction
  | DataPatchAction
  | HistCalPrevAction
  | HistCalNextAction
  | HistFilterAction
  | TrendsSetAction
  | ReportSetAction
  | ToastShowAction
  | ToastHideAction;

// ---------------------------------------------------------------------------
// Creators
// ---------------------------------------------------------------------------

export const navTab = (tab: Tab): Action => ({ type: 'NAV_TAB', tab });
export const navSub = (
  sub: Sub,
  extra: Omit<NavSubAction, 'type' | 'sub'> = {}
): Action => ({ type: 'NAV_SUB', sub, ...extra });
export const catBack = (): Action => ({ type: 'CAT_BACK' });
export const crisisBack = (): Action => ({ type: 'CRISIS_BACK' });
export const crisisAnswer = (ans: CrisisAns): Action => ({ type: 'CRISIS_ANSWER', ans });
export const safetyMore = (): Action => ({ type: 'SAFETY_MORE' });
export const obSet = (patch: ObSetAction['patch']): Action => ({ type: 'OB_SET', patch });
export const obFinish = (): Action => ({ type: 'OB_FINISH' });
export const todayNoSymptoms = (): Action => ({ type: 'TODAY_NO_SYMPTOMS' });
export const checkinStart = (iso: string, back: 'day' | null = null): Action =>
  ({ type: 'CHECKIN_START', iso, back });
export const checkinPatch = (patch: Partial<CheckinDraft>): Action => ({ type: 'CHECKIN_PATCH', patch });
export const checkinSymPatch = (id: string, patch: Partial<SymValue>): Action =>
  ({ type: 'CHECKIN_SYM_PATCH', id, patch });
export const checkinCtxPatch = (patch: Partial<Ctx>): Action => ({ type: 'CHECKIN_CTX_PATCH', patch });
export const checkinFinish = (): Action => ({ type: 'CHECKIN_FINISH' });
export const checkinExit = (saveDraft: boolean): Action => ({ type: 'CHECKIN_EXIT', saveDraft });
export const dialogOpen = (dialog: DialogState): Action => ({ type: 'DIALOG_OPEN', dialog });
export const dialogClose = (): Action => ({ type: 'DIALOG_CLOSE' });
export const mensesSet = (p: { sel?: 'today' | 'yest'; custom?: string }): Action =>
  ({ type: 'MENSES_SET', ...p });
export const mensesConfirm = (): Action => ({ type: 'MENSES_CONFIRM' });
export const mensesRemove = (iso: string): Action => ({ type: 'MENSES_REMOVE', iso });
export const entryDelete = (iso: string): Action => ({ type: 'ENTRY_DELETE', iso });
export const dataDelete = (scope: DataDeleteAction['scope']): Action => ({ type: 'DATA_DELETE', scope });
export const flareDlgToggle = (flag: FlareFlagKey): Action => ({ type: 'FLARE_DLG_TOGGLE', flag });
export const flareDlgNote = (note: string): Action => ({ type: 'FLARE_DLG_NOTE', note });
export const flareDlgGroups = (groupIds: string[]): Action => ({ type: 'FLARE_DLG_GROUPS', groupIds });
export const flareSave = (): Action => ({ type: 'FLARE_SAVE' });
export const flareDelete = (): Action => ({ type: 'FLARE_DELETE' });
export const catMove = (index: number, dir: -1 | 1): Action => ({ type: 'CAT_MOVE', index, dir });
export const catArchive = (id: string): Action => ({ type: 'CAT_ARCHIVE', id });
export const catRestore = (id: string): Action => ({ type: 'CAT_RESTORE', id });
export const catAddCustom = (): Action => ({ type: 'CAT_ADD_CUSTOM' });
export const catAddFromLib = (name: string, cat: string): Action => ({ type: 'CAT_ADD_FROM_LIB', name, cat });
export const catSet = (patch: CatSetAction['patch']): Action => ({ type: 'CAT_SET', patch });
export const groupFormSet = (name: string): Action => ({ type: 'GROUP_FORM_SET', name });
export const groupCreate = (): Action => ({ type: 'GROUP_CREATE' });
export const groupEditStart = (id: string | null): Action => ({ type: 'GROUP_EDIT_START', id });
export const groupRename = (): Action => ({ type: 'GROUP_RENAME' });
export const groupMove = (index: number, dir: -1 | 1): Action => ({ type: 'GROUP_MOVE', index, dir });
export const groupArchive = (id: string): Action => ({ type: 'GROUP_ARCHIVE', id });
export const groupRestore = (id: string): Action => ({ type: 'GROUP_RESTORE', id });
export const groupDelete = (id: string): Action => ({ type: 'GROUP_DELETE', id });
export const symptomGroupsSet = (id: string, groupIds: string[]): Action => ({ type: 'SYMPTOM_GROUPS_SET', id, groupIds });
export const historyGroupSet = (groupId: string | null): Action => ({ type: 'HISTORY_GROUP_SET', groupId });
export const dataPatch = (patch: DataPatchAction['patch']): Action => ({ type: 'DATA_PATCH', patch });
export const histCalPrev = (): Action => ({ type: 'HIST_CAL_PREV' });
export const histCalNext = (): Action => ({ type: 'HIST_CAL_NEXT' });
export const histFilter = (filter: HistFilter): Action => ({ type: 'HIST_FILTER', filter });
export const trendsSet = (patch: Partial<TrendsState>): Action => ({ type: 'TRENDS_SET', patch });
export const reportSet = (patch: Partial<ReportState>): Action => ({ type: 'REPORT_SET', patch });
export const toastShow = (text: string): Action => ({ type: 'TOAST_SHOW', text });
export const toastHide = (): Action => ({ type: 'TOAST_HIDE' });
