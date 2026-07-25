// Сесія сейфа: те, що досі існувало лише як набір перевірених модулів.
//
// До цього файлу `unwrapRoot`, `buildHmacTable`, `verifyManifest`, `fromRecords`,
// `mergeRecord`, `presenceAuthority`, `needsMassDeleteConfirmation` і перевірка
// підлоги `kdf_params` не мали жодного споживача, тобто половина гарантій §7 і
// §9.4 була доведена в unit-тестах і не діяла в застосунку. Тут вони стають
// шляхом коду.
//
// Три речі, які визначають форму файлу:
//
//  * `R` і підключі живуть ЛИШЕ в пам'яті. На диску немає ані кореневого
//    секрету, ані KEK, ані фрази — саме тому зміна фрази вимагає ввести
//    поточну, а не «розблокувати з кешу»;
//  * локальна копія первинна. Жодна серверна відповідь не видаляє локальних
//    даних без явного підтвердження на пристрої (§9.4);
//  * домен не змінюється. Результат злиття потрапляє в reducer наявною дією
//    `syncApply` (`DATA_PATCH` з `Partial<AppData>`), і `reducer.ts` не
//    редагується.

import { assertRecordPath, buildAad, type RecordPath } from '../crypto/aad';
import { fromBase64, fromHex, toBase64 } from '../crypto/bytes';
import { decrypt } from '../crypto/envelope';
import { deriveKek } from '../crypto/kdf';
import {
  assertAboveFloor,
  defaultParams,
  generateSalt,
  parseKdfParams,
  toWireParams,
  type KdfParams
} from '../crypto/kdf/params';
import { deriveSubkeys, generateRoot, type Subkeys } from '../crypto/keys';
import { payloadDigest, verifyManifest, type ManifestEntry } from '../crypto/manifest';
import { buildHmacTable, type HmacTable } from '../crypto/recordKey';
import { unwrapRoot, wrapRoot } from '../crypto/wrap';
import type { AppData, Entry } from '../lib/types';
import { SyncEngine } from './engine';
import {
  HttpSyncTransport,
  SyncError,
  type GrantBody,
  type SyncTransport,
  type VaultKeyView
} from './client';
import { needsMassDeleteConfirmation, presenceAuthority } from './guards';
import { readInitDataOnce, type BrowserEnv } from './initdata';
import { mergeRecord } from './merge';
import {
  clearMeta,
  emptyMeta,
  loadMeta,
  newDeviceId,
  saveMeta,
  type DomainIds,
  type RecordMeta,
  type StorageLike,
  type SyncMeta
} from './meta';
import {
  canonicalBody,
  domainIdsOf,
  entryPathOf,
  fromRecords,
  isoOfEntryPath,
  journalsAfterIds,
  snapshotOf,
  toRecords
} from './serialize';
import {
  isTombstone,
  type Journals,
  type PlainRecord,
  type RecordBody,
  type SyncRecord
} from './types';

/** Причини відмови, кожна з яких має власний нейтральний текст в UI. */
export type VaultFailure =
  | 'offline'
  | 'unauthenticated'
  | 'no_account'
  | 'step_up_required'
  | 'consent_required'
  | 'gone'
  | 'rate_limited'
  | 'no_vault_key'
  | 'bad_passphrase'
  | 'kdf_params_rejected'
  | 'stale_server_copy'
  | 'integrity'
  | 'conflict'
  | 'locked'
  | 'server';

export class VaultError extends Error {
  constructor(readonly failure: VaultFailure) {
    // Жодних деталей у повідомленні: усе, що тут можна було б сказати, є або
    // шифротекстом, або шляхом запису, або назвою згоди (§11).
    super(failure);
    this.name = 'VaultError';
  }
}

export interface VaultDeps {
  readonly transport: SyncTransport;
  readonly storage: StorageLike;
  readonly browser: BrowserEnv;
}

export interface PullOutcome {
  /** Безпечний патч: без жодного видалення, що потребує підтвердження. */
  readonly patch: Partial<AppData>;
  /** Той самий результат, але з застосованими видаленнями. */
  readonly confirmedPatch: Partial<AppData>;
  /** Записи, відкладені до виходу з чек-іна. */
  readonly deferredEntries: Readonly<Record<string, Entry>>;
  /** Локальні записи, відсутні на сервері: видалення лише з підтвердженням. */
  readonly prunePaths: readonly string[];
  /** Acked, локально відредаговані, на сервері відсутні — окреме рішення. */
  readonly conflictPaths: readonly string[];
  readonly massDelete: boolean;
  /** Серверну копію очищено (§9.4): застосування вимагає підтвердження. */
  readonly vaultWasReset: boolean;
  readonly consentStateChanged: boolean;
  readonly fullResync: boolean;
}

interface DecodedManifest {
  readonly vaultSeq: number;
  readonly keyVersion: number;
  readonly mac: string;
}

interface RemoteRecord {
  readonly path: RecordPath;
  readonly record: SyncRecord;
  readonly revision: number;
  readonly sha256: string;
  readonly clientTs: number;
  /** Відбиток ВІДКРИТОГО тіла саме серверної версії запису. */
  readonly plain: string;
}

/** Вікно HMAC-таблиці за замовчуванням: п'ять років назад і рік уперед. */
const TABLE_BACK_DAYS = 5 * 366;
const TABLE_FORWARD_DAYS = 366;
/** Стеля §7: сорок років щоденника. Ширшої таблиці не існує. */
const TABLE_WIDE_BACK_DAYS = 30 * 366;
const TABLE_WIDE_FORWARD_DAYS = 10 * 366 - 1;

/** Мітка «нічого не втрачати»: найнижча, яку приймає AAD §7 (`> 0`). */
const LOWEST_CLIENT_TS = 1;

function isoOffset(nowMs: number, days: number): string {
  return new Date(nowMs + days * 86_400_000).toISOString().slice(0, 10);
}

