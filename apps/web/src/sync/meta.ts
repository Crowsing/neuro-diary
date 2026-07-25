// Клієнтський sync-стан — §9.3 і §9.5 плану.
//
// Живе в ОКРЕМОМУ ключі localStorage, а не в `nd_demo_v3`. Причина механічна:
// `migrateState` будує `AppState` з білого списку полів, тож будь-яке зайве
// поле в стані щоденника зникло б при першому ж завантаженні, а додати його в
// білий список означало б змінити схему домену заради службових даних.
//
// Тут же лежить `sha256(payload)` кожного запису (§9.5): в інкрементальному
// режимі клієнт тримає plaintext, а не байти шифротексту, і без збережених
// дайджестів не міг би перерахувати manifest, не перешифрувавши все — що
// заборонено правилом nonce (§7).

import { EMPTY_JOURNALS, type Journals, type Removal } from './types';

export const SYNC_META_KEY = 'nd_sync_v1';
export const SYNC_META_VERSION = 1;

/**
 * Записи ключуються ЛОГІЧНИМ шляхом (`entry:<iso>`, `cycle`, …), а не
 * `record_key`. Причина не в зручності: `record_key` обчислюється з `k_index`,
 * тобто вимагає введеної фрази, а метадані треба вміти читати до неї — інакше
 * застосунок не знає навіть того, які записи він колись надіслав.
 *
 * Приватність від цього не змінюється: у тому самому localStorage поруч лежить
 * увесь щоденник у відкритому вигляді (`nd_demo_v3`), тож ISO-дати тут не
 * додають нічого, чого там уже немає. На сервер ці ключі не потрапляють ніколи.
 */
export interface RecordMeta {
  /** Ревізія, під якою сервер підтвердив саме цей вміст. */
  readonly revision: number;
  /** sha256 шифротексту — §9.5: без нього manifest не перерахувати. */
  readonly sha256: string;
  /** Є локальні зміни, ще не підтверджені сервером. */
  readonly dirty: boolean;
  /** `client_ts` того вмісту, який підтвердив сервер (автентифікований LWW). */
  readonly clientTs: number;
  /** sha256 ВІДКРИТОГО тіла: за ним визначається, чи запис справді змінився. */
  readonly plain: string;
}

/**
 * Ідентифікатори доменних множин на момент останньої серіалізації.
 *
 * Потрібні рівно для одного: `journalsAfter` виявляє видалення порівнянням із
 * попереднім станом, а після перезавантаження вкладки попереднього стану в
 * пам'яті вже немає. Без персисту видалення, зроблене до перезавантаження, не
 * потрапляє в журнал — і елемент воскресає на першому ж синку.
 */
export interface DomainIds {
  readonly cycle: readonly string[];
  readonly catalog: readonly string[];
  readonly groups: readonly string[];
}

/**
 * Знімок доменних множин: ідентифікатор → мітка часу його появи.
 *
 * Міток недостатньо було б у вигляді самих лише id. Незалежний review Фази 2
 * показав, чому: якщо серіалізація щоразу штампує `addedAt = now`, то будь-яке
 * додавання свіжіше за будь-яке видалення, і 2P-set §9.3 перестає працювати —
 * видалений на іншому пристрої елемент воскресає при першому ж синку. Тому
 * мітка появи елемента переживає перезавантаження саме тут.
 *
 * Це не копія щоденника: тут лежать ідентифікатори множин (дати початків
 * циклу, id симптомів і груп) і числа, без жодного значення спостереження.
 */
export type StampedIds = Readonly<Record<string, number>>;

export interface DomainSnapshot {
  readonly cycleStarts: StampedIds;
  readonly catalogIds: StampedIds;
  readonly groupIds: StampedIds;
  /** Коли востаннє змінювався порядок відображення (LWW цілим списком). */
  readonly orderAt: number;
}

