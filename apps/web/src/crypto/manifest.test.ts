import { describe, expect, it } from 'vitest';
import { toHex, utf8 } from './bytes';
import { deriveSubkeys } from './keys';
import {
  MANIFEST_PREFIX,
  type VaultSnapshotRecord,
  buildManifest,
  canonicalManifest,
  liveEntries,
  payloadDigest,
  verifyManifest
} from './manifest';

const subkeys = await deriveSubkeys(new Uint8Array(32).fill(0x11));
const other = await deriveSubkeys(new Uint8Array(32).fill(0x22));

const MANIFEST_KEY = 'ff'.repeat(32);

function record(
  recordKeyHex: string,
  clientTsMs: number,
  body: string,
  deleted = false
): VaultSnapshotRecord {
  return {
    recordKeyHex,
    clientTsMs,
    payload: deleted ? null : utf8(body),
    deleted
  };
}

const snapshot: VaultSnapshotRecord[] = [
  record('bb'.repeat(32), 1768435200002, 'second'),
  record('aa'.repeat(32), 1768435200001, 'first'),
  record(MANIFEST_KEY, 1768435200003, 'manifest itself'),
  record('cc'.repeat(32), 1768435200004, '', true)
];

describe('liveEntries', () => {
  it('keeps only live records, sorted by record_key', async () => {
    const live = await liveEntries(snapshot, MANIFEST_KEY);
    expect(live.map((entry) => entry.recordKeyHex)).toEqual([
      'aa'.repeat(32),
      'bb'.repeat(32)
    ]);
  });

  it('excludes the manifest record itself', async () => {
    const live = await liveEntries(snapshot, MANIFEST_KEY);
    expect(live.map((entry) => entry.recordKeyHex)).not.toContain(MANIFEST_KEY);
  });

  it('excludes tombstones', async () => {
    const live = await liveEntries(snapshot, MANIFEST_KEY);
    expect(live.map((entry) => entry.recordKeyHex)).not.toContain('cc'.repeat(32));
  });

  it('digests the payload of each live record', async () => {
    const live = await liveEntries(snapshot, MANIFEST_KEY);
    expect(live[0].payloadSha256Hex).toBe(await payloadDigest(utf8('first')));
  });
});

describe('canonicalManifest — §7 byte layout', () => {
  it('matches a reference string built independently', async () => {
    const live = await liveEntries(snapshot, MANIFEST_KEY);
    const expected =
      `${MANIFEST_PREFIX}\x1f7\x1f2\x1f2\x1e` +
      live
        .map((e) => `${e.recordKeyHex}\x1f${e.clientTsMs}\x1f${e.payloadSha256Hex}\x1e`)
        .join('');
    expect(canonicalManifest(7, 2, live)).toEqual(utf8(expected));
  });

  it('counts n_live without the manifest record', async () => {
    const live = await liveEntries(snapshot, MANIFEST_KEY);
    const text = new TextDecoder().decode(canonicalManifest(7, 2, live));
    expect(text.split('\x1e')[0].split('\x1f')[3]).toBe('2');
  });

  it('is stable regardless of the order the server returned records in', async () => {
    const shuffled = [snapshot[2], snapshot[0], snapshot[3], snapshot[1]];
    const a = canonicalManifest(7, 2, await liveEntries(snapshot, MANIFEST_KEY));
    const b = canonicalManifest(7, 2, await liveEntries(shuffled, MANIFEST_KEY));
    expect(toHex(a)).toBe(toHex(b));
  });
});

describe('verifyManifest', () => {
  const build = async (records: VaultSnapshotRecord[], seq = 7, keyVersion = 2) =>
    buildManifest(subkeys.auth, {
      vaultSeq: seq,
      keyVersion,
      live: await liveEntries(records, MANIFEST_KEY)
    });

  it('accepts the snapshot it was built from', async () => {
    const manifest = await build(snapshot);
    expect(
      await verifyManifest(subkeys.auth, manifest, {
        vaultSeq: 7,
        keyVersion: 2,
        live: await liveEntries(snapshot, MANIFEST_KEY)
      })
    ).toBe(true);
  });

  it('fails when a live record was withheld by the server (DoD)', async () => {
    const manifest = await build(snapshot);
    const withheld = snapshot.filter((r) => r.recordKeyHex !== 'bb'.repeat(32));
    expect(
      await verifyManifest(subkeys.auth, manifest, {
        vaultSeq: 7,
        keyVersion: 2,
        live: await liveEntries(withheld, MANIFEST_KEY)
      })
    ).toBe(false);
  });

  it('passes when a compacted tombstone disappeared (DoD)', async () => {
    // Штатний компактор видаляє tombstone фізично. Якби manifest покривав
    // надгробки, кожен компакшн виглядав би як помилка цілісності — саме тоді,
    // коли користувачка приходить на повний ресинк після 410.
    const manifest = await build(snapshot);
    const compacted = snapshot.filter((r) => !r.deleted);
    expect(
      await verifyManifest(subkeys.auth, manifest, {
        vaultSeq: 7,
        keyVersion: 2,
        live: await liveEntries(compacted, MANIFEST_KEY)
      })
    ).toBe(true);
  });

  it('fails when a payload was swapped for an older version', async () => {
    const manifest = await build(snapshot);
    const rolled = snapshot.map((r) =>
      r.recordKeyHex === 'aa'.repeat(32) ? record('aa'.repeat(32), 1768435200001, 'older') : r
    );
    expect(
      await verifyManifest(subkeys.auth, manifest, {
        vaultSeq: 7,
        keyVersion: 2,
        live: await liveEntries(rolled, MANIFEST_KEY)
      })
    ).toBe(false);
  });

  it('fails when client_ts_ms was rolled back', async () => {
    const manifest = await build(snapshot);
    const rolled = snapshot.map((r) =>
      r.recordKeyHex === 'aa'.repeat(32) ? record('aa'.repeat(32), 1768435200000, 'first') : r
    );
    expect(
      await verifyManifest(subkeys.auth, manifest, {
        vaultSeq: 7,
        keyVersion: 2,
        live: await liveEntries(rolled, MANIFEST_KEY)
      })
    ).toBe(false);
  });

  it('fails when vault_seq or key_version disagree', async () => {
    const manifest = await build(snapshot);
    const live = await liveEntries(snapshot, MANIFEST_KEY);
    expect(
      await verifyManifest(subkeys.auth, manifest, { vaultSeq: 6, keyVersion: 2, live })
    ).toBe(false);
    expect(
      await verifyManifest(subkeys.auth, manifest, { vaultSeq: 7, keyVersion: 1, live })
    ).toBe(false);
  });

  it('fails under another vault key', async () => {
    const manifest = await build(snapshot);
    expect(
      await verifyManifest(other.auth, manifest, {
        vaultSeq: 7,
        keyVersion: 2,
        live: await liveEntries(snapshot, MANIFEST_KEY)
      })
    ).toBe(false);
  });

  it('fails on a truncated mac of the right prefix', async () => {
    const manifest = await build(snapshot);
    expect(
      await verifyManifest(subkeys.auth, { ...manifest, mac: manifest.mac.slice(0, 16) }, {
        vaultSeq: 7,
        keyVersion: 2,
        live: await liveEntries(snapshot, MANIFEST_KEY)
      })
    ).toBe(false);
  });
});