/** Переклад кодів транспорту в причини, що мають текст для користувачки. */
function asFailure(error: unknown): VaultError {
  if (error instanceof VaultError) return error;
  if (error instanceof SyncError) {
    switch (error.code) {
      case 'offline':
      case 'unauthenticated':
      case 'no_account':
      case 'step_up_required':
      case 'consent_required':
      case 'gone':
      case 'rate_limited':
      case 'no_vault_key':
      case 'conflict':
        return new VaultError(error.code);
      default:
        return new VaultError('server');
    }
  }
  return new VaultError('server');
}

export class VaultSession {
  private meta: SyncMeta;
  private subkeys: Subkeys | null = null;
  private table: HmacTable | null = null;
  private widened = false;
  private wrapVersion = 0;
  /** Конверт, прочитаний між двома кроками увімкнення. */
  private pendingKey: VaultKeyView | null = null;
  /**
   * Активні згоди, як їх назвав сервер.
   *
   * Порожня множина означає «ще не знаємо», і це навмисно збігається з «немає
   * згоди»: фільтрація за згодою на push — клієнтська (§9.7), тож єдиний
   * безпечний дефолт тут fail-closed.
   */
  private consents = new Set<string>();

  private constructor(
    private readonly deps: VaultDeps,
    meta: SyncMeta
  ) {
    this.meta = meta;
  }

  static create(deps: VaultDeps): VaultSession {
    return new VaultSession(deps, loadMeta(deps.storage, newDeviceId()));
  }

  /** Композиційний корінь sync-збірки: єдине місце, де з'являється HTTP. */
  static forOrigin(options: {
    origin: string;
    storage: StorageLike;
    browser: BrowserEnv;
  }): VaultSession {
    return VaultSession.create({
      transport: new HttpSyncTransport(options.origin),
      storage: options.storage,
      browser: options.browser
    });
  }

  get deviceId(): string {
    return this.meta.deviceId;
  }

  get unlocked(): boolean {
    return this.subkeys !== null;
  }

  get snapshotMeta(): SyncMeta {
    return this.meta;
  }

  /** Активні згоди, як їх назвав сервер — джерело стану перемикачів в UI. */
  get activeConsents(): readonly string[] {
    return [...this.consents];
  }

  private persist(next: SyncMeta): void {
    this.meta = next;
    saveMeta(this.deps.storage, next);
  }

  private requireKeys(): Subkeys {
    if (this.subkeys === null) throw new VaultError('locked');
    return this.subkeys;
  }

  private requireTable(): HmacTable {
    if (this.table === null) throw new VaultError('locked');
    return this.table;
  }

  private async unlock(root: Uint8Array<ArrayBuffer>, nowMs: number): Promise<void> {
    // `R` не переживає цього виклику: далі потрібні лише підключі, а зайва
    // копія кореневого секрету — зайвий час його життя в пам'яті.
    this.subkeys = await deriveSubkeys(root);
    this.table = await buildHmacTable(this.subkeys.index, {
      from: isoOffset(nowMs, -TABLE_BACK_DAYS),
      to: isoOffset(nowMs, TABLE_FORWARD_DAYS)
    });
    this.widened = false;
  }

  /**
   * Перший мережевий крок увімкнення: сесія разом зі згодою.
   *
   * Порядок §8 дослівно: діалог згоди — локальний і передує цьому виклику, тож
   * скасування до нього не лишає на сервері жодного рядка. Відповідь каже, чого
   * питати далі: `create` — придумати фразу, `open` — ввести наявну. Питати
   * нову фразу до сейфа, який уже існує, означало б запропонувати ключ, яким
   * він не відкривається.
   */
  async beginEnable(grant: GrantBody): Promise<'create' | 'open'> {
    const initData = readInitDataOnce(this.deps.browser);
    if (initData === null) throw new VaultError('unauthenticated');
    try {
      // Спершу БЕЗ grant. Порядок обов'язковий: акаунт із уже наданою згодою
      // відповів би 409 `consent_already_active`, тобто другий пристрій не міг
      // би увійти взагалі. Гілка 2 §8 гарантує, що відмова `no_account` не
      // лишає жодного рядка й не витрачає initData, тож повтор із grant
      // легальний — і саме він створює акаунт разом із першою згодою.
      let session;
      try {
        session = await this.deps.transport.authenticate(initData);
      } catch (error) {
        if (!(error instanceof SyncError) || error.code !== 'no_account') throw error;
        session = await this.deps.transport.authenticate(initData, grant);
      }

      this.consents = new Set(session.consents.map((item) => item.kind));
      if (!this.consents.has(grant.kind)) {
        // Акаунт існує, але саме цієї згоди в нього немає (наприклад, лишилася
        // тільки згода на нагадування). Без неї push повернув би 403.
        await this.deps.transport.grantConsent(grant);
        this.consents.add(grant.kind);
      }

      this.pendingKey = await this.deps.transport.readKey();
      return this.pendingKey === null ? 'create' : 'open';
    } catch (error) {
      throw asFailure(error);
    }
  }

  /** Другий крок: фраза введена — або створюємо сейф, або відкриваємо наявний. */
  async finishEnable(input: {
    passphrase: string;
    data: AppData;
    nowMs: number;
  }): Promise<void> {
    try {
      const view = this.pendingKey;
      if (view !== null) {
        await this.openWith(view, input.passphrase, input.nowMs);
        return;
      }

      const root = generateRoot();
      const params = defaultParams(generateSalt());
      const kek = await deriveKek(input.passphrase, params);
      const wrapped = await wrapRoot(kek, root, params);
      const wire = toWireParams(params);
      const written = await this.deps.transport.writeKey({
        mode: 'rewrap',
        expected_wrap_version: 0,
        wrapped_dek: toBase64(wrapped),
        kdf: wire.kdf,
        kdf_params: wire.params
      });
      this.wrapVersion = written.wrapVersion;
      await this.unlock(root, input.nowMs);
      this.persist({ ...this.meta, keyVersion: written.keyVersion });

      await this.uploadEverything(input.data, input.nowMs, 0);
    } catch (error) {
      throw asFailure(error);
    }
  }

