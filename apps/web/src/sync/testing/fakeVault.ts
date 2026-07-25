// Тестовий двійник сервера сейфа.
//
// Це не мок із заздалегідь записаними відповідями, а маленька реалізація
// протоколу §9.1–9.2 зі справжньою семантикою: монотонні ревізії, per-key
// conflict-check під «локом», горизонт компакшну, reset-гейт, CAS конверта.
//
// Причина, з якої він існує, а не набір `vi.fn()`: два дефекти рівня «зупинка»
// у етапах A–C пройшли повз усі власні тести автора саме тому, що тести
// конструювали дані руками й перевіряли реалізацію самою реалізацією. Тест
// проти цього двійника йде через реальний шлях коду — серіалізацію, шифрування,
// чанки, merge — і бачить те, що побачив би справжній сервер.
//
// Модуль ніколи не імпортується продакшн-кодом, тож у бандл не потрапляє; це
// закріплено `scripts/assert-bundle.mjs`, який шукає сліди клієнта, а не тестів.

import type { EncryptedChange } from '../chunks';
import {
  SyncError,
  type AuthResult,
  type ConsentView,
  type GrantBody,
  type KeyWriteBody,
  type PullResult,
  type PulledRecord,
  type PushResult,
  type SyncTransport,
  type VaultKeyView
} from '../client';

interface StoredRecord {
  payloadB64: string | null;
  tombstone: boolean;
  revision: number;
  clientTsMs: number;
}

interface StoredKey {
  wrappedDek: string;
  kdf: string;
  kdfParams: Record<string, unknown>;
  keyVersion: number;
  wrapVersion: number;
}

/**
 * Керовані збої і зловмисні відповіді.
 *
 * Кожен прапорець існує заради конкретного пункту DoD, а не «про всяк випадок»:
 * без них перевірки §7 неможливо довести на реальному шляху pull.
 */
export interface FakeVaultFaults {
  /** Комміт відбувається, відповідь губиться — найчастіший сценарій обриву. */
  dropNextPushResponse: boolean;
  /** Приховати живий запис у відповіді pull (атака відкату на manifest). */
  withholdRecordKeys: readonly string[];
  /** Віддати payload під чужим record_key. */
  serveUnderForeignKey: { readonly from: string; readonly to: string } | null;
  /** Занизити `current_revision` у відповіді pull (anti-rollback §7). */
  reportRevisionAs: number | null;
  /** Підмінити `client_ts_ms` у відповіді — AAD перестане збігатися. */
  tamperClientTsKeys: readonly string[];
  /** Віддати параметри KDF нижчі за підлогу §7. */
  weakenKdfParams: boolean;
}

function noFaults(): FakeVaultFaults {
  return {
    dropNextPushResponse: false,
    withholdRecordKeys: [],
    serveUnderForeignKey: null,
    reportRevisionAs: null,
    tamperClientTsKeys: [],
    weakenKdfParams: false
  };
}

/**
 * Спільний стан «сервера»: кілька транспортів (пристроїв) дивляться в один
 * і той самий об'єкт, як і належить одному акаунту.
 */
export class FakeVaultServer {
  readonly records = new Map<string, StoredRecord>();
  currentRevision = 0;
  compactedUpTo = 0;
  resetRevision = 0;
  consentEpoch = 0;
  accountExists = false;
  key: StoredKey | null = null;
  readonly consents = new Set<string>();
  /** §9.7: значення, яке клієнт назвав при grant `cycle_sync`. */
  recordKeyCycle: string | null = null;
  readonly faults: FakeVaultFaults = noFaults();
  /** Скільки сесій ревокував останній `revoke-others`. */
  revokedOthers = 0;
  /** initData, за якими вже видано сесію: anti-replay §8. */
  readonly usedInitData = new Set<string>();
  /** Журнал викликів у порядку надходження — для перевірок порядку операцій. */
  readonly calls: string[] = [];

  private nextToken = 1;
  private readonly liveTokens = new Set<string>();

