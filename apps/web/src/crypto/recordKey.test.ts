import { describe, expect, it } from 'vitest';
import { assertRecordPath, buildAad, entryPath } from './aad';
import { toHex } from './bytes';
import { decrypt, encrypt } from './envelope';
import { deriveSubkeys } from './keys';
import { RECORD_KEY_BYTES, buildHmacTable, recordKey } from './recordKey';

const root = new Uint8Array(32).fill(0x11);
const subkeys = await deriveSubkeys(root);
const table = await buildHmacTable(subkeys.index, {
  from: '2026-01-10',
  to: '2026-01-20'
});

describe('recordKey', () => {
  it('is 32 bytes', async () => {
    expect(await recordKey(subkeys.index, assertRecordPath('cycle'))).toHaveLength(
      RECORD_KEY_BYTES
    );
  });

  it('is deterministic for one path and one key', async () => {
    const a = await recordKey(subkeys.index, entryPath('2026-01-15'));
    const b = await recordKey(subkeys.index, entryPath('2026-01-15'));
    expect(toHex(a)).toBe(toHex(b));
  });

  it('separates paths', async () => {
    const a = await recordKey(subkeys.index, entryPath('2026-01-15'));
    const b = await recordKey(subkeys.index, entryPath('2026-01-16'));
    const c = await recordKey(subkeys.index, assertRecordPath('cycle'));
    expect(new Set([toHex(a), toHex(b), toHex(c)]).size).toBe(3);
  });

  it('separates vaults', async () => {
    const other = await deriveSubkeys(new Uint8Array(32).fill(0x22));
    const mine = await recordKey(subkeys.index, assertRecordPath('cycle'));
    const theirs = await recordKey(other.index, assertRecordPath('cycle'));
    expect(toHex(mine)).not.toBe(toHex(theirs));
  });
});

describe('HmacTable — offline record_key → path mapping (§7)', () => {
  it('covers the five singletons', () => {
    for (const path of ['cycle', 'catalog', 'groups', 'settings', 'manifest']) {
      const known = assertRecordPath(path);
      expect(table.pathFor(table.keyHexFor(known))).toBe(known);
    }
  });

  it('covers every date in the window, inclusive on both ends', () => {
    for (const iso of ['2026-01-10', '2026-01-15', '2026-01-20']) {
      const known = entryPath(iso);
      expect(table.pathFor(table.keyHexFor(known))).toBe(known);
    }
  });

  it('returns null for a key outside the window', async () => {
    const outside = await recordKey(subkeys.index, entryPath('2026-02-01'));
    expect(table.pathFor(toHex(outside))).toBeNull();
  });

  it('returns null for a key from another vault', async () => {
    const other = await deriveSubkeys(new Uint8Array(32).fill(0x22));
    const theirs = await recordKey(other.index, assertRecordPath('cycle'));
    expect(table.pathFor(toHex(theirs))).toBeNull();
  });

  it('widens once and then covers the previously unknown key', async () => {
    const widened = await buildHmacTable(subkeys.index, {
      from: '2026-01-10',
      to: '2026-01-20'
    });
    const outside = await recordKey(subkeys.index, entryPath('2026-02-01'));
    expect(widened.pathFor(toHex(outside))).toBeNull();
    await widened.widen({ from: '2026-01-10', to: '2026-02-05' });
    expect(widened.pathFor(toHex(outside))).toBe(entryPath('2026-02-01'));
  });

  it('refuses a window that is not a pair of calendar dates', async () => {
    await expect(
      buildHmacTable(subkeys.index, { from: '2026-01-20', to: '2026-01-10' })
    ).rejects.toThrow();
    await expect(
      buildHmacTable(subkeys.index, { from: '2026-13-01', to: '2026-13-02' })
    ).rejects.toThrow();
  });

  it('refuses a window wider than the vault can ever need', async () => {
    await expect(
      buildHmacTable(subkeys.index, { from: '1900-01-01', to: '2100-01-01' })
    ).rejects.toThrow();
  });
});

describe('a payload served under a foreign record_key is refused (DoD)', () => {
  it('fails to open when the server swaps two records', async () => {
    const clientTsMs = 1768435200000;
    const mine = entryPath('2026-01-15');
    const payload = await encrypt(
      subkeys.enc,
      new TextEncoder().encode('{"status":"done"}'),
      buildAad(mine, clientTsMs, false)
    );

    // Сервер віддає той самий payload під ключем сусіднього дня. Клієнт бере
    // шлях із таблиці — тобто чужий — і будує під нього AAD.
    const foreignKeyHex = table.keyHexFor(entryPath('2026-01-16'));
    const claimed = table.pathFor(foreignKeyHex);
    expect(claimed).toBe(entryPath('2026-01-16'));
    await expect(
      decrypt(subkeys.enc, payload, buildAad(claimed!, clientTsMs, false))
    ).rejects.toThrow();
  });

  it('rejects a record whose key maps to no path at all', async () => {
    const unknown = await recordKey(subkeys.index, entryPath('2026-02-01'));
    expect(table.pathFor(toHex(unknown))).toBeNull();
  });

  it('fails to open when the server replays a record under a shifted timestamp', async () => {
    const path = entryPath('2026-01-15');
    const payload = await encrypt(
      subkeys.enc,
      new TextEncoder().encode('{"status":"done"}'),
      buildAad(path, 1768435200000, false)
    );
    await expect(
      decrypt(subkeys.enc, payload, buildAad(path, 1768435200001, false))
    ).rejects.toThrow();
  });

  it('fails to open when the server flips the tombstone flag', async () => {
    const path = entryPath('2026-01-15');
    const payload = await encrypt(
      subkeys.enc,
      new TextEncoder().encode('{"status":"done"}'),
      buildAad(path, 1768435200000, false)
    );
    await expect(
      decrypt(subkeys.enc, payload, buildAad(path, 1768435200000, true))
    ).rejects.toThrow();
  });
});
