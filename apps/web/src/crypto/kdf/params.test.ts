import { describe, expect, it } from 'vitest';
import { fromHex, toHex } from '../bytes';
import {
  ARGON2_FLOOR,
  ARGON2_TARGET,
  PBKDF2_FLOOR,
  PBKDF2_TARGET,
  SALT_BYTES,
  type KdfParams,
  assertAboveFloor,
  defaultParams,
  encodeKdfParams,
  generateSalt,
  parseKdfParams,
  toWireParams
} from './params';

const salt = fromHex('000102030405060708090a0b0c0d0e0f');

const argon = (over: Partial<Record<string, unknown>> = {}): unknown => ({
  kdf: 'argon2id',
  params: { m_kib: 65536, t: 3, p: 1, salt_hex: toHex(salt), ...over }
});

const pbkdf2 = (over: Partial<Record<string, unknown>> = {}): unknown => ({
  kdf: 'pbkdf2-sha256',
  params: { iterations: 1_000_000, salt_hex: toHex(salt), ...over }
});

describe('§7 floors', () => {
  it('pins the values fixed by the plan', () => {
    expect(ARGON2_TARGET).toEqual({ mKib: 65536, t: 3, p: 1 });
    expect(ARGON2_FLOOR).toEqual({ mKib: 47104, t: 1 });
    expect(PBKDF2_TARGET).toBe(1_000_000);
    expect(PBKDF2_FLOOR).toBe(600_000);
    expect(SALT_BYTES).toBe(16);
  });

  it('defaults to PBKDF2 while the KDF benchmark of §7 is unmeasured', () => {
    expect(defaultParams(salt).kdf).toBe('pbkdf2-sha256');
  });
});

describe('parseKdfParams — parameters arrive from the server', () => {
  it('accepts the two known kdfs', () => {
    expect(parseKdfParams(argon()).kdf).toBe('argon2id');
    expect(parseKdfParams(pbkdf2()).kdf).toBe('pbkdf2-sha256');
  });

  it.each([
    ['an unknown kdf', { kdf: 'scrypt', params: {} }],
    ['a missing params object', { kdf: 'argon2id' }],
    ['a non-object', 'argon2id'],
    ['null', null],
    ['a string memory cost', argon({ m_kib: '65536' })],
    ['a fractional iteration count', pbkdf2({ iterations: 1.5 })],
    ['odd-length salt hex', argon({ salt_hex: 'abc' })],
    ['non-hex salt', argon({ salt_hex: 'zz'.repeat(16) })]
  ])('rejects %s', (_name, raw) => {
    expect(() => parseKdfParams(raw)).toThrow();
  });
});

describe('assertAboveFloor — a compromised server must not weaken the KDF', () => {
  it('accepts the target parameters', () => {
    expect(() => assertAboveFloor(parseKdfParams(argon()))).not.toThrow();
    expect(() => assertAboveFloor(parseKdfParams(pbkdf2()))).not.toThrow();
  });

  it('accepts argon2id exactly at the floor', () => {
    expect(() =>
      assertAboveFloor(parseKdfParams(argon({ m_kib: 47104, t: 1 })))
    ).not.toThrow();
  });

  it.each([
    ['memory one kibibyte below the floor', argon({ m_kib: 47103 })],
    ['zero iterations', argon({ t: 0 })],
    ['zero parallelism', argon({ p: 0 })],
    ['pbkdf2 below the OWASP floor', pbkdf2({ iterations: 599_999 })]
  ])('rejects %s', (_name, raw) => {
    expect(() => assertAboveFloor(parseKdfParams(raw))).toThrow();
  });

  it.each([
    ['a 15-byte salt for argon2id', argon({ salt_hex: '00'.repeat(15) })],
    ['a 15-byte salt for pbkdf2', pbkdf2({ salt_hex: '00'.repeat(15) })]
  ])('rejects %s', (_name, raw) => {
    expect(() => assertAboveFloor(parseKdfParams(raw))).toThrow();
  });
});

describe('encodeKdfParams — bound into the envelope AAD by explicit 0x1f encoding', () => {
  it('does not depend on the key order the jsonb column returns', () => {
    const one = parseKdfParams({
      kdf: 'argon2id',
      params: { m_kib: 65536, t: 3, p: 1, salt_hex: toHex(salt) }
    });
    const other = parseKdfParams({
      kdf: 'argon2id',
      params: { salt_hex: toHex(salt), p: 1, t: 3, m_kib: 65536 }
    });
    expect(toHex(encodeKdfParams(one))).toBe(toHex(encodeKdfParams(other)));
  });

  it('separates every field with 0x1f', () => {
    const encoded = encodeKdfParams(parseKdfParams(argon()));
    expect([...encoded].filter((b) => b === 0x1f)).toHaveLength(4);
    expect(new TextDecoder().decode(encoded).split('\x1f')[0]).toBe('argon2id');
  });

  it('separates parameters that differ only in one number', () => {
    const a = encodeKdfParams(parseKdfParams(argon({ t: 3 })));
    const b = encodeKdfParams(parseKdfParams(argon({ t: 4 })));
    expect(toHex(a)).not.toBe(toHex(b));
  });

  it('separates the two kdfs even at matching salts', () => {
    const a = encodeKdfParams(parseKdfParams(argon()));
    const b = encodeKdfParams(parseKdfParams(pbkdf2()));
    expect(toHex(a)).not.toBe(toHex(b));
  });

  it('is not canonical JSON', () => {
    const encoded = new TextDecoder().decode(encodeKdfParams(parseKdfParams(pbkdf2())));
    expect(encoded).not.toContain('{');
    expect(encoded).not.toContain('"');
  });
});

describe('generateSalt', () => {
  it('returns 16 fresh bytes', () => {
    const seen = new Set<string>();
    for (let i = 0; i < 16; i += 1) seen.add(toHex(generateSalt()));
    expect(seen.size).toBe(16);
    expect(generateSalt()).toHaveLength(SALT_BYTES);
  });
});

describe('toWireParams', () => {
  it('round-trips through the wire shape the jsonb column stores', () => {
    for (const params of [parseKdfParams(argon()), parseKdfParams(pbkdf2())]) {
      const wire = toWireParams(params);
      const back: KdfParams = parseKdfParams(wire);
      expect(toHex(encodeKdfParams(back))).toBe(toHex(encodeKdfParams(params)));
    }
  });
});