  issueToken(): string {
    const token = `fake-session-${this.nextToken}`;
    this.nextToken += 1;
    this.liveTokens.add(token);
    return token;
  }

  isLive(token: string): boolean {
    return this.liveTokens.has(token);
  }

  revokeAllExcept(token: string): number {
    let revoked = 0;
    for (const live of [...this.liveTokens]) {
      if (live === token) continue;
      this.liveTokens.delete(live);
      revoked += 1;
    }
    this.revokedOthers = revoked;
    return revoked;
  }

  /** Кількість живих (не надгробкових) записів — зручність для тверджень. */
  liveCount(): number {
    let count = 0;
    for (const record of this.records.values()) if (!record.tombstone) count += 1;
    return count;
  }

  /**
   * Найвищий горизонт, якого компакшн узагалі може досягти.
   *
   * Він не піднімається вище за найнижчу живу ревізію: інакше повний ресинк
   * §9.2 не мав би чого повернути, а курсор другої сторінки одразу впав би в
   * 410 (четверте відхилення Фази 2).
   */
  raiseHorizonToLowestLive(): void {
    let lowest = Number.POSITIVE_INFINITY;
    for (const record of this.records.values()) {
      if (!record.tombstone) lowest = Math.min(lowest, record.revision);
    }
    if (Number.isFinite(lowest)) this.compactedUpTo = Math.max(this.compactedUpTo, lowest);
  }

  /**
   * Серверне відкликання згоди — рівно те, що робить `ConsentService.revoke`.
   *
   * Для `cycle_sync` це §9.7: hard DELETE за названим ключем, **без надгробка**,
   * з інкрементом ревізії, у тій самій транзакції, що й `revoked_at`. Надгробок
   * тут деанонімізував би `record_key` саме тоді, коли сервер бачить свіже
   * `revoked_at('cycle_sync')`.
   *
   * Викликати `records.delete` руками — не те саме: без інкременту ревізії й
   * без підняття `consent_epoch` тест не проходив би тим шляхом, яким піде
   * справжній клієнт.
   */
  revokeConsent(kind: string): void {
    if (!this.consents.delete(kind)) return;
    if (kind === 'health_sync') this.consents.delete('cycle_sync');
    if (
      (kind === 'cycle_sync' || kind === 'health_sync') &&
      this.recordKeyCycle !== null &&
      this.records.delete(this.recordKeyCycle)
    ) {
      this.currentRevision += 1;
    }
    this.consentEpoch += 1;
  }

  /** Компакшн: викидає надгробки й піднімає горизонт (§6.4). */
  compact(): void {
    let highest = this.compactedUpTo;
    let lowestLive = Number.POSITIVE_INFINITY;
    for (const record of this.records.values()) {
      if (!record.tombstone) lowestLive = Math.min(lowestLive, record.revision);
    }
    for (const [key, record] of [...this.records.entries()]) {
      if (!record.tombstone) continue;
      // Горизонт не піднімається вище за найнижчу живу ревізію — інакше повний
      // ресинк не має чого повернути (четверте відхилення Фази 2).
      if (record.revision >= lowestLive) continue;
      this.records.delete(key);
      highest = Math.max(highest, record.revision);
    }
    this.compactedUpTo = highest;
  }
}

/** Транспорт одного пристрою поверх спільного стану сервера. */
export class FakeVaultTransport implements SyncTransport {
  private token: string | null = null;

  constructor(
    private readonly server: FakeVaultServer,
    private readonly initData: string = 'fake-init-data'
  ) {}

  private requireSession(): string {
    if (this.token === null || !this.server.isLive(this.token)) {
      throw new SyncError('unauthenticated');
    }
    return this.token;
  }

  private requireHealthSync(): void {
    if (!this.server.consents.has('health_sync')) {
      throw new SyncError('consent_required');
    }
  }

