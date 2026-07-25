// Вивід KEK із парольної фрази — §7 плану.
//
// KEK обгортає кореневий секрет R і ніде не персиститься: саме тому зміна
// фрази неможлива без введення поточної, а втрата фрази означає втрату
// серверної копії (не даних — локальна копія первинна).

import type { Bytes } from '../bytes';
import { argon2idBits } from './argon2id';
import { KEK_BYTES, type KdfParams, assertAboveFloor } from './params';
import { pbkdf2Bits } from './pbkdf2';

export async function deriveKekBits(
  passphrase: string,
  params: KdfParams
): Promise<Bytes> {
  // Підлога перевіряється до будь-якої роботи з фразою: параметри приходять із
  // сервера, і саме тут зупиняється спроба нав'язати слабкий KDF (§7).
  assertAboveFloor(params);
  // Фраза українською може прийти в різних формах нормалізації залежно від
  // клавіатури й пристрою; без NFC та сама фраза дала б інший KEK на іншому
  // пристрої, і сейф не відкрився б.
  const normalized = passphrase.normalize('NFC');
  return params.kdf === 'argon2id'
    ? argon2idBits(
        normalized,
        params.salt,
        { mKib: params.mKib, t: params.t, p: params.p },
        KEK_BYTES
      )
    : pbkdf2Bits(normalized, params.salt, params.iterations, KEK_BYTES);
}

export async function deriveKek(
  passphrase: string,
  params: KdfParams
): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    'raw',
    await deriveKekBits(passphrase, params),
    { name: 'AES-GCM' },
    false,
    ['encrypt', 'decrypt']
  );
}
