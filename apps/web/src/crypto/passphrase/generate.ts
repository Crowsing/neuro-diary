// Генератор парольної фрази — §7 плану.
//
// Вибір слова — rejection sampling, а не `% bound`: остача від ділення дає
// зсув на користь перших слів списку щоразу, коли bound не ділить 2^32 націло.
// Для 4096 (степінь двійки) зсуву не було б, але правило має лишатися вірним і
// тоді, коли редактор віддасть список іншого розміру.
//
// Скільки біт дає фраза, тут не рахується і в UI не оголошується, доки словник
// не пройшов локалізаційний review (блокер 2 промпту Фази 2).

import { GENERATED_WORD_COUNT } from './policy';

export type RandomSource = (out: Uint32Array) => Uint32Array;

const defaultSource: RandomSource = (out) => crypto.getRandomValues(out);

export class WordlistError extends Error {
  constructor() {
    super('wordlist');
    this.name = 'WordlistError';
  }
}

export function randomIndex(bound: number, source: RandomSource = defaultSource): number {
  if (!Number.isSafeInteger(bound) || bound < 1 || bound > 0x1_0000_0000) {
    throw new WordlistError();
  }
  // Найбільше кратне bound, що вміщується в 2^32. Усе, що впало вище, —
  // відкидається; саме це й прибирає зсув.
  const limit = Math.floor(0x1_0000_0000 / bound) * bound;
  const buffer = new Uint32Array(1);
  for (;;) {
    const value = source(buffer)[0];
    if (value < limit) return value % bound;
  }
}

export function generatePassphrase(
  words: readonly string[],
  count: number = GENERATED_WORD_COUNT,
  source: RandomSource = defaultSource
): string {
  if (words.length < 2 || count < 1) throw new WordlistError();
  const chosen: string[] = [];
  for (let i = 0; i < count; i += 1) {
    chosen.push(words[randomIndex(words.length, source)]);
  }
  return chosen.join(' ');
}
