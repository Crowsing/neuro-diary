// Argon2id (WASM) — основний KDF за §7, який Фаза 2 реалізує, але не вмикає.
//
// Правило прийняття §7 зафіксоване наперед: p95 ≤ 3000 мс на еталонному
// пристрої A (low-end Android, вимір саме у вбудованому WebView Telegram).
// Вимір недоступний, тому активним дефолтом лишається PBKDF2 (див. params.ts).
// Реалізація й тести існують, щоб увімкнення після виміру було одним рядком, а
// не новою невідрев'юваною криптографією.
//
// Пакет підвантажується виключно динамічним імпортом: так argon2-чанк не
// потрапляє у граф вхідного бандла, і local-only збірка його не тягне.
// hash-wasm вантажить WASM через WebAssembly.instantiate, тобто політиці CSP
// достатньо 'wasm-unsafe-eval' — 'unsafe-eval' не потрібен.

import type { Bytes } from '../bytes';

export interface Argon2idCost {
  readonly mKib: number;
  readonly t: number;
  readonly p: number;
}

export async function argon2idBits(
  passphrase: string,
  salt: Bytes,
  cost: Argon2idCost,
  lengthBytes: number
): Promise<Bytes> {
  const { argon2id } = await import('hash-wasm');
  // hash-wasm типізує вихід загальним Uint8Array; копія в новий буфер повертає
  // тип, який приймає WebCrypto (див. `Bytes`).
  const digest = await argon2id({
    password: passphrase,
    salt,
    parallelism: cost.p,
    iterations: cost.t,
    memorySize: cost.mKib,
    hashLength: lengthBytes,
    outputType: 'binary'
  });
  return Uint8Array.from(digest);
}
