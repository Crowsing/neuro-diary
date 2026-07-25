// HTTP-клієнт sync — єдине місце в apps/web, яке робить мережеві виклики.
//
// Bearer-only: токен ніколи не потрапляє в URL (§8). Тіла помилок не
// розгортаються в текст для користувачки — сервер віддає стабільні ASCII-коди,
// а українська копія живе в UI (§11).

import type { EncryptedChange } from './chunks';

export type SyncErrorCode =
  | 'offline'
  | 'unauthenticated'
  | 'consent_required'
  | 'conflict'
  | 'vault_reset'
  | 'gone'
  | 'payload_too_large'
  | 'rate_limited'
  | 'no_vault_key'
  | 'server';

export class SyncError extends Error {
  constructor(
    readonly code: SyncErrorCode,
    readonly retryAfterSeconds: number | null = null,
    readonly conflictKeys: readonly string[] = []
  ) {
    super(code);
    this.name = 'SyncError';
  }
}

export interface PushResult {
  readonly newRevision: number;
}

export interface PulledRecord {
  readonly recordKeyHex: string;
  readonly payloadB64: string | null;
  readonly tombstone: boolean;
  readonly revision: number;
  readonly clientTsMs: number;
}

export interface PullResult {
  readonly records: readonly PulledRecord[];
  readonly nextSince: number;
  readonly currentRevision: number;
  readonly reset: boolean;
  readonly consentStateChanged: boolean;
}

export interface VaultKeyView {
  readonly wrappedDek: string;
  readonly kdf: string;
  readonly kdfParams: Record<string, unknown>;
  readonly keyVersion: number;
  readonly wrapVersion: number;
  readonly wrappedDekPrev: string | null;
}

export interface ConsentView {
  readonly kind: string;
  readonly grantedAt: string;
  readonly textVersion: string;
}

/** Порт транспорту: движок не знає ані про fetch, ані про URL. */
export interface SyncTransport {
  authenticate(initData: string, grant?: GrantBody): Promise<string>;
  listConsents(): Promise<readonly ConsentView[]>;
  grantConsent(grant: GrantBody): Promise<readonly ConsentView[]>;
  push(baseRevision: number, changes: readonly EncryptedChange[]): Promise<PushResult>;
  pull(since: number, consentEpoch: number): Promise<PullResult>;
  readKey(): Promise<VaultKeyView | null>;
  writeKey(body: KeyWriteBody): Promise<{ keyVersion: number; wrapVersion: number }>;
  vaultReset(): Promise<{ newRevision: number }>;
}

export interface GrantBody {
  readonly kind: string;
  readonly text_version: string;
  readonly text_sha256: string;
  readonly settings?: { time: string; timezone: string };
  readonly record_key_cycle?: string;
}

export interface KeyWriteBody {
  readonly mode: 'rewrap' | 'rekey';
  readonly expected_wrap_version: number;
  readonly wrapped_dek: string;
  readonly kdf: string;
  readonly kdf_params: Record<string, unknown>;
}

const CODE_BY_STATUS: Record<number, SyncErrorCode> = {
  401: 'unauthenticated',
  403: 'consent_required',
  404: 'no_vault_key',
  410: 'gone',
  413: 'payload_too_large',
  429: 'rate_limited'
};

export class HttpSyncTransport implements SyncTransport {
  private token: string | null = null;

  constructor(
    private readonly baseUrl: string,
    private readonly fetchImpl: typeof fetch = fetch
  ) {}

  setToken(token: string | null): void {
    this.token = token;
  }

  private async call<T>(
    method: string,
    path: string,
    body?: unknown
  ): Promise<T> {
    let response: Response;
    try {
      response = await this.fetchImpl(`${this.baseUrl}${path}`, {
        method,
        headers: {
          ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
          ...(this.token === null ? {} : { Authorization: `Bearer ${this.token}` })
        },
        body: body === undefined ? undefined : JSON.stringify(body)
      });
    } catch {
      // Мережева помилка не має тексту, вартого показу: щоденник працює далі.
      throw new SyncError('offline');
    }

    if (response.ok) return (await response.json()) as T;

    let payload: Record<string, unknown> = {};
    try {
      payload = (await response.json()) as Record<string, unknown>;
    } catch {
      payload = {};
    }

    if (response.status === 409) {
      const reason = payload.reason;
      if (reason === 'conflict') {
        const keys = Array.isArray(payload.conflict_keys)
          ? payload.conflict_keys.filter(
              (item): item is string => typeof item === 'string'
            )
          : [];
        throw new SyncError('conflict', null, keys);
      }
      throw new SyncError(
        payload.error === 'vault_reset' ? 'vault_reset' : 'conflict'
      );
    }

    const retryAfter = response.headers.get('Retry-After');
    throw new SyncError(
      CODE_BY_STATUS[response.status] ?? 'server',
      retryAfter === null ? null : Number.parseInt(retryAfter, 10)
    );
  }

