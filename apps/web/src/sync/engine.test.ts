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
import { SyncError, type EngineTransport, type PushResult } from './client';
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

class RecordingTransport implements EngineTransport {
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

  async authenticate() {
    return { token: 'token', consents: [] };
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

async function engineWith(
  transport: EngineTransport,
  retries = 2,
  sleep?: (milliseconds: number) => Promise<void>
): Promise<SyncEngine> {
  const subkeys = await deriveSubkeys(new Uint8Array(32).fill(0x11));
  return new SyncEngine({ transport, subkeys, deviceId: DEVICE, retries, sleep });
}

/** Записує кожну паузу замість того, щоб її витримувати. */
function recordingSleep(): {
  waits: number[];
  sleep: (milliseconds: number) => Promise<void>;
} {
  const waits: number[] = [];
  return {
    waits,
    sleep: async (milliseconds) => {
      waits.push(milliseconds);
    }
  };
}

/** Сервер, що віддає 429 задану кількість разів, а тоді приймає пуш. */
class ThrottlingTransport extends RecordingTransport {
  constructor(
    private refusalsLeft: number,
    private readonly retryAfterSeconds: number | null
  ) {
    super();
  }

  override async push(
    baseRevision: number,
    changes: readonly EncryptedChange[]
  ): Promise<PushResult> {
    if (this.refusalsLeft > 0) {
      this.refusalsLeft -= 1;
      this.pushes.push({ baseRevision, changes: [...changes] });
      throw new SyncError('rate_limited', this.retryAfterSeconds);
    }
    return super.push(baseRevision, changes);
  }
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

// §11 віддає `Retry-After` на всіх трьох шляхах, і до Фази 5 у нього не було
// споживача: цикл спроб не мав жодної паузи, тож бюджет `5 MiB/хв` робив перший
// вивантаж великого щоденника непроходимим — обидві спроби витрачались за
// мілісекунди в тому самому вікні.
describe('SyncEngine — 429 і Retry-After (§11)', () => {
  it('чекає названу сервером кількість секунд, а тоді повторює ті самі байти', async () => {
    const transport = new ThrottlingTransport(1, 7);
    const timer = recordingSleep();
    const engine = await engineWith(transport, 2, timer.sleep);

    const report = await engine.initialUpload([entry('2026-01-15')], {
      vaultSeq: 0,
      keyVersion: 1,
      clientTs: T0
    });

    expect(timer.waits).toEqual([7000]);
    expect(report.records).toBe(1);
    // Ті самі байти: перешифрування зламало б sha256, зафіксований у manifest.
    const [first, second] = transport.pushes;
    expect(second.changes.map((item) => item.payloadB64)).toEqual(
      first.changes.map((item) => item.payloadB64)
    );
  });

  it('не чекає, коли сервер не відмовляв', async () => {
    const transport = new RecordingTransport();
    const timer = recordingSleep();
    const engine = await engineWith(transport, 2, timer.sleep);

    await engine.initialUpload([entry('2026-01-15')], {
      vaultSeq: 0,
      keyVersion: 1,
      clientTs: T0
    });

    expect(timer.waits).toEqual([]);
  });

  it('обрізає названу сервером годину до стелі в шістдесят секунд', async () => {
    // Проти зламаного або ворожого сервера: без стелі один заголовок підвісив би
    // вивантаження на годину.
    const transport = new ThrottlingTransport(1, 3600);
    const timer = recordingSleep();
    const engine = await engineWith(transport, 2, timer.sleep);

    await engine.initialUpload([entry('2026-01-15')], {
      vaultSeq: 0,
      keyVersion: 1,
      clientTs: T0
    });

    expect(timer.waits).toEqual([60_000]);
  });

  it('без заголовка чекає секунду, а не нуль', async () => {
    const transport = new ThrottlingTransport(1, null);
    const timer = recordingSleep();
    const engine = await engineWith(transport, 2, timer.sleep);

    await engine.initialUpload([entry('2026-01-15')], {
      vaultSeq: 0,
      keyVersion: 1,
      clientTs: T0
    });

    expect(timer.waits).toEqual([1000]);
  });

  it('віддає 429 користувачці, коли спроби скінчились', async () => {
    // Межа лишається межею: пауза не робить бюджет нескінченним.
    const transport = new ThrottlingTransport(5, 3);
    const timer = recordingSleep();
    const engine = await engineWith(transport, 2, timer.sleep);

    await expect(
      engine.initialUpload([entry('2026-01-15')], {
        vaultSeq: 0,
        keyVersion: 1,
        clientTs: T0
      })
    ).rejects.toMatchObject({ code: 'rate_limited' });
    expect(timer.waits).toEqual([3000, 3000]);
  });
});
