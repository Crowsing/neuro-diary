import { describe, expect, it } from 'vitest';
import { toHex, utf8 } from './bytes';
import { deriveKek } from './kdf/index';
import { generateSalt, parseKdfParams, type KdfParams } from './kdf/params';
import { generateRoot } from './keys';
import { unwrapRoot, wrapRoot } from './wrap';

function paramsFor(salt: Uint8Array<ArrayBuffer>, iterations = 600_000): KdfParams {
  return parseKdfParams({
    kdf: 'pbkdf2-sha256',
    params: { iterations, salt_hex: toHex(salt) }
  });
}

const passphrase = 'вежа сонце дощ камінь вітер поле';

describe('wrapRoot / unwrapRoot', () => {
  it('round-trips the root secret', async () => {
    const params = paramsFor(generateSalt());
    const kek = await deriveKek(passphrase, params);
    const root = generateRoot();
    const wrapped = await wrapRoot(kek, root, params);
    expect(toHex(await unwrapRoot(kek, wrapped, params))).toBe(toHex(root));
  });

  it('produces a different envelope every time for one root', async () => {
    const params = paramsFor(generateSalt());
    const kek = await deriveKek(passphrase, params);
    const root = generateRoot();
    const a = await wrapRoot(kek, root, params);
    const b = await wrapRoot(kek, root, params);
    expect(toHex(a)).not.toBe(toHex(b));
  });

  it('does not open under another passphrase', async () => {
    const params = paramsFor(generateSalt());
    const wrapped = await wrapRoot(await deriveKek(passphrase, params), generateRoot(), params);
    const other = await deriveKek('інша фраза з шести слів тут', params);
    await expect(unwrapRoot(other, wrapped, params)).rejects.toThrow();
  });

  it('does not open under another salt', async () => {
    const original = paramsFor(generateSalt());
    const wrapped = await wrapRoot(await deriveKek(passphrase, original), generateRoot(), original);
    const rotated = paramsFor(generateSalt());
    await expect(
      unwrapRoot(await deriveKek(passphrase, rotated), wrapped, rotated)
    ).rejects.toThrow();
  });

  it('does not open when the parameters are restated with a different iteration count', async () => {
    const salt = generateSalt();
    const params = paramsFor(salt);
    const kek = await deriveKek(passphrase, params);
    const wrapped = await wrapRoot(kek, generateRoot(), params);
    // Той самий KEK, але параметри в AAD інші: конверт мусить лишитися закритим,
    // інакше сервер міг би тихо переписати kdf_params під слабші.
    await expect(unwrapRoot(kek, wrapped, paramsFor(salt, 700_000))).rejects.toThrow();
  });

  it('refuses a root of the wrong length', async () => {
    const params = paramsFor(generateSalt());
    const kek = await deriveKek(passphrase, params);
    await expect(wrapRoot(kek, utf8('short'), params)).rejects.toThrow();
  });

  it('carries no passphrase or root in the error', async () => {
    const params = paramsFor(generateSalt());
    const wrapped = await wrapRoot(await deriveKek(passphrase, params), generateRoot(), params);
    const other = await deriveKek('інша фраза з шести слів тут', params);
    const error = await unwrapRoot(other, wrapped, params).catch((e: unknown) => e);
    expect(String(error)).not.toContain('вежа');
    expect(String(error)).not.toContain('фраза');
  });
});