  async authenticate(initData: string, grant?: GrantBody): Promise<AuthResult> {
    this.server.calls.push('authenticate');
    if (!this.server.accountExists && grant === undefined) {
      // Гілка 2 §8: нуль рядків і initData НЕ витрачається — саме тому клієнт
      // має право повторити виклик із grant тим самим рядком.
      throw new SyncError('no_account');
    }
    if (this.server.usedInitData.has(initData)) {
      // Anti-replay §8: сесія видається щонайбільше один раз на конкретний
      // initData. Без цього тест двох пристроїв був би нечесним.
      throw new SyncError('unauthenticated');
    }
    if (grant !== undefined && this.server.consents.has(grant.kind)) {
      // §4.3: повторний grant активної згоди — 409, а не тихий no-op.
      throw new SyncError('conflict');
    }
    this.server.usedInitData.add(initData);
    if (grant !== undefined) {
      this.server.accountExists = true;
      this.server.consents.add(grant.kind);
      this.server.consentEpoch += 1;
    }
    this.token = this.server.issueToken();
    return { token: this.token, consents: await this.listConsents() };
  }

  async listConsents(): Promise<readonly ConsentView[]> {
    this.requireSession();
    this.server.calls.push('listConsents');
    return [...this.server.consents].map((kind) => ({
      kind,
      grantedAt: '2026-01-15T00:00:00Z',
      textVersion: '0.9'
    }));
  }

  async grantConsent(grant: GrantBody): Promise<readonly ConsentView[]> {
    this.requireSession();
    this.server.calls.push('grantConsent');
    if (grant.kind === 'cycle_sync' && !this.server.consents.has('health_sync')) {
      // §4.3: cycle_sync надається лише за активної health_sync.
      throw new SyncError('conflict');
    }
    this.server.consents.add(grant.kind);
    if (grant.record_key_cycle !== undefined) {
      this.server.recordKeyCycle = grant.record_key_cycle;
    }
    this.server.consentEpoch += 1;
    return this.listConsents();
  }

  /** Порядок перевірок повторює сім кроків §9.1. */
  async push(
    baseRevision: number,
    changes: readonly EncryptedChange[]
  ): Promise<PushResult> {
    this.requireSession();
    this.server.calls.push('push');
    this.requireHealthSync();

    // Два елементи з однаковим `record_key` в одному push — це два рядки з
    // однаковим первинним ключем в одному UPSERT. Реальна PostgreSQL відповіла
    // б «cannot affect row a second time», тож двійник мусить бути так само
    // непримиренним: інакше клієнт може роками надсилати таке, а тести —
    // мовчати.
    const seen = new Set<string>();
    for (const change of changes) {
      if (seen.has(change.recordKeyHex)) {
        throw new SyncError('server');
      }
      seen.add(change.recordKeyHex);
    }

    if (baseRevision < this.server.resetRevision) {
      throw new SyncError('vault_reset');
    }
    if (baseRevision < this.server.compactedUpTo) {
      throw new SyncError('gone');
    }

    const conflicts = changes
      .filter((change) => {
        const known = this.server.records.get(change.recordKeyHex);
        return known !== undefined && known.revision > baseRevision;
      })
      .map((change) => change.recordKeyHex);
    if (conflicts.length > 0) {
      throw new SyncError('conflict', null, conflicts);
    }

    for (const change of changes) {
      this.server.currentRevision += 1;
      this.server.records.set(change.recordKeyHex, {
        payloadB64: change.tombstone ? null : change.payloadB64,
        tombstone: change.tombstone,
        revision: this.server.currentRevision,
        clientTsMs: change.clientTsMs
      });
    }

    if (this.server.faults.dropNextPushResponse) {
      // Комміт уже відбувся — губиться саме відповідь.
      this.server.faults.dropNextPushResponse = false;
      throw new SyncError('offline');
    }

    return { newRevision: this.server.currentRevision };
  }