  async authenticate(initData: string, grant?: GrantBody): Promise<string> {
    const body = await this.call<{ session_token: string }>(
      'POST',
      '/v1/auth/telegram',
      grant === undefined
        ? { init_data: initData }
        : { init_data: initData, grant }
    );
    this.token = body.session_token;
    return body.session_token;
  }

  async listConsents(): Promise<readonly ConsentView[]> {
    const body = await this.call<{
      consents: { kind: string; granted_at: string; text_version: string }[];
    }>('GET', '/v1/consents');
    return body.consents.map((item) => ({
      kind: item.kind,
      grantedAt: item.granted_at,
      textVersion: item.text_version
    }));
  }

  async grantConsent(grant: GrantBody): Promise<readonly ConsentView[]> {
    const body = await this.call<{
      consents: { kind: string; granted_at: string; text_version: string }[];
    }>('POST', '/v1/consents', grant);
    return body.consents.map((item) => ({
      kind: item.kind,
      grantedAt: item.granted_at,
      textVersion: item.text_version
    }));
  }

  async push(
    baseRevision: number,
    changes: readonly EncryptedChange[]
  ): Promise<PushResult> {
    const body = await this.call<{ new_revision: number }>('POST', '/v1/sync/push', {
      base_revision: baseRevision,
      changes: changes.map((change) => ({
        record_key: change.recordKeyHex,
        client_ts_ms: change.clientTsMs,
        tombstone: change.tombstone,
        ...(change.tombstone ? {} : { payload_b64: change.payloadB64 })
      }))
    });
    return { newRevision: body.new_revision };
  }

  async pull(since: number, consentEpoch: number): Promise<PullResult> {
    const body = await this.call<{
      records: {
        record_key: string;
        payload_b64: string | null;
        tombstone: boolean;
        revision: number;
        client_ts_ms: number;
      }[];
      next_since: number;
      current_revision: number;
      reset: boolean;
      consent_state_changed: boolean;
    }>('GET', `/v1/sync/pull?since=${since}&consent_epoch=${consentEpoch}`);
    return {
      records: body.records.map((item) => ({
        recordKeyHex: item.record_key,
        payloadB64: item.payload_b64,
        tombstone: item.tombstone,
        revision: item.revision,
        clientTsMs: item.client_ts_ms
      })),
      nextSince: body.next_since,
      currentRevision: body.current_revision,
      reset: body.reset,
      consentStateChanged: body.consent_state_changed
    };
  }

  async readKey(): Promise<VaultKeyView | null> {
    try {
      const body = await this.call<{
        wrapped_dek: string;
        kdf: string;
        kdf_params: Record<string, unknown>;
        key_version: number;
        wrap_version: number;
        wrapped_dek_prev: string | null;
      }>('GET', '/v1/sync/key');
      return {
        wrappedDek: body.wrapped_dek,
        kdf: body.kdf,
        kdfParams: body.kdf_params,
        keyVersion: body.key_version,
        wrapVersion: body.wrap_version,
        wrappedDekPrev: body.wrapped_dek_prev
      };
    } catch (error) {
      if (error instanceof SyncError && error.code === 'no_vault_key') return null;
      throw error;
    }
  }

  async writeKey(
    body: KeyWriteBody
  ): Promise<{ keyVersion: number; wrapVersion: number }> {
    const result = await this.call<{ key_version: number; wrap_version: number }>(
      'POST',
      '/v1/sync/key',
      body
    );
    return { keyVersion: result.key_version, wrapVersion: result.wrap_version };
  }

  async vaultReset(): Promise<{ newRevision: number }> {
    const body = await this.call<{ new_revision: number }>(
      'POST',
      '/v1/account/vault-reset'
    );
    return { newRevision: body.new_revision };
  }
}
