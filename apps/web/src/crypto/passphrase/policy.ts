// Політика парольної фрази — §7 плану.
//
// Основний і попередньо обраний шлях — фраза, згенерована застосунком.
// Власна фраза дозволена як другий варіант із жорсткими мінімумами:
// ≥12 символів І ≥3 слова; якщо слово одне — ≥16 символів.
//
// Числовий або кольоровий індикатор сили НЕ показується і тут не обчислюється:
// він створює хибне заспокоєння замість твердого правила допуску. Підказка
// (hint) не реалізується — ні серверна, ні локальна.

export const GENERATED_WORD_COUNT = 6;
export const OWN_MIN_LENGTH = 12;
export const OWN_MIN_WORDS = 3;
export const OWN_SINGLE_WORD_MIN_LENGTH = 16;

export type PassphraseRejection = 'too_short' | 'too_few_words';

export interface PassphraseVerdict {
  readonly ok: boolean;
  readonly reason?: PassphraseRejection;
}

export function countWords(value: string): number {
  return value.trim().split(/\s+/).filter(Boolean).length;
}

/**
 * Довжина рахується в кодових точках, а не в кодових одиницях UTF-16: інакше
 * фраза з емодзі проходила б поріг удвічі дешевше.
 */
export function passphraseLength(value: string): number {
  return [...value.trim().normalize('NFC')].length;
}

export function validateOwnPassphrase(value: string): PassphraseVerdict {
  const length = passphraseLength(value);
  const words = countWords(value);

  if (words <= 1) {
    return length >= OWN_SINGLE_WORD_MIN_LENGTH
      ? { ok: true }
      : { ok: false, reason: 'too_short' };
  }
  if (length < OWN_MIN_LENGTH) return { ok: false, reason: 'too_short' };
  if (words < OWN_MIN_WORDS) return { ok: false, reason: 'too_few_words' };
  return { ok: true };
}
