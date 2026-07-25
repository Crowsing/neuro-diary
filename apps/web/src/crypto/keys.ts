// Ієрархія ключів сейфа — §7 плану.
//
// Кореневий секрет R = 32 випадкові байти; саме R обгортається KEK і лежить на
// сервері у vault_key.wrapped_dek. З R через HKDF-SHA256 виводяться три
// незалежні підключі з фіксованими info-рядками:
//
//   k_enc   "ndv1:enc"    вміст записів (AES-256-GCM)
//   k_index "ndv1:index"  HMAC для record_key
//   k_auth  "ndv1:auth"   HMAC для manifest
//
// Підключі імпортуються non-extractable: жоден шлях коду не має причини
// дістати їхні байти, а екстрактабельний ключ рано чи пізно опиниться в
// localStorage «щоб не виводити щоразу».

import { type Bytes, utf8 } from './bytes';

export const ROOT_BYTES = 32;
export const SUBKEY_BYTES = 32;

/**
 * HKDF потребує солі; тут вона стала і несекретна. Секретність цілком лежить
 * на R, а роль солі виконує фіксований info-рядок, який і розділяє підключі.
 */
export const HKDF_SALT: Bytes = new Uint8Array(32);

export const HKDF_INFO = {
  enc: 'ndv1:enc',
  index: 'ndv1:index',
  auth: 'ndv1:auth'
} as const;

export interface Subkeys {
  readonly enc: CryptoKey;
  readonly index: CryptoKey;
  readonly auth: CryptoKey;
}

export class KeyError extends Error {
  constructor() {
    super('key');
    this.name = 'KeyError';
  }
}

export function generateRoot(): Bytes {
  return crypto.getRandomValues(new Uint8Array(ROOT_BYTES));
}

export async function hkdfSha256(
  ikm: Bytes,
  salt: Bytes,
  info: Bytes,
  lengthBytes: number
): Promise<Bytes> {
  const key = await crypto.subtle.importKey('raw', ikm, 'HKDF', false, ['deriveBits']);
  const bits = await crypto.subtle.deriveBits(
    { name: 'HKDF', hash: 'SHA-256', salt, info },
    key,
    lengthBytes * 8
  );
  return new Uint8Array(bits);
}

async function subkey(
  root: Bytes,
  info: string,
  algorithm: 'AES-GCM' | 'HMAC',
  usages: readonly KeyUsage[]
): Promise<CryptoKey> {
  const material = await hkdfSha256(root, HKDF_SALT, utf8(info), SUBKEY_BYTES);
  return crypto.subtle.importKey(
    'raw',
    material,
    algorithm === 'AES-GCM' ? { name: 'AES-GCM' } : { name: 'HMAC', hash: 'SHA-256' },
    false,
    [...usages]
  );
}

export async function deriveSubkeys(root: Bytes): Promise<Subkeys> {
  if (root.length !== ROOT_BYTES) throw new KeyError();
  const [enc, index, auth] = await Promise.all([
    subkey(root, HKDF_INFO.enc, 'AES-GCM', ['encrypt', 'decrypt']),
    subkey(root, HKDF_INFO.index, 'HMAC', ['sign']),
    subkey(root, HKDF_INFO.auth, 'HMAC', ['sign'])
  ]);
  return { enc, index, auth };
}
