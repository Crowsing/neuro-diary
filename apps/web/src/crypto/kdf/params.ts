// Параметри KDF — §7 плану.
//
// Параметри приходять із сервера (це необхідно для онбордингу нового пристрою
// і не є секретом), тому клієнт зобов'язаний перевіряти їх підлогу: без цієї
// перевірки скомпрометований сервер віддав би параметри рівня «одна ітерація»
// і перетворив парольну фразу на майже відкритий текст.
//
// Кодування для AAD конверта — явне 0x1F-кодування, а не канонічний JSON:
// колонка має тип jsonb, який не зберігає порядок ключів і нормалізує числа,
// тож JSON-серіалізація недетерміновано ламала б розпакування конверта.

import { type Bytes, concat, fromHex, toHex, utf8 } from '../bytes';

export const SALT_BYTES = 16;
export const KEK_BYTES = 32;

export const ARGON2_TARGET = { mKib: 65536, t: 3, p: 1 } as const;
export const ARGON2_FLOOR = { mKib: 47104, t: 1 } as const;
export const PBKDF2_TARGET = 1_000_000;
export const PBKDF2_FLOOR = 600_000;

export interface Argon2idParams {
  readonly kdf: 'argon2id';
  readonly mKib: number;
  readonly t: number;
  readonly p: number;
  readonly salt: Bytes;
}

export interface Pbkdf2Params {
  readonly kdf: 'pbkdf2-sha256';
  readonly iterations: number;
  readonly salt: Bytes;
}

export type KdfParams = Argon2idParams | Pbkdf2Params;

/** Форма, у якій параметри лежать у колонці `vault_key.kdf_params` (jsonb). */
export interface WireKdfParams {
  readonly kdf: string;
  readonly params: Record<string, unknown>;
}

export class KdfParamsRejected extends Error {
  constructor() {
    super('kdf_params');
    this.name = 'KdfParamsRejected';
  }
}

export function generateSalt(): Bytes {
  return crypto.getRandomValues(new Uint8Array(SALT_BYTES));
}

/**
 * Дефолт для нового сейфа. Це PBKDF2, а не Argon2id: правило прийняття §7
 * вимагає виміру p95 ≤ 3000 мс на еталонному пристрої A, а вимір недоступний.
 * Argon2id реалізований і покритий тестами, але жоден шлях коду його не обирає,
 * доки бенчмарк не проведено.
 */
export function defaultParams(salt: Bytes): KdfParams {
  return { kdf: 'pbkdf2-sha256', iterations: PBKDF2_TARGET, salt };
}

function integer(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value)) {
    throw new KdfParamsRejected();
  }
  return value;
}

function salt(value: unknown): Bytes {
  if (typeof value !== 'string') throw new KdfParamsRejected();
  try {
    return fromHex(value);
  } catch {
    throw new KdfParamsRejected();
  }
}

export function parseKdfParams(raw: unknown): KdfParams {
  if (typeof raw !== 'object' || raw === null) throw new KdfParamsRejected();
  const wire = raw as { kdf?: unknown; params?: unknown };
  if (typeof wire.params !== 'object' || wire.params === null) {
    throw new KdfParamsRejected();
  }
  const params = wire.params as Record<string, unknown>;

  if (wire.kdf === 'argon2id') {
    return {
      kdf: 'argon2id',
      mKib: integer(params.m_kib),
      t: integer(params.t),
      p: integer(params.p),
      salt: salt(params.salt_hex)
    };
  }
  if (wire.kdf === 'pbkdf2-sha256') {
    return {
      kdf: 'pbkdf2-sha256',
      iterations: integer(params.iterations),
      salt: salt(params.salt_hex)
    };
  }
  throw new KdfParamsRejected();
}

export function toWireParams(params: KdfParams): WireKdfParams {
  return params.kdf === 'argon2id'
    ? {
        kdf: 'argon2id',
        params: {
          m_kib: params.mKib,
          t: params.t,
          p: params.p,
          salt_hex: toHex(params.salt)
        }
      }
    : {
        kdf: 'pbkdf2-sha256',
        params: { iterations: params.iterations, salt_hex: toHex(params.salt) }
      };
}

export function assertAboveFloor(params: KdfParams): KdfParams {
  if (params.salt.length < SALT_BYTES) throw new KdfParamsRejected();
  if (params.kdf === 'argon2id') {
    if (params.mKib < ARGON2_FLOOR.mKib) throw new KdfParamsRejected();
    if (params.t < ARGON2_FLOOR.t) throw new KdfParamsRejected();
    if (params.p < 1) throw new KdfParamsRejected();
    return params;
  }
  if (params.iterations < PBKDF2_FLOOR) throw new KdfParamsRejected();
  return params;
}

const separator = new Uint8Array([0x1f]);

export function encodeKdfParams(params: KdfParams): Bytes {
  const fields =
    params.kdf === 'argon2id'
      ? ['argon2id', String(params.mKib), String(params.t), String(params.p), toHex(params.salt)]
      : ['pbkdf2-sha256', String(params.iterations), toHex(params.salt)];
  const parts: Bytes[] = [];
  fields.forEach((field, position) => {
    if (position > 0) parts.push(separator);
    parts.push(utf8(field));
  });
  return concat(...parts);
}
