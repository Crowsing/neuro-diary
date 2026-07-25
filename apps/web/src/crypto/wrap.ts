// Обгортка кореневого секрету — §7 плану.
//
// На сервері у `vault_key.wrapped_dek` лежить саме R під KEK. Параметри KDF
// біндяться в AAD конверта явним 0x1F-кодуванням: інакше сервер міг би
// віддати ті самі байти конверта з переписаними (слабшими) kdf_params, і
// клієнт вивів би KEK за чужим правилом, не помітивши підміни.

import { type Bytes, concat, utf8 } from './bytes';
import { decrypt, encrypt } from './envelope';
import { type KdfParams, encodeKdfParams } from './kdf/params';
import { ROOT_BYTES } from './keys';

export const WRAP_AAD_PREFIX = 'ndv1-wrap';

export class WrapError extends Error {
  constructor() {
    super('wrap');
    this.name = 'WrapError';
  }
}

function wrapAad(params: KdfParams): Bytes {
  return concat(utf8(WRAP_AAD_PREFIX), new Uint8Array([0x1f]), encodeKdfParams(params));
}

export async function wrapRoot(
  kek: CryptoKey,
  root: Bytes,
  params: KdfParams
): Promise<Bytes> {
  if (root.length !== ROOT_BYTES) throw new WrapError();
  return encrypt(kek, root, wrapAad(params));
}

export async function unwrapRoot(
  kek: CryptoKey,
  wrapped: Bytes,
  params: KdfParams
): Promise<Bytes> {
  const root = await decrypt(kek, wrapped, wrapAad(params));
  if (root.length !== ROOT_BYTES) throw new WrapError();
  return root;
}
