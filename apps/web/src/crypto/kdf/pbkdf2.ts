// PBKDF2-HMAC-SHA256 через WebCrypto — fallback §7 і активний дефолт Фази 2.
//
// Нативний, нульова вартість бандла, доступний у кожному WebView. Прогресу не
// дає: WebCrypto виконує всі ітерації одним викликом, а розбити його на частини
// без зміни результату неможливо. Тому UI показує невизначений індикатор, а не
// відсотки — фейковий відсоток був би гіршим за чесний спінер.

import type { Bytes } from '../bytes';

export async function pbkdf2Bits(
  passphrase: string,
  salt: Bytes,
  iterations: number,
  lengthBytes: number
): Promise<Bytes> {
  const material = await crypto.subtle.importKey(
    'raw',
    new TextEncoder().encode(passphrase),
    'PBKDF2',
    false,
    ['deriveBits']
  );
  const bits = await crypto.subtle.deriveBits(
    { name: 'PBKDF2', hash: 'SHA-256', salt, iterations },
    material,
    lengthBytes * 8
  );
  return new Uint8Array(bits);
}