  /** Обидва кроки разом — зручність для тестів і для повторного входу. */
  async enable(input: {
    grant: GrantBody;
    passphrase: string;
    data: AppData;
    nowMs: number;
  }): Promise<void> {
    await this.beginEnable(input.grant);
    await this.finishEnable(input);
  }

  /**
   * Відкриття сейфа на другому пристрої.
   *
   * `assertAboveFloor` викликається ДО будь-якої роботи з фразою: параметри
   * приходять із сервера, і саме тут зупиняється спроба нав'язати KDF рівня
   * «одна ітерація» (§7).
   */
  async open(input: { passphrase: string; nowMs: number }): Promise<void> {
    const initData = readInitDataOnce(this.deps.browser);
    if (initData === null) throw new VaultError('unauthenticated');

    try {
      const session = await this.deps.transport.authenticate(initData);
      this.consents = new Set(session.consents.map((item) => item.kind));
      const view = await this.deps.transport.readKey();
      if (view === null) throw new VaultError('no_vault_key');
      await this.openWith(view, input.passphrase, input.nowMs);
    } catch (error) {
      throw asFailure(error);
    }
  }

  private async openWith(
    view: VaultKeyView,
    passphrase: string,
    nowMs: number
  ): Promise<void> {
    const params = this.acceptParams(view.kdf, view.kdfParams);
    const kek = await deriveKek(passphrase, params);
    let root: Uint8Array<ArrayBuffer>;
    try {
      root = await unwrapRoot(kek, fromBase64(view.wrappedDek), params);
    } catch {
      // Невідкритий конверт означає лише те, що ця фраза його не відкриває:
      // сервер zero-knowledge і причини не знає.
      throw new VaultError('bad_passphrase');
    }
    this.wrapVersion = view.wrapVersion;
    await this.unlock(root, nowMs);
    this.persist({ ...this.meta, keyVersion: view.keyVersion });
  }

  private acceptParams(kdf: string, raw: Record<string, unknown>): KdfParams {
    try {
      return assertAboveFloor(parseKdfParams({ kdf, params: raw }));
    } catch {
      throw new VaultError('kdf_params_rejected');
    }
  }

  // ---------------------------------------------------------------------
  // Pull
  // ---------------------------------------------------------------------

  /**
   * Повний шлях pull: сторінка → шлях за `record_key` → AAD → розшифрування →
   * merge → правило авторитетності присутності → патч домену.
   */
  async pull(input: {
    data: AppData;
    nowMs: number;
    checkinDate?: string | null;
  }): Promise<PullOutcome> {
    const keys = this.requireKeys();
    try {
      let fullResync = this.meta.highestSeenRevision === 0;
      let since = fullResync ? 0 : this.meta.highestSeenRevision;
      let page;
      try {
        page = await this.collect(since);
      } catch (error) {
        if (!(error instanceof SyncError) || error.code !== 'gone') throw error;
        // 410: пристрій за горизонтом компакшну — лише повний ресинк (§9.4).
        fullResync = true;
        since = 0;
        page = await this.collect(0);
      }

      // §7: запис, який не зіставляється з відомим шляхом, або чий AAD не
      // збігається, відхиляється нейтральною помилкою цілісності — і разом із
      // ним уся сторінка. Сервер, здатний підмінити один запис, не заслуговує
      // довіри й до решти; локальні дані від цього не страждають.
      if (page.rejected > 0) throw new VaultError('integrity');

      if (page.currentRevision < this.meta.highestSeenRevision) {
        throw new VaultError('stale_server_copy');
      }
      if (page.manifest !== null && page.manifest.vaultSeq < this.meta.vaultSeq) {
        throw new VaultError('stale_server_copy');
      }
      if (fullResync) await this.checkManifest(page);

      // §9.4: pruning не виконується без свіжої відповіді `/v1/consents`, не
      // старшої за цей самий pull. Тому запит іде ПІСЛЯ останньої сторінки.
      const consentKinds = fullResync
        ? (await this.deps.transport.listConsents()).map((item) => item.kind)
        : [];
      if (fullResync) this.consents = new Set(consentKinds);

      return await this.applyPage({ ...page, fullResync, consentKinds }, keys, input);
    } catch (error) {
      throw asFailure(error);
    }
  }

