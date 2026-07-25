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
import { fromBase64, toBase64, toHex, utf8 } from '../crypto/bytes';
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
  /** Лічильник manifest після останнього чанка — його персистить `SyncMeta`. */
  readonly vaultSeq: number;
  /** Логічний шлях → sha256 надісланого шифротексту. */
  readonly digests: Readonly<Record<string, string>>;
  /** Логічний шлях → `client_ts`, під яким запис пішов на сервер. */
  readonly clientTs: Readonly<Record<string, number>>;
}

/** Живий запис, який цього разу не надсилається, але входить у manifest. */
export interface CarriedRecord {
  readonly sha256: string;
  readonly clientTs: number;
}

export interface UploadPlan {
  readonly baseRevision: number;
  readonly updates: readonly PlainRecord[];
  readonly tombstones: readonly { readonly path: string; readonly clientTs: number }[];
  /** Шлях → дайджест уже наявного на сервері запису (§9.5). */
  readonly carried: Readonly<Record<string, CarriedRecord>>;
  readonly vaultSeq: number;
  readonly keyVersion: number;
  readonly clientTs: number;
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
  readonly path: string;
  readonly change: EncryptedChange;
  readonly digest: string;
}

interface LiveEntry {
  readonly recordKeyHex: string;
  readonly clientTsMs: number;
  readonly payloadSha256Hex: string;
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
      path,
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

  /**
   * Надгробок не несе payload, тож і шифрувати нічого: сервер зберігає лише
   * факт видалення, а прапорець надгробка в AAD існує для того, щоб клієнт міг
   * відтворити AAD ДО розшифрування (§7).
   */
  private async sealTombstone(
    path: string,
    clientTs: number
  ): Promise<{ path: string; change: EncryptedChange }> {
    const valid = assertRecordPath(path);
    const key = await recordKey(this.deps.subkeys.index, valid);
    return {
      path: valid,
      change: {
        recordKeyHex: toHex(key),
        payloadB64: null,
        tombstone: true,
        clientTsMs: clientTs,
        byteLength: 0
      }
    };
  }

  private async sealManifest(
    live: readonly LiveEntry[],
    vaultSeq: number,
    keyVersion: number,
    clientTs: number
  ): Promise<EncryptedChange> {
    const path = assertRecordPath('manifest');
    const key = await recordKey(this.deps.subkeys.index, path);
    const entries = [...live].sort((a, b) => (a.recordKeyHex < b.recordKeyHex ? -1 : 1));

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
    return this.upload({
      baseRevision: 0,
      updates: records,
      tombstones: [],
      carried: {},
      vaultSeq: options.vaultSeq,
      keyVersion: options.keyVersion,
      clientTs: options.clientTs
    });
  }

  /** Спільний шлях повного й інкрементального вивантаження. */
  async upload(plan: UploadPlan): Promise<UploadReport> {
    const sealed: SealedRecord[] = [];
    for (const record of plan.updates) sealed.push(await this.seal(record));

    const graves: { path: string; change: EncryptedChange }[] = [];
    for (const grave of plan.tombstones) {
      graves.push(await this.sealTombstone(grave.path, grave.clientTs));
    }

    // Стан manifest на старті: те, що вже лежить на сервері й цього разу не
    // надсилається. Без цього manifest інкрементального push оголошував би
    // відсутніми всі незмінені записи.
    const live = new Map<string, LiveEntry>();
    for (const [path, carried] of Object.entries(plan.carried)) {
      const key = await recordKey(this.deps.subkeys.index, assertRecordPath(path));
      live.set(path, {
        recordKeyHex: toHex(key),
        clientTsMs: carried.clientTs,
        payloadSha256Hex: carried.sha256
      });
    }

    const byKey = new Map<string, SealedRecord>(
      sealed.map((item) => [item.change.recordKeyHex, item])
    );
    const gravesByKey = new Map<string, string>(
      graves.map((item) => [item.change.recordKeyHex, item.path])
    );

    const chunks = planChunks(
      [...sealed.map((item) => item.change), ...graves.map((item) => item.change)],
      1
    );

    let revision = plan.baseRevision;
    let sent = 0;
    let vaultSeq = plan.vaultSeq;

    const sendOne = async (chunk: readonly EncryptedChange[]): Promise<void> => {
      for (const change of chunk) {
        const update = byKey.get(change.recordKeyHex);
        if (update !== undefined) {
          live.set(update.path, {
            recordKeyHex: update.change.recordKeyHex,
            clientTsMs: update.change.clientTsMs,
            payloadSha256Hex: update.digest
          });
          continue;
        }
        const gravePath = gravesByKey.get(change.recordKeyHex);
        if (gravePath !== undefined) live.delete(gravePath);
      }
      vaultSeq += 1;
      const manifest = await this.sealManifest(
        [...live.values()],
        vaultSeq,
        plan.keyVersion,
        plan.clientTs
      );
      const outgoing = [...chunk, manifest];
      revision = await this.sendWithRetries(
        revision,
        outgoing,
        await digestsOf(outgoing, byKey)
      );
      sent += chunk.length;
    };

    for (const chunk of chunks) await sendOne(chunk);
    if (chunks.length === 0) await sendOne([]);

    return {
      chunks: Math.max(chunks.length, 1),
      records: sent,
      finalRevision: revision,
      vaultSeq,
      digests: Object.fromEntries(sealed.map((item) => [item.path, item.digest])),
      clientTs: Object.fromEntries(
        sealed.map((item) => [item.path, item.change.clientTsMs])
      )
    };
  }

