// AAD конверта записів — §7 плану.
//
//   AAD = "ndv1" ‖ 0x1F ‖ шлях ‖ 0x1F ‖ client_ts_ms ‖ 0x1F ‖ ("0"|"1")
//
// 0x1F розділяє поля однозначно: проста конкатенація лишала б межі полів
// неоднозначними, а отже два різні записи могли б мати однаковий AAD.
//
// Останнє поле — прапорець надгробка: "1" ⟺ tombstone. §7 його семантики не
// давала; вибір зафіксовано тим, що AAD мусить бути відтворюваним ДО
// розшифрування — інакше клієнт перебирав би обидва значення на кожному записі.
// Єдиний булев, який клієнт має до розшифрування, — серверна колонка `deleted`.
// Змінити прапорець після першого записаного шифротексту неможливо.

import { concat, utf8, type Bytes } from './bytes';

export const AAD_VERSION = 'ndv1';
export const UNIT_SEPARATOR = 0x1f;

export const RECORD_PATH_RE =
  /^(entry:\d{4}-\d{2}-\d{2}|cycle|catalog|groups|settings|manifest)$/;

/** Шлях, який уже пройшов валідацію закритим переліком §7. */
export type RecordPath = string & { readonly __recordPath: unique symbol };

export const SINGLETON_PATHS = [
  'cycle',
  'catalog',
  'groups',
  'settings',
  'manifest'
] as const;

export class AadError extends Error {
  constructor() {
    // Без деталей: значення, що не пройшло валідацію, може бути датою запису.
    super('aad');
    this.name = 'AadError';
  }
}

export function isRecordPath(value: string): value is RecordPath {
  return RECORD_PATH_RE.test(value);
}

export function assertRecordPath(value: string): RecordPath {
  if (!isRecordPath(value)) throw new AadError();
  return value;
}

export function entryPath(iso: string): RecordPath {
  return assertRecordPath(`entry:${iso}`);
}

const separator = new Uint8Array([UNIT_SEPARATOR]);

export function buildAad(
  path: RecordPath,
  clientTsMs: number,
  tombstone: boolean
): Bytes {
  if (!Number.isSafeInteger(clientTsMs) || clientTsMs <= 0) throw new AadError();
  return concat(
    utf8(AAD_VERSION),
    separator,
    utf8(path),
    separator,
    utf8(String(clientTsMs)),
    separator,
    utf8(tombstone ? '1' : '0')
  );
}