  /** Читає всі сторінки й розкодовує кожен запис, відхиляючи невідповідні. */
  private async collect(since: number): Promise<{
    remote: RemoteRecord[];
    manifest: DecodedManifest | null;
    manifestKeyHex: string;
    rejected: number;
    currentRevision: number;
    consentStateChanged: boolean;
    reset: boolean;
  }> {
    const keys = this.requireKeys();
    const table = this.requireTable();
    const manifestKeyHex = table.keyHexFor(assertRecordPath('manifest'));

    const remote: RemoteRecord[] = [];
    let manifest: DecodedManifest | null = null;
    let rejected = 0;
    let cursor = since;
    let currentRevision = since;
    let consentStateChanged = false;
    let reset = false;

    for (let guard = 0; guard < 1000; guard += 1) {
      const result = await this.deps.transport.pull(cursor, this.meta.consentEpoch);
      currentRevision = result.currentRevision;
      consentStateChanged = consentStateChanged || result.consentStateChanged;
      // §9.4: `reset:true` означає, що серверну копію очищено. Клієнт
      // зобов'язаний показати підтвердження очищення на пристрої і лише після
      // рішення користувачки пушити — тож прапорець доводиться аж до виходу.
      reset = reset || result.reset;

      for (const item of result.records) {
        const path = await this.resolvePath(item.recordKeyHex);
        if (path === null) {
          // §7: ключ, що не зіставляється з жодним відомим шляхом, — не наш.
          rejected += 1;
          continue;
        }
        if (item.tombstone || item.payloadB64 === null) {
          if (path !== 'manifest') {
            remote.push({
              path,
              record: { path, clientTs: item.clientTsMs, deviceId: '', deleted: true },
              revision: item.revision,
              sha256: '',
              clientTs: item.clientTsMs,
              plain: ''
            });
          }
          continue;
        }

        const payload = fromBase64(item.payloadB64);
        let plaintext: Uint8Array<ArrayBuffer>;
        try {
          // AAD відтворюється ДО розшифрування з того, що видно серверу:
          // шляху, `client_ts_ms` і прапорця надгробка. Розбіжність у будь-
          // якому з них — помилка цілісності, а не «інший формат».
          plaintext = await decrypt(keys.enc, payload, buildAad(path, item.clientTsMs, false));
        } catch {
          rejected += 1;
          continue;
        }

        if (item.recordKeyHex === manifestKeyHex) {
          manifest = decodeManifest(plaintext);
          continue;
        }

        const decoded = decodeRecord(path, item.clientTsMs, plaintext);
        if (decoded === null) {
          rejected += 1;
          continue;
        }
        remote.push({
          path,
          record: decoded,
          revision: item.revision,
          sha256: await payloadDigest(payload),
          clientTs: item.clientTsMs,
          plain: digestSync(decoded.body)
        });
      }

      if (result.nextSince <= cursor || result.records.length === 0) break;
      cursor = result.nextSince;
    }

    return {
      remote,
      manifest,
      manifestKeyHex,
      rejected,
      currentRevision,
      consentStateChanged,
      reset
    };
  }

  /**
   * Шлях під цим ключем.
   *
   * Невідомий ключ спершу означає лише те, що дата поза вікном таблиці —
   * запис із 2015 року в щоденнику, відкритому в 2026-му, законний. Тому
   * вікно один раз розширюється до стелі §7, і лише потім запис відхиляється.
   */
  private async resolvePath(recordKeyHex: string): Promise<RecordPath | null> {
    const table = this.requireTable();
    const known = table.pathFor(recordKeyHex);
    if (known !== null) return known;
    if (this.widened) return null;
    this.widened = true;
    const now = Date.now();
    await table.widen({
      from: isoOffset(now, -TABLE_WIDE_BACK_DAYS),
      to: isoOffset(now, TABLE_WIDE_FORWARD_DAYS)
    });
    return table.pathFor(recordKeyHex);
  }

  /**
   * Перевірка manifest після повного pull (§7).
   *
   * Manifest визначається над ЖИВИМИ записами і сам до нього не входить: якби
   * він покривав надгробки, штатний компактор щоразу робив би серверний набір
   * строгою підмножиною зафіксованого — і користувачка бачила б помилку
   * цілісності без жодної атаки.
   */
  private async checkManifest(page: {
    remote: readonly RemoteRecord[];
    manifest: DecodedManifest | null;
  }): Promise<void> {
    const keys = this.requireKeys();
    const table = this.requireTable();
    if (page.manifest === null) {
      // Сейф без manifest — це або справді порожній сервер, або приховування
      // самого manifest. Порожній набір не помилка цілісності, але й не привід
      // щось видаляти: рішення про локальні записи, яких там немає, ухвалює
      // правило авторитетності присутності — і лише з підтвердженням (§9.4).
      if (page.remote.length === 0) return;
      throw new VaultError('stale_server_copy');
    }
    // Manifest із іншого покоління ключа — теж відкат: після re-key серверна
    // копія перезаписується під `key_version + 1`, тож старіший номер означає,
    // що віддали стан до заміни ключа.
    if (page.manifest.keyVersion < this.meta.keyVersion) {
      throw new VaultError('stale_server_copy');
    }

    const live: ManifestEntry[] = [];
    for (const item of page.remote) {
      if (isTombstone(item.record)) continue;
      live.push({
        recordKeyHex: table.keyHexFor(item.path),
        clientTsMs: item.clientTs,
        payloadSha256Hex: item.sha256
      });
    }
    live.sort((a, b) => (a.recordKeyHex < b.recordKeyHex ? -1 : 1));

    const ok = await verifyManifest(
      keys.auth,
      {
        vaultSeq: page.manifest.vaultSeq,
        keyVersion: page.manifest.keyVersion,
        mac: fromHex(page.manifest.mac)
      },
      {
        vaultSeq: page.manifest.vaultSeq,
        keyVersion: page.manifest.keyVersion,
        live
      }
    );
    if (!ok) throw new VaultError('stale_server_copy');
  }

