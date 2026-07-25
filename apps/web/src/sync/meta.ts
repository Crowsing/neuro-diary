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

export const SYNC_META_KEY = 'nd_sync_v1';
export const SYNC_META_VERSION = 1;

export interface RecordMeta {
  /** Ревізія, під якою сервер підтвердив саме цей вміст. */
  readonly revision: number;
  readonly sha256: string;
  /** Є локальні зміни, ще не підтверджені сервером. */
  readonly dirty: boolean;
}

/**
 * Знімок доменних множин для журналів видалень. Зберігаються лише
 * ідентифікатори: щоб помітити зникнення елемента, більше нічого не треба, а
 * повна копія щоденника в службовому ключі була б другою копією медичних даних.
 */
export interface DomainSnapshot {
  readonly cycleStarts: readonly string[];
  readonly catalogIds: readonly string[];
  readonly groupIds: readonly string[];
}

export interface SyncMeta {
  readonly version: number;
  readonly deviceId: string;
  readonly lastAckedRevision: number;
  readonly vaultSeq: number;
  readonly consentEpoch: number;
  /** Ревізія pull, під час якої востаннє читалися згоди (§9.4). */
  readonly consentsFetchedAtRevision: number;
  readonly lastSuccessfulSyncAt: number | null;
  readonly records: Readonly<Record<string, RecordMeta>>;
  readonly snapshot: DomainSnapshot | null;
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
    vaultSeq: 0,
    consentEpoch: 0,
    consentsFetchedAtRevision: -1,
    lastSuccessfulSyncAt: null,
    records: {},
    snapshot: null
  };
}

function isRecordMeta(value: unknown): value is RecordMeta {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.revision === 'number' &&
    typeof candidate.sha256 === 'string' &&
    typeof candidate.dirty === 'boolean'
  );
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
      if (isRecordMeta(item)) records[key] = item;
    }
  }

  const number = (input: unknown, fallback: number): number =>
    typeof input === 'number' && Number.isFinite(input) ? input : fallback;

  return {
    version: SYNC_META_VERSION,
    deviceId: typeof value.deviceId === 'string' ? value.deviceId : deviceId,
    lastAckedRevision: number(value.lastAckedRevision, 0),
    vaultSeq: number(value.vaultSeq, 0),
    consentEpoch: number(value.consentEpoch, 0),
    consentsFetchedAtRevision: number(value.consentsFetchedAtRevision, -1),
    lastSuccessfulSyncAt:
      typeof value.lastSuccessfulSyncAt === 'number'
        ? value.lastSuccessfulSyncAt
        : null,
    records,
    snapshot: parseSnapshot(value.snapshot)
  };
}

function parseSnapshot(value: unknown): DomainSnapshot | null {
  if (typeof value !== 'object' || value === null) return null;
  const candidate = value as Record<string, unknown>;
  const strings = (input: unknown): string[] =>
    Array.isArray(input) ? input.filter((item) => typeof item === 'string') : [];
  return {
    cycleStarts: strings(candidate.cycleStarts),
    catalogIds: strings(candidate.catalogIds),
    groupIds: strings(candidate.groupIds)
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
