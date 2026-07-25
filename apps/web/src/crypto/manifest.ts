// Anti-rollback manifest — §7 плану.
//
// Manifest — шифрований синглтон під тим самим DEK, який оновлюється в кожному
// push. Він містить монотонний клієнтський `vault_seq` і HMAC(k_auth) над
// станом сейфа, розширений дайджестом вмісту кожного запису. Канонічний рядок:
//
//   "ndv1-manifest" ‖ 0x1F ‖ vault_seq ‖ 0x1F ‖ key_version ‖ 0x1F ‖ n_live ‖ 0x1E
//   далі для кожного запису в порядку зростання record_key:
//   record_key_hex ‖ 0x1F ‖ client_ts_ms ‖ 0x1F ‖ sha256(payload) ‖ 0x1E
//
// Manifest визначається над ЖИВИМИ записами (deleted = false), і сам запис
// `manifest` не входить ані до переліку, ані до n_live. Це не деталь: якби
// manifest покривав надгробки, штатний компактор щоразу робив би серверний
// набір строгою підмножиною зафіксованого — і користувачка бачила б «помилку
// цілісності» без жодної атаки, рівно тоді, коли приходить на повний ресинк
// після 410. Приховування tombstone сервером закрите іншими механізмами:
// правилом авторитетності присутності (§9.4) і push-гейтом
// base_revision < compacted_up_to.

import { type Bytes, equalConstantTime, toHex, utf8 } from './bytes';

export const MANIFEST_PREFIX = 'ndv1-manifest';
const UNIT = '\x1f';
const RECORD = '\x1e';

/** Запис у тому вигляді, у якому його віддає сервер (payload — шифротекст). */
export interface VaultSnapshotRecord {
  readonly recordKeyHex: string;
  readonly clientTsMs: number;
  readonly payload: Bytes | null;
  readonly deleted: boolean;
}

export interface ManifestEntry {
  readonly recordKeyHex: string;
  readonly clientTsMs: number;
  readonly payloadSha256Hex: string;
}

export interface ManifestState {
  readonly vaultSeq: number;
  readonly keyVersion: number;
  readonly live: readonly ManifestEntry[];
}

export interface Manifest {
  readonly vaultSeq: number;
  readonly keyVersion: number;
  readonly mac: Bytes;
}

export async function payloadDigest(payload: Bytes): Promise<string> {
  return toHex(new Uint8Array(await crypto.subtle.digest('SHA-256', payload)));
}

export async function liveEntries(
  records: readonly VaultSnapshotRecord[],
  manifestKeyHex: string
): Promise<ManifestEntry[]> {
  const live: ManifestEntry[] = [];
  for (const item of records) {
    if (item.deleted || item.payload === null) continue;
    if (item.recordKeyHex === manifestKeyHex) continue;
    live.push({
      recordKeyHex: item.recordKeyHex,
      clientTsMs: item.clientTsMs,
      payloadSha256Hex: await payloadDigest(item.payload)
    });
  }
  // Порядок зростання record_key. Ключі однакової довжини, тож лексикографічний
  // порядок hex збігається з байтовим — це закріплено тестом стабільності.
  live.sort((a, b) => (a.recordKeyHex < b.recordKeyHex ? -1 : 1));
  return live;
}

export function canonicalManifest(
  vaultSeq: number,
  keyVersion: number,
  live: readonly ManifestEntry[]
): Bytes {
  let text = `${MANIFEST_PREFIX}${UNIT}${vaultSeq}${UNIT}${keyVersion}${UNIT}${live.length}${RECORD}`;
  for (const entry of live) {
    text += `${entry.recordKeyHex}${UNIT}${entry.clientTsMs}${UNIT}${entry.payloadSha256Hex}${RECORD}`;
  }
  return utf8(text);
}

export async function manifestMac(auth: CryptoKey, canonical: Bytes): Promise<Bytes> {
  return new Uint8Array(await crypto.subtle.sign('HMAC', auth, canonical));
}

export async function buildManifest(
  auth: CryptoKey,
  state: ManifestState
): Promise<Manifest> {
  return {
    vaultSeq: state.vaultSeq,
    keyVersion: state.keyVersion,
    mac: await manifestMac(
      auth,
      canonicalManifest(state.vaultSeq, state.keyVersion, state.live)
    )
  };
}

export async function verifyManifest(
  auth: CryptoKey,
  manifest: Manifest,
  state: ManifestState
): Promise<boolean> {
  if (manifest.vaultSeq !== state.vaultSeq) return false;
  if (manifest.keyVersion !== state.keyVersion) return false;
  const expected = await manifestMac(
    auth,
    canonicalManifest(state.vaultSeq, state.keyVersion, state.live)
  );
  return equalConstantTime(expected, manifest.mac);
}
