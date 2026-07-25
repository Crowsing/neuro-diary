// Байтові примітиви крипто-модуля. Нічого доменного тут немає і не з'явиться:
// весь модуль оперує байтами, щоб медичні значення не мали жодного шляху
// потрапити в текст помилки (§11).

const HEX = '0123456789abcdef';

/**
 * Байти над звичайним `ArrayBuffer`. WebCrypto не приймає `SharedArrayBuffer`,
 * а типовий параметр `Uint8Array` з TypeScript 5.7 його допускає — тож увесь
 * крипто-модуль говорить саме цим типом, а не `Uint8Array`.
 */
export type Bytes = Uint8Array<ArrayBuffer>;

export function utf8(value: string): Bytes {
  return new TextEncoder().encode(value);
}

export function concat(...parts: readonly Bytes[]): Bytes {
  const total = parts.reduce((sum, part) => sum + part.length, 0);
  const out = new Uint8Array(total);
  let offset = 0;
  for (const part of parts) {
    out.set(part, offset);
    offset += part.length;
  }
  return out;
}

export function toHex(bytes: Bytes): string {
  let out = '';
  for (const byte of bytes) out += HEX[byte >> 4] + HEX[byte & 0x0f];
  return out;
}

export function fromHex(value: string): Bytes {
  if (value.length % 2 !== 0 || !/^[0-9a-f]*$/.test(value)) {
    throw new RangeError('hex');
  }
  const out = new Uint8Array(value.length / 2);
  for (let i = 0; i < out.length; i += 1) {
    out[i] = Number.parseInt(value.slice(i * 2, i * 2 + 2), 16);
  }
  return out;
}

export function toBase64(bytes: Bytes): string {
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

export function fromBase64(value: string): Bytes {
  const binary = atob(value);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) out[i] = binary.charCodeAt(i);
  return out;
}

/**
 * Порівняння за сталий час. Довжина не є секретом (усі порівнювані значення
 * фіксованої довжини), тому розбіжність довжин повертає false одразу.
 */
export function equalConstantTime(a: Bytes, b: Bytes): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) diff |= a[i] ^ b[i];
  return diff === 0;
}