  private async applyPage(
    page: {
      remote: readonly RemoteRecord[];
      manifest: DecodedManifest | null;
      rejected: number;
      currentRevision: number;
      consentStateChanged: boolean;
      reset: boolean;
      fullResync: boolean;
      consentKinds: readonly string[];
    },
    keys: Subkeys,
    input: { data: AppData; nowMs: number; checkinDate?: string | null }
  ): Promise<PullOutcome> {
    void keys;
    const local = this.localRecords(input.data, input.nowMs);

    const merged = new Map<string, SyncRecord>();
    for (const item of local.records) merged.set(item.path, item);

    const localPaths = new Set(local.records.map((item) => item.path));
    let tombstoned = 0;
    for (const item of page.remote) {
      const before = merged.get(item.path) ?? null;
      const after = mergeRecord(before, item.record);
      if (after !== null) merged.set(item.path, after);
      if (isTombstone(item.record) && localPaths.has(item.path)) tombstoned += 1;
    }

    // Захист від масового видалення вкраденою сесією (§9.4). Рахуються лише
    // надгробки на ті шляхи, які на цьому пристрої СПРАВДІ є: на повному
    // ресинку приходять усі нескомпактовані надгробки акаунта, і без цього
    // фільтра діалог підтвердження показувався б майже завжди — тобто привчав
    // би клікати «так» на видалення.
    const massDelete = needsMassDeleteConfirmation(local.records.length, tombstoned);

    const serverPaths = new Set(
      page.remote.filter((item) => !isTombstone(item.record)).map((item) => item.path as string)
    );
    const raw = page.fullResync
      ? presenceAuthority({
          local: local.records.map((item) => ({
            path: item.path,
            acked: this.meta.records[item.path] !== undefined,
            dirty: local.dirty.has(item.path)
          })),
          serverPaths,
          consents: {
            activeKinds: page.consentKinds,
            fetchedAtRevision: page.currentRevision
          },
          pullRevision: page.currentRevision
        })
      : { prune: [], keep: [], needsConfirmation: [] };
    // Порожній перелік згод — це не «жодна не активна», а «сервер відповів
    // дивно»: акаунт існує лише разом із ≥1 згодою (§4.3). Автоматичний
    // pruning на такій відповіді видаляв би й дані циклу, тож усе, що він
    // запропонував, переводиться в явне рішення користувачки — fail-closed, як
    // і фільтрація на push.
    const verdict =
      page.consentKinds.length === 0
        ? {
            prune: [] as readonly string[],
            needsConfirmation: [...raw.prune, ...raw.needsConfirmation] as readonly string[]
          }
        : { prune: raw.prune, needsConfirmation: raw.needsConfirmation };

    const survivors = [...merged.values()];
    const deferredPaths = new Set<string>();
    const deferredEntries: Record<string, Entry> = {};
    if (input.checkinDate != null) {
      const guarded = entryPathOf(input.checkinDate);
      const remote = page.remote.find((item) => item.path === guarded);
      const record = merged.get(guarded);
      if (remote !== undefined && record !== undefined && !isTombstone(record)) {
        // Дзеркало чек-іна живе поза `data`, і `DATA_PATCH` його не чіпає:
        // застосувати сюди віддалений запис означало б розсинхронізувати
        // відкриту форму з тим, що лежить у сховищі.
        deferredPaths.add(guarded);
        if (record.body.kind === 'entry') deferredEntries[input.checkinDate] = record.body.entry;
      }
    }

    // `drop` ВИДАЛЯЄ шлях із патча, а `DATA_PATCH` підставляє поля цілком, тож
    // виключення шляху — це видалення запису з щоденника. Звідси й напрямок:
    // безпечний патч, який застосовується одразу і без діалогу, не виключає
    // НІЧОГО, а лише повертає на місце локальні версії тих шляхів, щодо яких
    // рішення ще не ухвалене. Видаляє — виключно підтверджений патч.
    // Очищена серверна копія (§9.4) робить кожне видалення предметом того
    // самого підтвердження: доки користувачка не вирішила, локальні версії
    // лишаються на місці.
    const holdDeletions = massDelete || page.reset;
    const undecided = [
      ...verdict.prune,
      ...verdict.needsConfirmation,
      ...(holdDeletions ? tombstonedPaths(page.remote) : [])
    ];
    const patch = this.toPatch(survivors, {
      drop: new Set(),
      keepLocal: [...deferredPaths, ...undecided],
      local: local.records
    });
    const confirmedPatch = this.toPatch(survivors, {
      drop: new Set([...verdict.prune, ...verdict.needsConfirmation]),
      keepLocal: [...deferredPaths],
      local: local.records
    });

    // Журнали злиття читаються з тих самих тіл, які поїдуть у домен: це
    // єдине місце, де вони існують після merge, бо `toPatch` їх відкидає.
    const settled = fromRecords([...merged.values()]);
    this.persistAfterPull(
      page,
      local,
      merged,
      { journals: settled.journals, domainIds: domainIdsOf(settled.data) },
      input.nowMs
    );

    return {
      patch,
      confirmedPatch,
      deferredEntries,
      prunePaths: verdict.prune,
      conflictPaths: verdict.needsConfirmation,
      massDelete,
      vaultWasReset: page.reset,
      consentStateChanged: page.consentStateChanged,
      fullResync: page.fullResync
    };
  }

  /**
   * Патч домену: лише синхронізовані поля.
   *
   * `fromRecords` будує повний `AppData` з `emptyData()`, тож передавати його
   * цілком означало б затерти `report` і `lock`, яких сейф не знає.
   */
  private toPatch(
    survivors: readonly SyncRecord[],
    options: {
      drop: ReadonlySet<string>;
      keepLocal: readonly string[];
      local: readonly PlainRecord[];
    }
  ): Partial<AppData> {
    const byPath = new Map<string, SyncRecord>();
    for (const item of survivors) byPath.set(item.path, item);
    for (const path of options.drop) byPath.delete(path);
    for (const path of options.keepLocal) {
      const original = options.local.find((item) => item.path === path);
      if (original !== undefined) byPath.set(path, original);
    }

    const restored = fromRecords([...byPath.values()]);
    return {
      entries: restored.data.entries,
      cycleStarts: restored.data.cycleStarts,
      active: restored.data.active,
      archived: restored.data.archived,
      custom: restored.data.custom,
      groups: restored.data.groups,
      symptomGroupIds: restored.data.symptomGroupIds,
      cycleOn: restored.data.cycleOn
    };
  }