  /**
   * Надсилає ОДИН і той самий масив байтів, поки не вдасться або поки не
   * вичерпаються спроби. Жодного перешифрування між спробами (§7).
   */
  private async sendWithRetries(
    baseRevision: number,
    changes: readonly EncryptedChange[],
    digests: ReadonlyMap<string, string | null>
  ): Promise<number> {
    let lastError: unknown = null;
    for (let attempt = 0; attempt < this.retries; attempt += 1) {
      try {
        const result = await this.deps.transport.push(baseRevision, changes);
        return result.newRevision;
      } catch (error) {
        lastError = error;
        if (error instanceof SyncError && error.code === 'conflict') {
          // §9.5 вимагає ідемпотентного ретраю по `record_key` + вміст. Найчастіший
          // сценарій обриву — комміт пройшов, а відповідь загубилася: повторна
          // спроба тоді бачить `revision > base_revision` і виглядає конфліктом,
          // хоча насправді сервер уже має РІВНО ті байти, які ми надіслали.
          const applied = await this.chunkAlreadyApplied(baseRevision, digests);
          if (applied !== null) return applied;
          throw error;
        }
        // Повторюємо лише те, що минеться саме: 410, reset і 413 потребують
        // іншого рішення, а не наполегливості.
        if (
          error instanceof SyncError &&
          (error.code === 'gone' ||
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

  /**
   * Чи є на сервері рівно той чанк, який ми щойно надіслали.
   *
   * Порівнюється sha256 ШИФРОТЕКСТУ, а не plaintext: збіг означає ті самі
   * байти, тобто наш власний запис, а не чужий. Розбіжність хоча б в одному
   * ключі — справжній конфлікт, і його розв'язує pull-merge-push (§9.3).
   * Перешифрування тут не відбувається ніколи (§7, правило 3).
   */
  private async chunkAlreadyApplied(
    baseRevision: number,
    digests: ReadonlyMap<string, string | null>
  ): Promise<number | null> {
    const pending = new Map(digests);
    let since = baseRevision;
    let currentRevision = baseRevision;

    for (let page = 0; page < 8 && pending.size > 0; page += 1) {
      let result;
      try {
        // `consent_epoch = 0` тут свідомо: цей pull не приймає жодних рішень
        // про згоди й ігнорує `consent_state_changed`. Рішення про pruning
        // ухвалює виключно `VaultSession` зі свіжою відповіддю `/v1/consents`.
        result = await this.deps.transport.pull(since, 0);
      } catch {
        return null;
      }
      currentRevision = result.currentRevision;
      for (const record of result.records) {
        const expected = pending.get(record.recordKeyHex);
        if (expected === undefined) continue;
        if (expected === null) {
          if (!record.tombstone) return null;
        } else {
          if (record.tombstone || record.payloadB64 === null) return null;
          if ((await payloadDigest(fromBase64(record.payloadB64))) !== expected) return null;
        }
        pending.delete(record.recordKeyHex);
      }
      if (result.nextSince <= since) break;
      since = result.nextSince;
    }

    return pending.size === 0 ? currentRevision : null;
  }
}

/**
 * `record_key` → sha256 шифротексту; `null` означає надгробок.
 *
 * Manifest теж входить: він шифрується один раз на чанк і при ретраї
 * надсилається тими самими байтами, тож його збіг — найсильніше свідчення, що
 * на сервері лежить саме наш чанк, а не чужий запис під тим самим ключем.
 */
async function digestsOf(
  changes: readonly EncryptedChange[],
  byKey: ReadonlyMap<string, SealedRecord>
): Promise<ReadonlyMap<string, string | null>> {
  const out = new Map<string, string | null>();
  for (const change of changes) {
    if (change.tombstone) {
      out.set(change.recordKeyHex, null);
      continue;
    }
    const sealed = byKey.get(change.recordKeyHex);
    out.set(
      change.recordKeyHex,
      sealed?.digest ??
        (change.payloadB64 === null
          ? null
          : await payloadDigest(fromBase64(change.payloadB64)))
    );
  }
  return out;
}
