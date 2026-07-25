// Конверт запису сейфа — §7 плану.
//
//   payload = 0x01 ‖ nonce(12) ‖ ciphertext ‖ tag(16)
//
// Три механічні правила §7, які тут виконуються буквально:
//   1) публічний API має вигляд `encrypt(key, plaintext, aad)` — параметра
//      `nonce` немає і не з'явиться (закріплено api.test.ts);
//   2) nonce народжується всередині виклику з `crypto.getRandomValues` і ніколи
//      не виводиться з record_key, ревізії, лічильника або часу;
//   3) при ретраї чанка надсилається байт-у-байт той самий payload — тобто
//      виклик `encrypt` для одного запису відбувається рівно один раз, а не
//      повторюється (це вже інваріант sync-движка, не цього файлу).
//
// WebCrypto AES-GCM повертає `ciphertext ‖ tag`, тож тег не дописується окремо.

import { AAD_VERSION } from './aad';
import { utf8, type Bytes } from './bytes';

export const ENVELOPE_VERSION = 0x01;
export const NONCE_BYTES = 12;
export const TAG_BYTES = 16;

const VERSION_PREFIX = utf8(AAD_VERSION);

export class EnvelopeError extends Error {
  constructor() {
    // Нейтральна помилка без деталей: усе, що тут можна було б повідомити, є
    // або шифротекстом, або шляхом запису (§11).
    super('envelope');
    this.name = 'EnvelopeError';
  }
}

export async function encrypt(
  key: CryptoKey,
  plaintext: Bytes,
  aad: Bytes
): Promise<Bytes> {
  const nonce = crypto.getRandomValues(new Uint8Array(NONCE_BYTES));
  const sealed = new Uint8Array(
    await crypto.subtle.encrypt(
      { name: 'AES-GCM', iv: nonce, additionalData: aad, tagLength: TAG_BYTES * 8 },
      key,
      plaintext
    )
  );
  const payload = new Uint8Array(1 + NONCE_BYTES + sealed.length);
  payload[0] = ENVELOPE_VERSION;
  payload.set(nonce, 1);
  payload.set(sealed, 1 + NONCE_BYTES);
  return payload;
}

export async function decrypt(
  key: CryptoKey,
  payload: Bytes,
  aad: Bytes
): Promise<Bytes> {
  // Версійний байт звіряється з версійним префіксом AAD до будь-якої роботи з
  // шифротекстом: розбіжність тут означає інший формат, а не збій цілісності.
  if (payload.length < 1 + NONCE_BYTES + TAG_BYTES) throw new EnvelopeError();
  if (payload[0] !== ENVELOPE_VERSION) throw new EnvelopeError();
  if (aad.length < VERSION_PREFIX.length) throw new EnvelopeError();
  for (let i = 0; i < VERSION_PREFIX.length; i += 1) {
    if (aad[i] !== VERSION_PREFIX[i]) throw new EnvelopeError();
  }

  try {
    return new Uint8Array(
      await crypto.subtle.decrypt(
        {
          name: 'AES-GCM',
          iv: payload.slice(1, 1 + NONCE_BYTES),
          additionalData: aad,
          tagLength: TAG_BYTES * 8
        },
        key,
        payload.slice(1 + NONCE_BYTES)
      )
    );
  } catch {
    throw new EnvelopeError();
  }
}