export interface SyncMeta {
  readonly version: number;
  readonly deviceId: string;
  readonly lastAckedRevision: number;
  /**
   * Найвища ревізія, яку цей пристрій БАЧИВ (§7, anti-rollback).
   *
   * Це не те саме, що `lastAckedRevision`: сервер може мати зміни іншого
   * пристрою, яких ми ще не пушили. Відповідь із нижчим значенням означає
   * відкат — або серверний, або підмінений — і не застосовується.
   */
  readonly highestSeenRevision: number;
  readonly vaultSeq: number;
  readonly consentEpoch: number;
  /** Ревізія pull, під час якої востаннє читалися згоди (§9.4). */
  readonly consentsFetchedAtRevision: number;
  readonly lastSuccessfulSyncAt: number | null;
  readonly records: Readonly<Record<string, RecordMeta>>;
  readonly snapshot: DomainSnapshot | null;
  /** Журнали видалень переживають перезавантаження разом із мітками. */
  readonly journals: Journals;
  readonly domainIds: DomainIds | null;
  /**
   * Надгробки, які домен уже застосував, а сервер ще ні.
   *
   * Потрібні там, де намір неможливо відновити зі стану: `DATA_DELETE
   * {scope:'cycle'}` лишає порожню множину, нерозрізнювану від «користувачка
   * прибрала позначки по одній», а §9.4 вимагає на неї саме надгробок `cycle`.
   */
  readonly pendingTombstones: readonly string[];
  /** `key_version` конверта, під яким зашифровано поточний сейф. */
  readonly keyVersion: number;
}

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export function newDeviceId(): string {
  return [...crypto.getRandomValues(new Uint8Array(16))]
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('');
}

export function emptyMeta(deviceId: string): SyncMeta {
  return {
    version: SYNC_META_VERSION,
    deviceId,
    lastAckedRevision: 0,
    highestSeenRevision: 0,
    vaultSeq: 0,
    consentEpoch: 0,
    consentsFetchedAtRevision: -1,
    lastSuccessfulSyncAt: null,
    records: {},
    snapshot: null,
    journals: EMPTY_JOURNALS,
    domainIds: null,
    pendingTombstones: [],
    keyVersion: 1
  };
}

/**
 * Обов'язковими лишаються рівно три поля первісної форми: метадані, записані
 * до появи `clientTs`/`plain`, мусять читатися далі, інакше оновлення застосунку
 * мовчки скидало б увесь стан синхронізації в «нічого не надіслано».
 */
function isRecordMeta(value: unknown): value is Record<string, unknown> {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.revision === 'number' &&
    typeof candidate.sha256 === 'string' &&
    typeof candidate.dirty === 'boolean'
  );
}

function toRecordMeta(value: Record<string, unknown>): RecordMeta {
  return {
    revision: value.revision as number,
    sha256: value.sha256 as string,
    dirty: value.dirty as boolean,
    clientTs: typeof value.clientTs === 'number' ? value.clientTs : 0,
    plain: typeof value.plain === 'string' ? value.plain : ''
  };
}

function parseRemovals(value: unknown): readonly Removal[] {
  if (!Array.isArray(value)) return [];
  const out: Removal[] = [];
  for (const item of value) {
    if (typeof item !== 'object' || item === null) continue;
    const entry = item as Record<string, unknown>;
    if (typeof entry.id !== 'string') continue;
    if (typeof entry.removedAt !== 'number' || !Number.isFinite(entry.removedAt)) continue;
    out.push({ id: entry.id, removedAt: entry.removedAt });
  }
  return out;
}

function parseJournals(value: unknown): Journals {
  if (typeof value !== 'object' || value === null) return EMPTY_JOURNALS;
  const candidate = value as Record<string, unknown>;
  return {
    cycle: parseRemovals(candidate.cycle),
    catalog: parseRemovals(candidate.catalog),
    groups: parseRemovals(candidate.groups)
  };
}

function parseDomainIds(value: unknown): DomainIds | null {
  if (typeof value !== 'object' || value === null) return null;
  const candidate = value as Record<string, unknown>;
  const ids = (input: unknown): readonly string[] =>
    Array.isArray(input) ? input.filter((item): item is string => typeof item === 'string') : [];
  return {
    cycle: ids(candidate.cycle),
    catalog: ids(candidate.catalog),
    groups: ids(candidate.groups)
  };
}

