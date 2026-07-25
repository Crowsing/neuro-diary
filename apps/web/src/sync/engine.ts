// Движок синхронізації: шифрування, чанки, ретрай і manifest.
//
// Порядок операцій тут — не деталь реалізації, а виконання §7 і §9.5:
//
//  * запис шифрується РІВНО один раз; ретрай надсилає ті самі байти, бо кожне
//    шифрування дає новий nonce, а manifest фіксує sha256 конкретних байтів;
//  * manifest перераховується й додається до КОЖНОГО чанка — інакше кожен
//    проміжний стан виглядав би як помилка цілісності;
//  * дайджести зберігаються в метаданих: в інкрементальному режимі клієнт
//    тримає plaintext, а не шифротекст, і без них не перерахував би manifest,
//    не перешифрувавши все.

import { buildAad, assertRecordPath } from '../crypto/aad';
import { toBase64, toHex, utf8 } from '../crypto/bytes';
import { encrypt } from '../crypto/envelope';
import type { Subkeys } from '../crypto/keys';
import { buildManifest, canonicalManifest, payloadDigest } from '../crypto/manifest';
import { recordKey } from '../crypto/recordKey';
import { type EncryptedChange, planChunks } from './chunks';
import { SyncError, type SyncTransport } from './client';
import type { PlainRecord } from './types';

export interface UploadReport {
  readonly chunks: number;
  readonly records: number;
  readonly finalRevision: number;
  readonly digests: Readonly<Record<string, string>>;
}

export interface EngineDeps {
  readonly transport: SyncTransport;
  readonly subkeys: Subkeys;
  readonly deviceId: string;
  /** Скільки разів повторювати чанк тими самими байтами. */
  readonly retries?: number;
}

/** Один зашифрований запис разом із дайджестом для manifest. */
interface SealedRecord {
  readonly change: EncryptedChange;
  readonly digest: string;
}

export class SyncEngine {
  private readonly retries: number;

  constructor(private readonly deps: EngineDeps) {
    this.retries = deps.retries ?? 2;
  }

  private async seal(record: PlainRecord): Promise<SealedRecord> {
    const path = assertRecordPath(record.path);
    const key = await recordKey(this.deps.subkeys.index, path);
    const aad = buildAad(path, record.clientTs, false);
    const payload = await encrypt(
      this.deps.subkeys.enc,
      utf8(JSON.stringify({ body: record.body, deviceId: record.deviceId })),
      aad
    );
    return {
      change: {
        recordKeyHex: toHex(key),
        payloadB64: toBase64(payload),
        tombstone: false,
        clientTsMs: record.clientTs,
        byteLength: payload.length
      },
      digest: await payloadDigest(payload)
    };
  }

  private async sealManifest(
    live: SealedRecord[],
    vaultSeq: number,
    keyVersion: number,
    clientTs: number
  ): Promise<EncryptedChange> {
    const path = assertRecordPath('manifest');
    const key = await recordKey(this.deps.subkeys.index, path);
    const entries = live
      .map((item) => ({
        recordKeyHex: item.change.recordKeyHex,
        clientTsMs: item.change.clientTsMs,
        payloadSha256Hex: item.digest
      }))
      .sort((a, b) => (a.recordKeyHex < b.recordKeyHex ? -1 : 1));

    const manifest = await buildManifest(this.deps.subkeys.auth, {
      vaultSeq,
      keyVersion,
      live: entries
    });
    const aad = buildAad(path, clientTs, false);
    const payload = await encrypt(
      this.deps.subkeys.enc,
      utf8(
        JSON.stringify({
          vaultSeq,
          keyVersion,
          mac: toHex(manifest.mac),
          canonicalLength: canonicalManifest(vaultSeq, keyVersion, entries).length
        })
      ),
      aad
    );
    return {
      recordKeyHex: toHex(key),
      payloadB64: toBase64(payload),
      tombstone: false,
      clientTsMs: clientTs,
      byteLength: payload.length
    };
  }

  /**
   * Повне завантаження снапшоту чанками (§9.5).
   *
   * Обрив чанка не перешифровує нічого: повторюється той самий байтовий вміст,
   * тож sha256, зафіксований у manifest, лишається дійсним.
   */
  async initialUpload(
    records: readonly PlainRecord[],
    options: { vaultSeq: number; keyVersion: number; clientTs: number }
  ): Promise<UploadReport> {
    const sealed: SealedRecord[] = [];
    for (const record of records) sealed.push(await this.seal(record));

    // Один слот кожного чанка резервується під manifest.
    const chunks = planChunks(
      sealed.map((item) => item.change),
      1
    );

    let revision = 0;
    let sent = 0;
    const uploaded: SealedRecord[] = [];
    const byKey = new Map(sealed.map((item) => [item.change.recordKeyHex, item]));

    for (const [index, chunk] of chunks.entries()) {
      for (const change of chunk) {
        const item = byKey.get(change.recordKeyHex);
        if (item !== undefined) uploaded.push(item);
      }
      const manifest = await this.sealManifest(
        uploaded,
        options.vaultSeq + index + 1,
        options.keyVersion,
        options.clientTs
      );
      revision = await this.sendWithRetries(revision, [...chunk, manifest]);
      sent += chunk.length;
    }

    if (chunks.length === 0) {
      const manifest = await this.sealManifest(
        [],
        options.vaultSeq + 1,
        options.keyVersion,
        options.clientTs
      );
      revision = await this.sendWithRetries(revision, [manifest]);
    }

    return {
      chunks: Math.max(chunks.length, 1),
      records: sent,
      finalRevision: revision,
      digests: Object.fromEntries(
        sealed.map((item) => [item.change.recordKeyHex, item.digest])
      )
    };
  }

  /**
   * Надсилає ОДИН і той самий масив байтів, поки не вдасться або поки не
   * вичерпаються спроби. Жодного перешифрування між спробами (§7).
   */
  private async sendWithRetries(
    baseRevision: number,
    changes: readonly EncryptedChange[]
  ): Promise<number> {
    let lastError: unknown = null;
    for (let attempt = 0; attempt < this.retries; attempt += 1) {
      try {
        const result = await this.deps.transport.push(baseRevision, changes);
        return result.newRevision;
      } catch (error) {
        lastError = error;
        // Повторюємо лише те, що минеться саме: конфлікт і 410 потребують
        // pull і merge, а не наполегливості.
        if (
          error instanceof SyncError &&
          (error.code === 'conflict' ||
            error.code === 'gone' ||
            error.code === 'vault_reset' ||
            error.code === 'consent_required' ||
            error.code === 'payload_too_large')
        ) {
          throw error;
        }
      }
    }
    throw lastError instanceof Error ? lastError : new SyncError('server');
  }
}
