import { describe, expect, it } from 'vitest';
import { emptyCtx } from '../lib/checkin';
import { deriveSubkeys } from '../crypto/keys';
import type { DoneEntry } from '../lib/types';
import {
  MAX_BYTES_PER_CHUNK,
  MAX_RECORDS_PER_CHUNK,
  type EncryptedChange,
  planChunks
} from './chunks';
import { SyncError, type PushResult, type SyncTransport } from './client';
import { SyncEngine } from './engine';
import type { PlainRecord } from './types';

const T0 = 1_768_435_200_000;
const DEVICE = 'aaaa0000';

function change(bytes: number, key = 'aa'): EncryptedChange {
  return {
    recordKeyHex: key.repeat(32).slice(0, 64),
    payloadB64: 'x',
    tombstone: false,
    clientTsMs: T0,
    byteLength: bytes
  };
}

function entry(iso: string): PlainRecord {
  const body: DoneEntry = {
    status: 'done',
    wb: 3,
    sym: {},
    absent: [],
    ctx: emptyCtx(),
    note: 'нотатка',
    flare: null,
    noSymptoms: false,
    filledLater: false
  };
  return {
    path: `entry:${iso}`,
    clientTs: T0,
    deviceId: DEVICE,
    body: { kind: 'entry', entry: body }
  };
}

class RecordingTransport implements SyncTransport {
  readonly pushes: { baseRevision: number; changes: EncryptedChange[] }[] = [];
  failuresLeft = 0;
  revision = 0;

  async push(
    baseRevision: number,
    changes: readonly EncryptedChange[]
  ): Promise<PushResult> {
    this.pushes.push({ baseRevision, changes: [...changes] });
    if (this.failuresLeft > 0) {
      this.failuresLeft -= 1;
      throw new SyncError('offline');
    }
    this.revision += changes.length;
    return { newRevision: this.revision };
  }

  async authenticate(): Promise<string> {
    return 'token';
  }
  async listConsents() {
    return [];
  }
  async grantConsent() {
    return [];
  }
  async pull() {
    return {
      records: [],
      nextSince: 0,
      currentRevision: 0,
      reset: false,
      consentStateChanged: false
    };
  }
  async readKey() {
    return null;
  }
  async writeKey() {
    return { keyVersion: 1, wrapVersion: 1 };
  }
  async vaultReset() {
    return { newRevision: 1 };
  }
  async revokeOtherSessions() {
    return { revoked: 0 };
  }
}

async function engineWith(transport: SyncTransport, retries = 2): Promise<SyncEngine> {
  const subkeys = await deriveSubkeys(new Uint8Array(32).fill(0x11));
  return new SyncEngine({ transport, subkeys, deviceId: DEVICE, retries });
}

describe('planChunks — the limits of §9.5', () => {
  it('never exceeds two hundred records', () => {
    const chunks = planChunks(Array.from({ length: 450 }, () => change(10)));
    expect(chunks).toHaveLength(3);
    for (const chunk of chunks) {
      expect(chunk.length).toBeLessThanOrEqual(MAX_RECORDS_PER_CHUNK);
    }
  });

  it('reserves room for the manifest when asked', () => {
    const chunks = planChunks(Array.from({ length: 400 }, () => change(10)), 1);
    expect(chunks[0]).toHaveLength(MAX_RECORDS_PER_CHUNK - 1);
  });

  it('never exceeds one mebibyte', () => {
    const chunks = planChunks(
      Array.from({ length: 40 }, () => change(64 * 1024))
    );
    for (const chunk of chunks) {
      const bytes = chunk.reduce((sum, item) => sum + item.byteLength, 0);
      expect(bytes).toBeLessThanOrEqual(MAX_BYTES_PER_CHUNK);
    }
  });

  it('keeps a single oversized record in its own chunk rather than dropping it', () => {
    const chunks = planChunks([change(10), change(MAX_BYTES_PER_CHUNK + 1)]);
    expect(chunks).toHaveLength(2);
    expect(chunks[1]).toHaveLength(1);
  });

  it('returns nothing for nothing', () => {
    expect(planChunks([])).toEqual([]);
  });
});

