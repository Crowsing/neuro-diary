// Непрозорий ключ запису й офлайн-мапування назад — §6.1 і §7 плану.
//
//   record_key = HMAC-SHA256(k_index, канонічний шлях)
//
// Зрізу [:32] у коді немає свідомо: HMAC-SHA256 і так має рівно 32 байти, тож
// §7 описує тотожність. Скорочення до 16 байтів зробило б колізії досяжними.
//
// Plaintext-заголовка шляху не існує — сервер не знає навіть дат записів. Щоб
// зіставити відповідь сервера з локальним доменом, клієнт будує HMAC-таблицю
// над скінченним простором: усі дати вікна щоденника + п'ять синглтонів.
// Вікно обов'язкове: без верхньої межі таблиця необмежена (DoS на клієнті),
// без нижньої — легітимні старі записи тихо зникають.

import { type RecordPath, SINGLETON_PATHS, assertRecordPath, entryPath } from './aad';
import { type Bytes, toHex, utf8 } from './bytes';
import { diffDays, isIsoDate, isoOff } from '../lib/dates';

export const RECORD_KEY_BYTES = 32;

/** Стеля розміру таблиці: 40 років щоденника. Ширший запит — помилка виклику. */
export const MAX_WINDOW_DAYS = 40 * 366;

export interface DateWindow {
  readonly from: string;
  readonly to: string;
}

export class RecordKeyError extends Error {
  constructor() {
    super('record_key');
    this.name = 'RecordKeyError';
  }
}

export async function recordKey(index: CryptoKey, path: RecordPath): Promise<Bytes> {
  return new Uint8Array(await crypto.subtle.sign('HMAC', index, utf8(path)));
}

export interface HmacTable {
  /** hex-ключ для відомого шляху. Кидає, якщо шлях поза побудованим вікном. */
  keyHexFor(path: RecordPath): string;
  /** Шлях під цим ключем або null, якщо ключ таблиці невідомий. */
  pathFor(recordKeyHex: string): RecordPath | null;
  /** Розширює вікно; уже обчислені ключі не перераховуються. */
  widen(window: DateWindow): Promise<void>;
  readonly size: number;
}

function assertWindow(window: DateWindow): number {
  if (!isIsoDate(window.from) || !isIsoDate(window.to)) throw new RecordKeyError();
  const span = diffDays(window.from, window.to);
  if (span < 0 || span > MAX_WINDOW_DAYS) throw new RecordKeyError();
  return span;
}

export async function buildHmacTable(
  index: CryptoKey,
  window: DateWindow
): Promise<HmacTable> {
  const byKey = new Map<string, RecordPath>();
  const byPath = new Map<RecordPath, string>();

  const remember = async (path: RecordPath): Promise<void> => {
    if (byPath.has(path)) return;
    const hex = toHex(await recordKey(index, path));
    byPath.set(path, hex);
    byKey.set(hex, path);
  };

  const fill = async (target: DateWindow): Promise<void> => {
    const span = assertWindow(target);
    const start = new Date(`${target.from}T12:00:00`);
    for (let offset = 0; offset <= span; offset += 1) {
      await remember(entryPath(isoOff(start, offset)));
    }
  };

  for (const singleton of SINGLETON_PATHS) await remember(assertRecordPath(singleton));
  await fill(window);

  return {
    keyHexFor(path: RecordPath): string {
      const hex = byPath.get(path);
      if (hex === undefined) throw new RecordKeyError();
      return hex;
    },
    pathFor(recordKeyHex: string): RecordPath | null {
      return byKey.get(recordKeyHex) ?? null;
    },
    async widen(next: DateWindow): Promise<void> {
      await fill(next);
    },
    get size(): number {
      return byKey.size;
    }
  };
}
