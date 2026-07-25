import { describe, expect, it } from 'vitest';
import { assertRecordPath, buildAad } from './aad';
import { ENVELOPE_VERSION, NONCE_BYTES, TAG_BYTES, decrypt, encrypt } from './envelope';

async function key(seed = 7): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    'raw',
    new Uint8Array(32).fill(seed),
    { name: 'AES-GCM' },
    false,
    ['encrypt', 'decrypt']
  );
}

const aad = buildAad(assertRecordPath('entry:2026-01-15'), 1768435200000, false);
const plaintext = new TextEncoder().encode('{"status":"done"}');

describe('encrypt / decrypt', () => {
  it('round-trips under the same aad', async () => {
    const k = await key();
    expect(await decrypt(k, await encrypt(k, plaintext, aad), aad)).toEqual(plaintext);
  });

  it('produces a different nonce and a different ciphertext every time', async () => {
    const k = await key();
    const seen = new Set<string>();
    const nonces = new Set<string>();
    for (let i = 0; i < 64; i += 1) {
      const payload = await encrypt(k, plaintext, aad);
      expect(payload[0]).toBe(ENVELOPE_VERSION);
      nonces.add([...payload.slice(1, 1 + NONCE_BYTES)].join(','));
      seen.add([...payload.slice(1 + NONCE_BYTES)].join(','));
    }
    expect(nonces.size).toBe(64);
    expect(seen.size).toBe(64);
  });

  it('lays out 0x01 ‖ nonce(12) ‖ ciphertext ‖ tag(16)', async () => {
    const k = await key();
    const payload = await encrypt(k, plaintext, aad);
    expect(payload).toHaveLength(1 + NONCE_BYTES + plaintext.length + TAG_BYTES);
  });

  it('refuses a payload whose aad differs by one bit', async () => {
    const k = await key();
    const payload = await encrypt(k, plaintext, aad);
    const tampered = Uint8Array.from(aad);
    tampered[tampered.length - 1] ^= 0x01;
    await expect(decrypt(k, payload, tampered)).rejects.toThrow();
  });

  it('refuses a payload whose ciphertext differs by one bit', async () => {
    const k = await key();
    const payload = await encrypt(k, plaintext, aad);
    payload[payload.length - 1] ^= 0x01;
    await expect(decrypt(k, payload, aad)).rejects.toThrow();
  });

  it('refuses a payload written under another key', async () => {
    const payload = await encrypt(await key(7), plaintext, aad);
    await expect(decrypt(await key(9), payload, aad)).rejects.toThrow();
  });

  it('refuses an unknown version byte before touching the ciphertext', async () => {
    const k = await key();
    const payload = await encrypt(k, plaintext, aad);
    payload[0] = 0x02;
    await expect(decrypt(k, payload, aad)).rejects.toThrow();
  });

  it('refuses a version byte that disagrees with the aad prefix', async () => {
    const k = await key();
    const payload = await encrypt(k, plaintext, aad);
    const foreign = new TextEncoder().encode('ndv2\x1fcycle\x1f1\x1f0');
    await expect(decrypt(k, payload, foreign)).rejects.toThrow();
  });

  it('refuses a payload too short to hold a nonce and a tag', async () => {
    const k = await key();
    const short = new Uint8Array(1 + NONCE_BYTES + TAG_BYTES - 1);
    short[0] = ENVELOPE_VERSION;
    await expect(decrypt(k, short, aad)).rejects.toThrow();
  });

  it('carries no plaintext in the error', async () => {
    const k = await key();
    const payload = await encrypt(k, plaintext, aad);
    payload[payload.length - 1] ^= 0x01;
    const error = await decrypt(k, payload, aad).catch((e: unknown) => e);
    expect(String(error)).not.toContain('done');
    expect(String(error)).not.toMatch(/\d{4}-\d{2}-\d{2}/);
  });
});
