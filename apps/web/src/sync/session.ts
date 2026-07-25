// Наскрізний флоу синхронізації: автентифікація, згода, ключ, вивантаження.
//
// Модуль підвантажується динамічно і лише у sync-збірці, тож у local-only
// бандлі його не існує (§9.6).
//
// Порядок кроків повторює §8 і §9.5: діалог згоди — локальний і передує будь-
// якому мережевому виклику; сесія купується initData рівно один раз; конверт
// пишеться під step-up; щоденник іде чанками з manifest у кожному.

import { deriveSubkeys, generateRoot } from '../crypto/keys';
import { deriveKek } from '../crypto/kdf';
import { defaultParams, generateSalt, toWireParams } from '../crypto/kdf/params';
import { toBase64 } from '../crypto/bytes';
import { wrapRoot } from '../crypto/wrap';
import type { AppData } from '../lib/types';
import { HttpSyncTransport, type GrantBody, type SyncTransport } from './client';
import { SyncEngine } from './engine';
import { readInitDataOnce, type BrowserEnv } from './initdata';
import { newDeviceId } from './meta';
import { toRecords } from './serialize';
import { EMPTY_JOURNALS } from './types';

export interface EnableResult {
  readonly revision: number;
  readonly deviceId: string;
}

export function createTransport(origin: string): HttpSyncTransport {
  return new HttpSyncTransport(origin);
}

/**
 * Увімкнення синхронізації з нуля: згода вже отримана локально, фраза вже
 * підтверджена. Усе, що лишилося, — мережа й криптографія.
 */
export async function enableSync(options: {
  transport: SyncTransport;
  browser: BrowserEnv;
  grant: GrantBody;
  passphrase: string;
  data: AppData;
  nowMs: number;
  deviceId?: string;
}): Promise<EnableResult> {
  const initData = readInitDataOnce(options.browser);
  if (initData === null) throw new Error('no_init_data');

  // Одна транзакція §8: акаунт створюється разом із першою згодою, тож
  // скасування діалогу до цього моменту не лишає на сервері нічого.
  await options.transport.authenticate(initData, options.grant);

  const root = generateRoot();
  const params = defaultParams(generateSalt());
  const kek = await deriveKek(options.passphrase, params);
  const wrapped = await wrapRoot(kek, root, params);
  const wire = toWireParams(params);

  await options.transport.writeKey({
    mode: 'rewrap',
    expected_wrap_version: 0,
    wrapped_dek: toBase64(wrapped),
    kdf: wire.kdf,
    kdf_params: wire.params
  });

  const deviceId = options.deviceId ?? newDeviceId();
  const subkeys = await deriveSubkeys(root);
  const engine = new SyncEngine({ transport: options.transport, subkeys, deviceId });
  const report = await engine.initialUpload(
    toRecords(options.data, EMPTY_JOURNALS, deviceId, options.nowMs),
    { vaultSeq: 0, keyVersion: 1, clientTs: options.nowMs }
  );

  return { revision: report.finalRevision, deviceId };
}
