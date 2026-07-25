import { describe, expect, it } from 'vitest';
import { fromHex, toHex } from './bytes';
import {
  HKDF_INFO,
  ROOT_BYTES,
  deriveSubkeys,
  generateRoot,
  hkdfSha256
} from './keys';

const root = new Uint8Array(32).fill(0x11);

async function mac(key: CryptoKey, message: string): Promise<string> {
  return toHex(
    new Uint8Array(await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(message)))
  );
}

describe('hkdfSha256', () => {
  it('matches RFC 5869 test case 1', async () => {
    const okm = await hkdfSha256(
      new Uint8Array(22).fill(0x0b),
      fromHex('000102030405060708090a0b0c'),
      fromHex('f0f1f2f3f4f5f6f7f8f9'),
      42
    );
    expect(toHex(okm)).toBe(
      '3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf34007208d5b887185865'
    );
  });

  it('separates outputs by info', async () => {
    const salt = new Uint8Array(32);
    const enc = await hkdfSha256(root, salt, new TextEncoder().encode('ndv1:enc'), 32);
    const index = await hkdfSha256(root, salt, new TextEncoder().encode('ndv1:index'), 32);
    expect(toHex(enc)).not.toBe(toHex(index));
  });
});

describe('generateRoot', () => {
  it('returns 32 fresh bytes', () => {
    const seen = new Set<string>();
    for (let i = 0; i < 32; i += 1) {
      const value = generateRoot();
      expect(value).toHaveLength(ROOT_BYTES);
      seen.add(toHex(value));
    }
    expect(seen.size).toBe(32);
  });
});

describe('deriveSubkeys', () => {
  it('uses the info strings fixed by §7', () => {
    expect(HKDF_INFO).toEqual({
      enc: 'ndv1:enc',
      index: 'ndv1:index',
      auth: 'ndv1:auth'
    });
  });

  it('is deterministic for one root', async () => {
    const a = await deriveSubkeys(root);
    const b = await deriveSubkeys(root);
    expect(await mac(a.index, 'cycle')).toBe(await mac(b.index, 'cycle'));
    expect(await mac(a.auth, 'cycle')).toBe(await mac(b.auth, 'cycle'));
  });

  it('derives three independent subkeys', async () => {
    const { index, auth } = await deriveSubkeys(root);
    expect(await mac(index, 'cycle')).not.toBe(await mac(auth, 'cycle'));
  });

  it('changes every subkey when the root changes', async () => {
    const a = await deriveSubkeys(root);
    const b = await deriveSubkeys(new Uint8Array(32).fill(0x22));
    expect(await mac(a.index, 'cycle')).not.toBe(await mac(b.index, 'cycle'));
    expect(await mac(a.auth, 'cycle')).not.toBe(await mac(b.auth, 'cycle'));
  });

  it('binds k_enc to the "ndv1:enc" info string', async () => {
    const { enc } = await deriveSubkeys(root);
    const expected = await crypto.subtle.importKey(
      'raw',
      await hkdfSha256(root, new Uint8Array(32), new TextEncoder().encode('ndv1:enc'), 32),
      { name: 'AES-GCM' },
      false,
      ['encrypt', 'decrypt']
    );
    const iv = new Uint8Array(12).fill(3);
    const message = new TextEncoder().encode('x');
    const sealed = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, expected, message);
    const opened = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, enc, sealed);
    expect(new Uint8Array(opened)).toEqual(message);
  });

  it('keeps the subkeys non-extractable', async () => {
    const { enc, index, auth } = await deriveSubkeys(root);
    for (const key of [enc, index, auth]) expect(key.extractable).toBe(false);
  });

  it('refuses a root of the wrong length', async () => {
    await expect(deriveSubkeys(new Uint8Array(31))).rejects.toThrow();
  });
});