  async pull(since: number, consentEpoch: number, limit = 500): Promise<PullResult> {
    this.requireSession();
    this.server.calls.push('pull');
    this.requireHealthSync();

    if (since > 0 && since < this.server.compactedUpTo) {
      throw new SyncError('gone');
    }

    const faults = this.server.faults;
    const withheld = new Set(faults.withholdRecordKeys);
    const tampered = new Set(faults.tamperClientTsKeys);

    const page: PulledRecord[] = [...this.server.records.entries()]
      .filter(([key, record]) => record.revision > since && !withheld.has(key))
      .sort((a, b) => a[1].revision - b[1].revision)
      .slice(0, limit)
      .map(([key, record]) => ({
        recordKeyHex:
          faults.serveUnderForeignKey !== null && faults.serveUnderForeignKey.from === key
            ? faults.serveUnderForeignKey.to
            : key,
        payloadB64: record.payloadB64,
        tombstone: record.tombstone,
        revision: record.revision,
        clientTsMs: tampered.has(key) ? record.clientTsMs + 1 : record.clientTsMs
      }));

    const highest = page.length === 0 ? since : page[page.length - 1].revision;
    return {
      records: page,
      nextSince: highest,
      currentRevision: faults.reportRevisionAs ?? this.server.currentRevision,
      reset: since < this.server.resetRevision,
      consentStateChanged: consentEpoch < this.server.consentEpoch
    };
  }

  async readKey(): Promise<VaultKeyView | null> {
    this.requireSession();
    this.server.calls.push('readKey');
    const key = this.server.key;
    if (key === null) return null;
    return {
      wrappedDek: key.wrappedDek,
      kdf: key.kdf,
      kdfParams: this.server.faults.weakenKdfParams
        ? weakened(key.kdf, key.kdfParams)
        : key.kdfParams,
      keyVersion: key.keyVersion,
      wrapVersion: key.wrapVersion,
      wrappedDekPrev: null
    };
  }

  async writeKey(
    body: KeyWriteBody
  ): Promise<{ keyVersion: number; wrapVersion: number }> {
    this.requireSession();
    this.server.calls.push(`writeKey:${body.mode}`);
    const current = this.server.key;
    const currentWrapVersion = current?.wrapVersion ?? 0;
    if (body.expected_wrap_version !== currentWrapVersion) {
      // CAS саме по wrap_version (§7): два конкурентні re-wrap не можуть
      // обидва пройти, інакше одна з нових фраз мовчки губиться.
      throw new SyncError('conflict');
    }
    const next: StoredKey = {
      wrappedDek: body.wrapped_dek,
      kdf: body.kdf,
      kdfParams: body.kdf_params,
      keyVersion:
        body.mode === 'rekey' ? (current?.keyVersion ?? 1) + 1 : (current?.keyVersion ?? 1),
      wrapVersion: currentWrapVersion + 1
    };
    this.server.key = next;
    return { keyVersion: next.keyVersion, wrapVersion: next.wrapVersion };
  }

  async vaultReset(): Promise<{ newRevision: number }> {
    this.requireSession();
    this.server.calls.push('vaultReset');
    this.server.records.clear();
    this.server.currentRevision += 1;
    this.server.resetRevision = this.server.currentRevision;
    this.server.compactedUpTo = this.server.currentRevision;
    return { newRevision: this.server.currentRevision };
  }

  async revokeOtherSessions(): Promise<{ revoked: number }> {
    const token = this.requireSession();
    this.server.calls.push('revokeOtherSessions');
    return { revoked: this.server.revokeAllExcept(token) };
  }

  /** Значення initData цього пристрою — воно ж і його «профіль браузера». */
  ownInitData(): string {
    return this.initData;
  }
}

/** Параметри рівня «одна ітерація» — те, що віддав би скомпрометований сервер. */
function weakened(kdf: string, params: Record<string, unknown>): Record<string, unknown> {
  return kdf === 'argon2id'
    ? { ...params, m_kib: 8, t: 1, p: 1 }
    : { ...params, iterations: 1 };
}