describe('initialUpload', () => {
  it('updates the manifest in every chunk, not once at the end', async () => {
    const transport = new RecordingTransport();
    const engine = await engineWith(transport);
    const records = Array.from({ length: 400 }, (_, index) =>
      entry(`2026-01-${String((index % 28) + 1).padStart(2, '0')}`)
    ).map((record, index) => ({ ...record, path: `entry:2026-${String((index % 12) + 1).padStart(2, '0')}-${String((index % 28) + 1).padStart(2, '0')}` }));

    const report = await engine.initialUpload(records, {
      vaultSeq: 0,
      keyVersion: 1,
      clientTs: T0
    });

    expect(report.chunks).toBeGreaterThan(1);
    expect(transport.pushes).toHaveLength(report.chunks);
    // Останній запис кожного чанка — manifest, і він у кожному з них.
    const manifestKeys = new Set(
      transport.pushes.map((push) => push.changes[push.changes.length - 1].recordKeyHex)
    );
    expect(manifestKeys.size).toBe(1);
  });

  it('sends a byte-identical payload when a chunk is retried', async () => {
    const transport = new RecordingTransport();
    transport.failuresLeft = 1;
    const engine = await engineWith(transport, 3);

    await engine.initialUpload([entry('2026-01-15')], {
      vaultSeq: 0,
      keyVersion: 1,
      clientTs: T0
    });

    expect(transport.pushes.length).toBeGreaterThanOrEqual(2);
    const [first, second] = transport.pushes;
    expect(second.changes.map((item) => item.payloadB64)).toEqual(
      first.changes.map((item) => item.payloadB64)
    );
  });

  it('encrypts each record exactly once', async () => {
    const transport = new RecordingTransport();
    transport.failuresLeft = 1;
    const engine = await engineWith(transport, 3);

    await engine.initialUpload([entry('2026-01-15'), entry('2026-01-16')], {
      vaultSeq: 0,
      keyVersion: 1,
      clientTs: T0
    });

    const payloads = transport.pushes.flatMap((push) =>
      push.changes.map((item) => item.payloadB64)
    );
    // Кожен унікальний payload повторюється рівно стільки разів, скільки було
    // спроб — тобто нового шифротексту між спробами не з'явилося.
    expect(new Set(payloads).size).toBe(3);
  });

  it('keeps a digest for every record so the manifest survives a restart', async () => {
    const transport = new RecordingTransport();
    const engine = await engineWith(transport);

    const report = await engine.initialUpload([entry('2026-01-15')], {
      vaultSeq: 0,
      keyVersion: 1,
      clientTs: T0
    });

    expect(Object.keys(report.digests)).toHaveLength(1);
    for (const digest of Object.values(report.digests)) {
      expect(digest).toMatch(/^[0-9a-f]{64}$/);
    }
  });

  it('still refuses a real conflict — one whose content is not ours', async () => {
    class ConflictingTransport extends RecordingTransport {
      override async push(): Promise<PushResult> {
        throw new SyncError('conflict', null, ['aa'.repeat(32)]);
      }
    }
    const engine = await engineWith(new ConflictingTransport(), 5);

    await expect(
      engine.initialUpload([entry('2026-01-15')], {
        vaultSeq: 0,
        keyVersion: 1,
        clientTs: T0
      })
    ).rejects.toMatchObject({ code: 'conflict' });
  });

  it('uploads a manifest even for an empty diary', async () => {
    const transport = new RecordingTransport();
    const engine = await engineWith(transport);

    const report = await engine.initialUpload([], {
      vaultSeq: 0,
      keyVersion: 1,
      clientTs: T0
    });

    expect(report.records).toBe(0);
    expect(transport.pushes).toHaveLength(1);
    expect(transport.pushes[0].changes).toHaveLength(1);
  });
});