  private persistAfterPull(
    page: {
      remote: readonly RemoteRecord[];
      manifest: DecodedManifest | null;
      currentRevision: number;
      consentStateChanged: boolean;
      fullResync: boolean;
    },
    local: LocalView,
    merged: ReadonlyMap<string, SyncRecord>,
    merge: { journals: Journals; domainIds: DomainIds },
    nowMs: number
  ): void {
    const records: Record<string, RecordMeta> = { ...this.meta.records };
    for (const item of page.remote) {
      if (isTombstone(item.record)) {
        delete records[item.path];
        continue;
      }
      const winner = merged.get(item.path);
      const same = winner !== undefined && !isTombstone(winner) && winner.clientTs === item.clientTs;
      // `plain` описує ВМІСТ СЕРВЕРА, а не переможця злиття: саме з ним
      // порівнюється локальна серіалізація, щоб відповісти «чи є що надсилати».
      // Якби тут лежав відбиток переможця, локальна перемога тихо перетворилася
      // б на «нічого не змінилося», і правка ніколи не поїхала б на сервер.
      records[item.path] = {
        revision: item.revision,
        sha256: item.sha256,
        dirty: !same,
        clientTs: item.clientTs,
        plain: item.plain
      };
    }

    this.persist({
      ...this.meta,
      // Після pull пристрій знає стан сервера аж до `current_revision`, тож
      // саме воно стає базою наступного push. Лишити тут стару
      // `lastAckedRevision` означало б гарантований 409 на кожному push після
      // будь-якої чужої зміни.
      lastAckedRevision: Math.max(this.meta.lastAckedRevision, page.currentRevision),
      highestSeenRevision: Math.max(this.meta.highestSeenRevision, page.currentRevision),
      vaultSeq: page.manifest?.vaultSeq ?? this.meta.vaultSeq,
      consentsFetchedAtRevision: page.fullResync
        ? page.currentRevision
        : this.meta.consentsFetchedAtRevision,
      lastSuccessfulSyncAt: nowMs,
      // `consent_epoch` — курсор, а не назва згоди (§9.2). Не оновлювати його
      // означало б назавжди питати з нуля і назавжди отримувати
      // `consent_state_changed: true`, тобто перетворити нейтральний сигнал на
      // константу.
      consentEpoch: page.consentStateChanged
        ? this.meta.consentEpoch + 1
        : this.meta.consentEpoch,
      records,
      // Журнали й попередні множини — з РЕЗУЛЬТАТУ злиття, а не з
      // доpatch-ового стану. Інакше наступна серіалізація бачила б «id був,
      // тепер немає» для елемента, який зник унаслідок чужого видалення, і
      // штампувала б власну, значно пізнішу мітку — а вона перебивала б
      // законне повторне додавання з іншого пристрою.
      journals: merge.journals,
      domainIds: merge.domainIds,
      snapshot: local.snapshot
    });
  }

  // ---------------------------------------------------------------------
  // Push
  // ---------------------------------------------------------------------

  /** Інкрементальний push: лише те, що справді змінилося. */
  async push(input: { data: AppData; nowMs: number }): Promise<void> {
    const keys = this.requireKeys();
    const local = this.localRecords(input.data, input.nowMs);

    const outgoing = this.withoutUnconsented(local.records);
    const gravePaths = new Set(
      [
        ...this.meta.pendingTombstones,
        ...Object.keys(this.meta.records).filter(
          (path) => !outgoing.some((item) => item.path === path)
        )
      ].filter((path) => this.mayLeave(path))
    );
    // Надгробок і оновлення того самого `record_key` в одному push дали б два
    // елементи з однаковим первинним ключем: реальний UPSERT відповів би 500, а
    // manifest, який рахується по мапі оновлень, оголосив би запис живим — і
    // розходження з сервером не самовідновилося б жодним наступним push.
    const updates = outgoing.filter(
      (item) => local.dirty.has(item.path) && !gravePaths.has(item.path)
    );
    const graves = [...gravePaths].map((path) => ({ path, clientTs: input.nowMs }));

    if (updates.length === 0 && graves.length === 0) {
      this.persist({ ...this.meta, journals: local.journals, domainIds: local.domainIds });
      return;
    }

    const carried: Record<string, { sha256: string; clientTs: number }> = {};
    for (const item of outgoing) {
      if (local.dirty.has(item.path) || gravePaths.has(item.path)) continue;
      const known = this.meta.records[item.path];
      if (known === undefined) continue;
      carried[item.path] = { sha256: known.sha256, clientTs: known.clientTs };
    }

    const engine = new SyncEngine({
      transport: this.deps.transport,
      subkeys: keys,
      deviceId: this.meta.deviceId
    });

    try {
      const report = await engine.upload({
        baseRevision: this.meta.lastAckedRevision,
        updates,
        tombstones: graves,
        carried,
        vaultSeq: this.meta.vaultSeq,
        keyVersion: this.meta.keyVersion,
        clientTs: input.nowMs
      });
      this.persistAfterPush(report, local, graves.map((item) => item.path), input.nowMs);
    } catch (error) {
      throw asFailure(error);
    }
  }

  /**
   * Чи дозволено цьому шляху покидати пристрій.
   *
   * §9.7: сервер не бачить, який ciphertext є циклом, тож фільтрація за згодою
   * можлива лише тут. Дефолт fail-closed — доки згода не названа сервером,
   * запис `cycle` не надсилається взагалі.
   */
  private mayLeave(path: string): boolean {
    return path !== 'cycle' || this.consents.has('cycle_sync');
  }

  private withoutUnconsented(records: readonly PlainRecord[]): PlainRecord[] {
    return records.filter((item) => this.mayLeave(item.path));
  }

  /** Повне вивантаження снапшоту — увімкнення і re-key. */
  private async uploadEverything(
    data: AppData,
    nowMs: number,
    baseRevision: number
  ): Promise<void> {
    const keys = this.requireKeys();
    const local = this.localRecords(data, nowMs, true);
    const engine = new SyncEngine({
      transport: this.deps.transport,
      subkeys: keys,
      deviceId: this.meta.deviceId
    });
    const report = await engine.upload({
      baseRevision,
      updates: this.withoutUnconsented(local.records),
      tombstones: [],
      carried: {},
      vaultSeq: this.meta.vaultSeq,
      keyVersion: this.meta.keyVersion,
      clientTs: nowMs
    });
    this.persistAfterPush(report, local, [], nowMs);
  }

