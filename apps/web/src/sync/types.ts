// Одиниці зберігання сейфа — §6.1 плану.
//
// Домен щоденника живе в reducer і не знає про синхронізацію нічого. Цей
// модуль — єдине місце, де він перетворюється на записи сейфа й назад, тож
// саме тут живуть журнали видалень: reducer знає лише, що елемента більше
// немає, а merge потребує знати, коли саме він зник (§4.5, §9.3).

import type { Entry, SymptomDef, TrackingGroup } from '../lib/types';

/** Запис журналу видалень: що зникло і коли (мілісекунди UTC). */
export interface Removal {
  readonly id: string;
  readonly removedAt: number;
}

/** Дата початку циклу з часом додавання — для поелементного merge (2P-set). */
export interface CycleStart {
  readonly date: string;
  readonly addedAt: number;
}

export interface CycleBody {
  readonly kind: 'cycle';
  readonly starts: readonly CycleStart[];
  readonly journal: readonly Removal[];
}

/**
 * Каталог симптомів. Множина мерджиться поелементно, а порядок відображення —
 * окремим полем `orderAt` за LWW: порядок є властивістю списку, а не елемента,
 * і поелементного timestamp для нього не існує.
 */
export interface CatalogBody {
  readonly kind: 'catalog';
  readonly places: Readonly<Record<string, { place: 'active' | 'archived'; at: number }>>;
  readonly custom: Readonly<Record<string, { def: SymptomDef; at: number }>>;
  readonly order: readonly string[];
  readonly orderAt: number;
  readonly journal: readonly Removal[];
}

export interface GroupsBody {
  readonly kind: 'groups';
  readonly groups: Readonly<Record<string, { group: TrackingGroup; at: number }>>;
  readonly membership: Readonly<Record<string, { ids: readonly string[]; at: number }>>;
  readonly order: readonly string[];
  readonly orderAt: number;
  readonly journal: readonly Removal[];
}

export interface SettingsBody {
  readonly kind: 'settings';
  /** `lock` свідомо не синхронізується: локальна демо-властивість (§6.1). */
  readonly cycleOn: boolean;
}

export interface EntryBody {
  readonly kind: 'entry';
  readonly entry: Entry;
}

export type RecordBody =
  | CycleBody
  | CatalogBody
  | GroupsBody
  | SettingsBody
  | EntryBody;

/**
 * Запис у відкритому вигляді — те, що шифрується цілком.
 *
 * `clientTs` і `deviceId` лежать УСЕРЕДИНІ payload: серверна колонка
 * `client_ts_ms` — лише індексна копія, і merge за нею був би merge за
 * значенням, яке сервер може переписати (§9.3).
 */
export interface PlainRecord {
  readonly path: string;
  readonly clientTs: number;
  readonly deviceId: string;
  readonly body: RecordBody;
}

/** Надгробок: запис, який існує лише як факт видалення. */
export interface Tombstone {
  readonly path: string;
  readonly clientTs: number;
  readonly deviceId: string;
  readonly deleted: true;
}

export type SyncRecord = PlainRecord | Tombstone;

export function isTombstone(record: SyncRecord): record is Tombstone {
  return 'deleted' in record;
}

/** Журнали видалень доменних множин, що живуть на межі серіалізації. */
export interface Journals {
  readonly cycle: readonly Removal[];
  readonly catalog: readonly Removal[];
  readonly groups: readonly Removal[];
}

export const EMPTY_JOURNALS: Journals = { cycle: [], catalog: [], groups: [] };