/**
 * Толерантний читач, як і `migrateState` домену: пошкоджені метадані означають
 * повільніший наступний синк, а не втрату щоденника. Локальні дані первинні.
 */
export function parseMeta(raw: string | null, deviceId: string): SyncMeta {
  if (raw === null) return emptyMeta(deviceId);
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return emptyMeta(deviceId);
  }
  if (typeof parsed !== 'object' || parsed === null) return emptyMeta(deviceId);
  const value = parsed as Record<string, unknown>;
  if (value.version !== SYNC_META_VERSION) return emptyMeta(deviceId);

  const records: Record<string, RecordMeta> = {};
  if (typeof value.records === 'object' && value.records !== null) {
    for (const [key, item] of Object.entries(value.records)) {
      if (isRecordMeta(item)) records[key] = toRecordMeta(item);
    }
  }

  const number = (input: unknown, fallback: number): number =>
    typeof input === 'number' && Number.isFinite(input) ? input : fallback;

  const lastAcked = number(value.lastAckedRevision, 0);

  return {
    version: SYNC_META_VERSION,
    deviceId: typeof value.deviceId === 'string' ? value.deviceId : deviceId,
    lastAckedRevision: lastAcked,
    // Метадані, записані до появи поля, не мають виглядати як відкат: підлогою
    // служить те, що пристрій точно бачив, — підтверджена ревізія.
    highestSeenRevision: Math.max(number(value.highestSeenRevision, 0), lastAcked),
    vaultSeq: number(value.vaultSeq, 0),
    consentEpoch: number(value.consentEpoch, 0),
    consentsFetchedAtRevision: number(value.consentsFetchedAtRevision, -1),
    lastSuccessfulSyncAt:
      typeof value.lastSuccessfulSyncAt === 'number'
        ? value.lastSuccessfulSyncAt
        : null,
    records,
    snapshot: parseSnapshot(value.snapshot),
    journals: parseJournals(value.journals),
    domainIds: parseDomainIds(value.domainIds),
    pendingTombstones: Array.isArray(value.pendingTombstones)
      ? value.pendingTombstones.filter((item): item is string => typeof item === 'string')
      : [],
    keyVersion: number(value.keyVersion, 1)
  };
}

function parseSnapshot(value: unknown): DomainSnapshot | null {
  if (typeof value !== 'object' || value === null) return null;
  const candidate = value as Record<string, unknown>;
  const stamped = (input: unknown): StampedIds => {
    if (typeof input !== 'object' || input === null) return {};
    const out: Record<string, number> = {};
    for (const [key, at] of Object.entries(input as Record<string, unknown>)) {
      if (typeof at === 'number' && Number.isFinite(at)) out[key] = at;
    }
    return out;
  };
  return {
    cycleStarts: stamped(candidate.cycleStarts),
    catalogIds: stamped(candidate.catalogIds),
    groupIds: stamped(candidate.groupIds),
    orderAt:
      typeof candidate.orderAt === 'number' && Number.isFinite(candidate.orderAt)
        ? candidate.orderAt
        : 0
  };
}

export function loadMeta(storage: StorageLike, deviceId: string): SyncMeta {
  try {
    return parseMeta(storage.getItem(SYNC_META_KEY), deviceId);
  } catch {
    return emptyMeta(deviceId);
  }
}

export function saveMeta(storage: StorageLike, meta: SyncMeta): boolean {
  try {
    storage.setItem(SYNC_META_KEY, JSON.stringify(meta));
    return true;
  } catch {
    // Переповнене сховище не має ламати щоденник: наступний синк просто
    // почнеться з чистих метаданих.
    return false;
  }
}

export function clearMeta(storage: StorageLike): void {
  try {
    storage.removeItem(SYNC_META_KEY);
  } catch {
    // Те саме: локальні дані первинні.
  }
}
