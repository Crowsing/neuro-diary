// Чанкування initial upload — §9.5 плану.
//
// Чанк ≤200 записів і ≤1 MiB, послідовно. Обрив → ретрай ТОГО САМОГО чанка
// байт-у-байт: клієнт ніколи не перешифровує payload наново, бо кожне
// шифрування дає новий nonce, а manifest фіксує sha256 конкретних байтів (§7).
//
// Manifest оновлюється в КОЖНОМУ чанку, а не один раз наприкінці: інакше
// посилений manifest оголошує кожен проміжний стан помилкою цілісності — саме
// те вікно, у яке інші пристрої гарантовано потрапляють після re-key.

export const MAX_RECORDS_PER_CHUNK = 200;
export const MAX_BYTES_PER_CHUNK = 1_048_576;

export interface EncryptedChange {
  readonly recordKeyHex: string;
  readonly payloadB64: string | null;
  readonly tombstone: boolean;
  readonly clientTsMs: number;
  /** Розмір шифротексту в байтах — те, що рахує серверний ліміт. */
  readonly byteLength: number;
}

/**
 * Верхня оцінка розміру manifest: він росте з кількістю живих записів, а
 * недооцінка коштує 413 на останньому чанку — і саме на ньому вивантаження
 * зупиняється остаточно, бо 413 не ретраїться.
 */
export const MANIFEST_BUDGET_BYTES = 64 * 1024;

export function planChunks(
  changes: readonly EncryptedChange[],
  reserved = 0,
  reservedBytes = reserved > 0 ? MANIFEST_BUDGET_BYTES : 0
): EncryptedChange[][] {
  const limit = Math.max(1, MAX_RECORDS_PER_CHUNK - reserved);
  const byteBudget = Math.max(1, MAX_BYTES_PER_CHUNK - reservedBytes);
  const chunks: EncryptedChange[][] = [];
  let current: EncryptedChange[] = [];
  let bytes = 0;

  for (const change of changes) {
    const wouldExceedCount = current.length >= limit;
    const wouldExceedBytes =
      current.length > 0 && bytes + change.byteLength > byteBudget;
    if (wouldExceedCount || wouldExceedBytes) {
      chunks.push(current);
      current = [];
      bytes = 0;
    }
    current.push(change);
    bytes += change.byteLength;
  }
  if (current.length > 0) chunks.push(current);
  return chunks;
}
