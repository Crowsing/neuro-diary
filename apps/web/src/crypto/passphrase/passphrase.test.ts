import { describe, expect, it } from 'vitest';
import { WORDS } from '../wordlist/uk-4096';
import {
  type RandomSource,
  generatePassphrase,
  randomIndex
} from './generate';
import {
  GENERATED_WORD_COUNT,
  OWN_MIN_LENGTH,
  OWN_MIN_WORDS,
  OWN_SINGLE_WORD_MIN_LENGTH,
  validateOwnPassphrase
} from './policy';

/** Джерело, яке віддає наперед задані 32-бітові значення по черзі. */
function scripted(values: readonly number[]): RandomSource {
  let cursor = 0;
  return (out) => {
    out[0] = values[cursor % values.length];
    cursor += 1;
    return out;
  };
}

describe('validateOwnPassphrase — §7 thresholds', () => {
  it('pins the thresholds of the plan', () => {
    expect(OWN_MIN_LENGTH).toBe(12);
    expect(OWN_MIN_WORDS).toBe(3);
    expect(OWN_SINGLE_WORD_MIN_LENGTH).toBe(16);
    expect(GENERATED_WORD_COUNT).toBe(6);
  });

  it('accepts three words at exactly twelve characters', () => {
    expect(validateOwnPassphrase('вежа сонце дощ')).toEqual({ ok: true });
  });

  it('rejects three words at eleven characters', () => {
    expect(validateOwnPassphrase('веж сонц до')).toEqual({
      ok: false,
      reason: 'too_short'
    });
  });

  it('rejects two words however long', () => {
    expect(validateOwnPassphrase('надзвичайно довжелезна')).toEqual({
      ok: false,
      reason: 'too_few_words'
    });
  });

  it('accepts one word at exactly sixteen characters', () => {
    expect(validateOwnPassphrase('абвгдеєжзиіїйклм')).toEqual({ ok: true });
  });

  it('rejects one word at fifteen characters', () => {
    expect(validateOwnPassphrase('абвгдеєжзиіїйкл')).toEqual({
      ok: false,
      reason: 'too_short'
    });
  });

  it('ignores surrounding and repeated whitespace', () => {
    expect(validateOwnPassphrase('   вежа   сонце   дощ   ')).toEqual({ ok: true });
    expect(validateOwnPassphrase('   ')).toEqual({ ok: false, reason: 'too_short' });
  });

  it('counts code points, not UTF-16 units', () => {
    // Шість емодзі — це 12 кодових одиниць, але одне «слово» з шести символів.
    expect(validateOwnPassphrase('🙂🙂🙂🙂🙂🙂')).toEqual({
      ok: false,
      reason: 'too_short'
    });
  });
});

describe('randomIndex — rejection sampling, no modulo bias', () => {
  it('discards a draw above the largest multiple of the bound', () => {
    // 2^32 % 3 == 1, тож 0xffffffff лежить поза найбільшим кратним і мусить
    // бути відкинутий. Взяття по модулю повернуло б 0 і зсунуло розподіл.
    expect(randomIndex(3, scripted([0xffffffff, 5]))).toBe(2);
  });

  it('keeps the largest draw still below the limit', () => {
    // limit для bound=3 дорівнює 0xffffffff, тож 0xfffffffe — останнє прийнятне
    // значення, і воно дає 0xfffffffe % 3 == 2.
    expect(randomIndex(3, scripted([0xfffffffe]))).toBe(2);
  });

  it('returns every value of a small bound', () => {
    expect(randomIndex(4096, scripted([0]))).toBe(0);
    expect(randomIndex(4096, scripted([4095]))).toBe(4095);
    expect(randomIndex(4096, scripted([4096]))).toBe(0);
  });

  it('refuses a nonsensical bound', () => {
    expect(() => randomIndex(0)).toThrow();
    expect(() => randomIndex(-1)).toThrow();
    expect(() => randomIndex(1.5)).toThrow();
  });

  it('covers the whole list over many draws', () => {
    const seen = new Set<number>();
    for (let i = 0; i < 60_000; i += 1) seen.add(randomIndex(WORDS.length));
    expect(seen.size).toBeGreaterThan(WORDS.length * 0.99);
  });
});

describe('generatePassphrase', () => {
  it('returns six words by default', () => {
    const phrase = generatePassphrase(WORDS);
    expect(phrase.split(' ')).toHaveLength(GENERATED_WORD_COUNT);
  });

  it('draws every word from the list', () => {
    const list = new Set(WORDS);
    for (const word of generatePassphrase(WORDS).split(' ')) {
      expect(list.has(word)).toBe(true);
    }
  });

  it('does not repeat itself across calls', () => {
    const seen = new Set<string>();
    for (let i = 0; i < 200; i += 1) seen.add(generatePassphrase(WORDS));
    expect(seen.size).toBe(200);
  });

  it('passes its own policy for own phrases', () => {
    expect(validateOwnPassphrase(generatePassphrase(WORDS))).toEqual({ ok: true });
  });

  it('refuses a degenerate list', () => {
    expect(() => generatePassphrase([])).toThrow();
    expect(() => generatePassphrase(['вежа'])).toThrow();
  });
});
