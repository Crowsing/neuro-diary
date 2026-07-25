import { describe, expect, it } from 'vitest';
import { toHex, utf8 } from '../bytes';
import { argon2idBits } from './argon2id';
import { deriveKek } from './index';
import { pbkdf2Bits } from './pbkdf2';
import { parseKdfParams } from './params';

describe('pbkdf2Bits — PBKDF2-HMAC-SHA256', () => {
  // Вектори відтворені незалежно (hashlib.pbkdf2_hmac), а не взяті з цієї
  // реалізації: інакше тест підтверджував би сам себе.
  it('matches the c=1 vector', async () => {
    expect(toHex(await pbkdf2Bits('password', utf8('salt'), 1, 32))).toBe(
      '120fb6cffcf8b32c43e7225256c4f837a86548c92ccc35480805987cb70be17b'
    );
  });

  it('matches the c=4096 vector', async () => {
    expect(toHex(await pbkdf2Bits('password', utf8('salt'), 4096, 32))).toBe(
      'c5e478d59288c841aa530db6845c4c8d962893a001ce4e11a4963873aa98134a'
    );
  });

  it('separates outputs by salt', async () => {
    const a = await pbkdf2Bits('password', utf8('salt-a'), 1000, 32);
    const b = await pbkdf2Bits('password', utf8('salt-b'), 1000, 32);
    expect(toHex(a)).not.toBe(toHex(b));
  });
});

describe('argon2idBits — Argon2id via WASM', () => {
  // Незалежна реалізація: OpenSSL 3.6.1 `openssl kdf ... ARGON2ID`.
  // Параметри навмисно малі — це перевірка передавання m/t/p/salt/outLen,
  // а не бенчмарк. Argon2id не є активним дефолтом (див. index.ts).
  it('matches an independently generated vector', async () => {
    const out = await argon2idBits('password', utf8('somesalt0123456'), {
      mKib: 1024,
      t: 2,
      p: 1
    }, 32);
    expect(toHex(out)).toBe(
      'dc0f798d55429bd36d20fc0600f62470bdf0f238336395eb6cb228c1148a537a'
    );
  });

  it('separates outputs by iteration count', async () => {
    const a = await argon2idBits('password', utf8('somesalt0123456'), { mKib: 1024, t: 2, p: 1 }, 32);
    const b = await argon2idBits('password', utf8('somesalt0123456'), { mKib: 1024, t: 3, p: 1 }, 32);
    expect(toHex(a)).not.toBe(toHex(b));
  });
});

describe('deriveKek', () => {
  const salt = utf8('0123456789abcdef');

  const pbkdf2Params = parseKdfParams({
    kdf: 'pbkdf2-sha256',
    params: { iterations: 600_000, salt_hex: toHex(salt) }
  });

  it('returns a non-extractable AES-GCM key', async () => {
    const kek = await deriveKek('correct horse battery staple', pbkdf2Params);
    expect(kek.extractable).toBe(false);
    expect(kek.algorithm.name).toBe('AES-GCM');
  });

  it('is deterministic for one passphrase and one parameter set', async () => {
    const iv = new Uint8Array(12).fill(1);
    const message = utf8('x');
    const a = await deriveKek('фраза з шести слів для сейфа', pbkdf2Params);
    const b = await deriveKek('фраза з шести слів для сейфа', pbkdf2Params);
    const sealed = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, a, message);
    const opened = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, b, sealed);
    expect(new Uint8Array(opened)).toEqual(message);
  });

  it('routes argon2id parameters to the WASM implementation', async () => {
    const params = parseKdfParams({
      kdf: 'argon2id',
      params: { m_kib: 47104, t: 1, p: 1, salt_hex: toHex(salt) }
    });
    const kek = await deriveKek('фраза з шести слів для сейфа', params);
    expect(kek.algorithm.name).toBe('AES-GCM');
  });

  it('refuses parameters below the §7 floor before touching the passphrase', async () => {
    const weak = parseKdfParams({
      kdf: 'pbkdf2-sha256',
      params: { iterations: 1, salt_hex: toHex(salt) }
    });
    await expect(deriveKek('фраза', weak)).rejects.toThrow();
  });

  it('normalises the passphrase to NFC before deriving', async () => {
    const iv = new Uint8Array(12).fill(2);
    const message = utf8('x');
    const composed = 'ї'.normalize('NFC');
    const decomposed = 'ї'.normalize('NFD');
    expect(composed).not.toBe(decomposed);
    const a = await deriveKek(`фраза ${composed} з шести слів`, pbkdf2Params);
    const b = await deriveKek(`фраза ${decomposed} з шести слів`, pbkdf2Params);
    const sealed = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, a, message);
    const opened = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, b, sealed);
    expect(new Uint8Array(opened)).toEqual(message);
  });
});