  private persistAfterPush(
    report: {
      finalRevision: number;
      vaultSeq: number;
      digests: Readonly<Record<string, string>>;
      clientTs: Readonly<Record<string, number>>;
    },
    local: LocalView,
    graves: readonly string[],
    nowMs: number
  ): void {
    const records: Record<string, RecordMeta> = { ...this.meta.records };
    for (const path of graves) delete records[path];
    for (const [path, sha256] of Object.entries(report.digests)) {
      records[path] = {
        revision: report.finalRevision,
        sha256,
        dirty: false,
        clientTs: report.clientTs[path] ?? nowMs,
        plain: local.plain.get(path) ?? ''
      };
    }
    this.persist({
      ...this.meta,
      lastAckedRevision: report.finalRevision,
      highestSeenRevision: Math.max(this.meta.highestSeenRevision, report.finalRevision),
      vaultSeq: report.vaultSeq,
      lastSuccessfulSyncAt: nowMs,
      records,
      journals: local.journals,
      domainIds: local.domainIds,
      snapshot: local.snapshot,
      pendingTombstones: []
    });
  }

  /**
   * Серіалізація домену разом із відповіддю «що саме змінилося».
   *
   * `client_ts` незміненого запису береться зі збереженого, а не з `now`:
   * інакше кожен запуск застосунку робив би локальну копію новішою за будь-яку
   * чужу правку, і LWW §9.3 перестав би бути LWW.
   */
  private localRecords(data: AppData, nowMs: number, everythingDirty = false): LocalView {
    const journals = journalsAfterIds(
      this.meta.domainIds,
      domainIdsOf(data),
      this.meta.journals,
      nowMs
    );
    const snapshot = snapshotOf(data, this.meta.snapshot, nowMs);
    const drafted = toRecords(data, journals, this.meta.deviceId, nowMs, snapshot);

    // Порожній щоденник на пристрої, який ще жодного разу не синхронізувався,
    // не є «свіжішою правдою». Без цього винятку синглтони, щойно
    // серіалізовані з міткою `now`, перемагали б у LWW усе, що лежить на
    // сервері, — і другий пристрій отримував би порожні `settings` замість
    // справжніх. Втратити тут нічого: втрачати нема чого.
    const blank = this.meta.lastSuccessfulSyncAt === null && isEmptyDiary(data);

    const records: PlainRecord[] = [];
    const dirty = new Set<string>();
    const plain = new Map<string, string>();
    for (const item of drafted) {
      const known = this.meta.records[item.path];
      const digest = digestSync(item.body);
      plain.set(item.path, digest);
      const changed = everythingDirty || known === undefined || known.plain !== digest;
      if (changed) dirty.add(item.path);
      if (blank) {
        // Найнижча ДОДАТНА мітка, а не нуль: AAD §7 вимагає додатного
        // `client_ts_ms`, тож нуль зривав би шифрування ще до першого запиту —
        // і саме на тому пристрої, де щоденник іще порожній, тобто на
        // онбордингу, який не вимагає обрати жоден симптом.
        records.push({ ...item, clientTs: LOWEST_CLIENT_TS });
        continue;
      }
      records.push(changed ? item : { ...item, clientTs: known.clientTs });
    }

    return { records, dirty, plain, journals, snapshot, domainIds: domainIdsOf(data) };
  }

  // ---------------------------------------------------------------------
  // Ключі
  // ---------------------------------------------------------------------

  /**
   * Зміна фрази: нова сіль, ТОЙ САМИЙ `R`, `wrap_version + 1`.
   *
   * Поточна фраза обов'язкова, і це не UX-рішення: `R` на пристрої не
   * зберігається, тож переобгорнути його інакше просто нічим.
   */
  async changePassphrase(input: { current: string; next: string }): Promise<void> {
    try {
      const view = await this.deps.transport.readKey();
      if (view === null) throw new VaultError('no_vault_key');
      const params = this.acceptParams(view.kdf, view.kdfParams);
      const kek = await deriveKek(input.current, params);
      let root: Uint8Array<ArrayBuffer>;
      try {
        root = await unwrapRoot(kek, fromBase64(view.wrappedDek), params);
      } catch {
        throw new VaultError('bad_passphrase');
      }

      const nextParams = defaultParams(generateSalt());
      const nextKek = await deriveKek(input.next, nextParams);
      const wrapped = await wrapRoot(nextKek, root, nextParams);
      const wire = toWireParams(nextParams);
      const written = await this.deps.transport.writeKey({
        mode: 'rewrap',
        expected_wrap_version: view.wrapVersion,
        wrapped_dek: toBase64(wrapped),
        kdf: wire.kdf,
        kdf_params: wire.params
      });
      this.wrapVersion = written.wrapVersion;
    } catch (error) {
      throw asFailure(error);
    }
  }

  /**
   * Заміна ключа при компрометації — окрема явна дія, а не наслідок зміни фрази.
   *
   * Порядок обов'язковий: `vault-reset` ПЕРЕД записом нового конверта. Зворотний
   * порядок лишив би інші пристрої з сейфом, який уже не відкривається старим
   * ключем і ще не перезаписаний новим.
   */
  async rekey(input: { passphrase: string; data: AppData; nowMs: number }): Promise<void> {
    try {
      const root = generateRoot();
      const params = defaultParams(generateSalt());
      const kek = await deriveKek(input.passphrase, params);
      const wrapped = await wrapRoot(kek, root, params);
      const wire = toWireParams(params);

      const reset = await this.deps.transport.vaultReset();
      await this.unlock(root, input.nowMs);
      this.persist({
        ...this.meta,
        records: {},
        pendingTombstones: [],
        lastAckedRevision: reset.newRevision,
        highestSeenRevision: Math.max(this.meta.highestSeenRevision, reset.newRevision),
        vaultSeq: this.meta.vaultSeq,
        keyVersion: this.meta.keyVersion + 1
      });

      await this.uploadEverything(input.data, input.nowMs, reset.newRevision);

      const written = await this.deps.transport.writeKey({
        mode: 'rekey',
        expected_wrap_version: this.wrapVersion,
        wrapped_dek: toBase64(wrapped),
        kdf: wire.kdf,
        kdf_params: wire.params
      });
      this.wrapVersion = written.wrapVersion;
      this.persist({ ...this.meta, keyVersion: written.keyVersion });

      await this.deps.transport.revokeOtherSessions();
    } catch (error) {
      throw asFailure(error);
    }
  }

  /** Видалення всіх даних на пристрої тягне за собою серверну копію (§9.4). */
  async resetVault(): Promise<void> {
    try {
      await this.deps.transport.vaultReset();
    } catch (error) {
      // Метадані чистяться ЛИШЕ після успіху. Раніше це стояло у `finally`, і
      // при збої серверна копія лишалася на місці, а пристрій уже не знав ані
      // її ревізії, ані дайджестів — тобто не міг ані добити скидання, ані
      // синхронізуватися далі.
      throw asFailure(error);
    }
    clearMeta(this.deps.storage);
    this.meta = emptyMeta(this.meta.deviceId);
    this.subkeys = null;
    this.table = null;
    this.consents = new Set();
  }

  /**
   * Згода на синхронізацію циклу (§9.7).
   *
   * Разом із нею на сервер іде `record_key_cycle = HMAC(k_index,'cycle')` —
   * єдиний спосіб, яким сервер узагалі може дізнатися, який із непрозорих
   * ключів належить домену циклу, щоб при відкликанні видалити саме його
   * hard-DELETE, а не надгробком (надгробок деанонімізував би ключ назавжди).
   * Саме видалення — Фаза 3; тут лише приймання значення.
   */
  async grantCycleSync(grant: GrantBody): Promise<void> {
    const table = this.requireTable();
    try {
      await this.deps.transport.grantConsent({
        ...grant,
        record_key_cycle: table.keyHexFor(assertRecordPath('cycle'))
      });
      this.consents.add(grant.kind);
    } catch (error) {
      throw asFailure(error);
    }
  }

  /** Намір, який неможливо відновити зі стану, фіксується явно (§9.4). */
  markTombstone(path: string): void {
    if (this.meta.pendingTombstones.includes(path)) return;
    this.persist({
      ...this.meta,
      pendingTombstones: [...this.meta.pendingTombstones, path]
    });
  }
}

interface LocalView {
  readonly records: readonly PlainRecord[];
  readonly dirty: ReadonlySet<string>;
  readonly plain: ReadonlyMap<string, string>;
  readonly journals: Journals;
  readonly snapshot: ReturnType<typeof snapshotOf>;
  readonly domainIds: ReturnType<typeof domainIdsOf>;
}

function isEmptyDiary(data: AppData): boolean {
  return (
    Object.keys(data.entries).length === 0 &&
    data.cycleStarts.length === 0 &&
    data.active.length === 0 &&
    data.archived.length === 0 &&
    data.custom.length === 0 &&
    data.groups.length === 0
  );
}

function tombstonedPaths(remote: readonly RemoteRecord[]): string[] {
  return remote.filter((item) => isTombstone(item.record)).map((item) => item.path);
}

function decodeManifest(plaintext: Uint8Array<ArrayBuffer>): DecodedManifest | null {
  try {
    const parsed: unknown = JSON.parse(new TextDecoder().decode(plaintext));
    if (typeof parsed !== 'object' || parsed === null) return null;
    const value = parsed as Record<string, unknown>;
    if (typeof value.vaultSeq !== 'number') return null;
    if (typeof value.keyVersion !== 'number') return null;
    if (typeof value.mac !== 'string') return null;
    return { vaultSeq: value.vaultSeq, keyVersion: value.keyVersion, mac: value.mac };
  } catch {
    return null;
  }
}

function decodeRecord(
  path: RecordPath,
  clientTs: number,
  plaintext: Uint8Array<ArrayBuffer>
): PlainRecord | null {
  try {
    const parsed: unknown = JSON.parse(new TextDecoder().decode(plaintext));
    if (typeof parsed !== 'object' || parsed === null) return null;
    const value = parsed as { body?: unknown; deviceId?: unknown };
    if (typeof value.body !== 'object' || value.body === null) return null;
    const body = value.body as RecordBody;
    // Шлях мусить відповідати тілу: `entry:<iso>` із тілом каталогу означає
    // або зіпсовані дані, або підміну, і мерджити його не можна.
    const expected = path.startsWith('entry:') ? 'entry' : path;
    if (body.kind !== expected) return null;
    return {
      path,
      clientTs,
      deviceId: typeof value.deviceId === 'string' ? value.deviceId : '',
      body
    };
  } catch {
    return null;
  }
}

/**
 * Синхронний дайджест відкритого тіла.
 *
 * SHA-256 у WebCrypto асинхронний, а тут потрібне лише порівняння «змінилося
 * чи ні» — криптографічної стійкості від нього не вимагається, тож дешевий
 * детермінований хеш канонічного рядка робить рівно те, що треба, і не
 * перетворює серіалізацію на асинхронну.
 */
function digestSync(body: RecordBody): string {
  const text = canonicalBody(body);
  let h1 = 0x811c9dc5;
  let h2 = 0x01000193;
  for (let index = 0; index < text.length; index += 1) {
    const code = text.charCodeAt(index);
    h1 = Math.imul(h1 ^ code, 0x01000193) >>> 0;
    h2 = Math.imul(h2 + code, 0x85ebca6b) >>> 0;
  }
  return `${h1.toString(16).padStart(8, '0')}${h2.toString(16).padStart(8, '0')}${text.length.toString(16)}`;
}

export function createVaultSession(options: {
  origin: string;
  storage: StorageLike;
  browser: BrowserEnv;
}): VaultSession {
  return VaultSession.forOrigin(options);
}

export { digestSync as bodyFingerprint, isoOfEntryPath };
