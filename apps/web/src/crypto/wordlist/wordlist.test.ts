import { describe, expect, it } from 'vitest';
import {
  EXACT_COUNT,
  MAX_LENGTH,
  MIN_LENGTH,
  PREFIX_LENGTH,
  STOP_STEMS,
  UKRAINIAN_LOWERCASE,
  devoicedForm,
  hasAllowedLength,
  hasNoHomophones,
  hasNoMedicalStem,
  hasUniquePrefixes,
  isUkrainianLowercase
} from './filters';
import { REVIEWED_BY_LOCALIZATION_EDITOR, WORDS } from './uk-4096';

describe('filters', () => {
  it('accepts lengths 4 to 9 and nothing else', () => {
    expect(hasAllowedLength('дощ')).toBe(false);
    expect(hasAllowedLength('вежа')).toBe(true);
    expect(hasAllowedLength('дбайливий')).toBe(true);
    expect(hasAllowedLength('дбайливість')).toBe(false);
  });

  it('accepts only lowercase Ukrainian letters', () => {
    expect(isUkrainianLowercase('ґанок')).toBe(true);
    expect(isUkrainianLowercase('Вежа')).toBe(false);
    expect(isUkrainianLowercase("п'ята")).toBe(false);
    expect(isUkrainianLowercase('синьо-жовтий')).toBe(false);
    expect(isUkrainianLowercase('vezha')).toBe(false);
    expect(isUkrainianLowercase('ёлка')).toBe(false);
    expect(isUkrainianLowercase('')).toBe(false);
  });

  it('rejects medical and neurological stems', () => {
    expect(hasNoMedicalStem('мігрень')).toBe(false);
    expect(hasNoMedicalStem('лікар')).toBe(false);
    expect(hasNoMedicalStem('здоровий')).toBe(false);
    expect(hasNoMedicalStem('вежа')).toBe(true);
  });

  it('devoices the final consonant so homophones collide', () => {
    expect(devoicedForm('гриб')).toBe(devoicedForm('грип'));
    expect(devoicedForm('сад')).toBe('сат');
    expect(devoicedForm('ніж')).toBe('ніш');
    expect(devoicedForm('віз')).toBe('віс');
    expect(devoicedForm('сніг')).toBe('сніх');
    expect(devoicedForm('вежа')).toBe('вежа');
  });

  it('detects a duplicate four-letter prefix', () => {
    expect(hasUniquePrefixes(['вежа', 'вода'])).toBe(true);
    expect(hasUniquePrefixes(['вежа', 'вежами'])).toBe(false);
  });

  it('detects a homophone pair', () => {
    expect(hasNoHomophones(['гриб', 'вежа'])).toBe(true);
    expect(hasNoHomophones(['гриб', 'грип'])).toBe(false);
  });
});

describe('uk-4096 wordlist', () => {
  it('holds exactly 4096 unique words', () => {
    expect(WORDS).toHaveLength(EXACT_COUNT);
    expect(new Set(WORDS).size).toBe(EXACT_COUNT);
  });

  it('holds only words of 4 to 9 letters', () => {
    for (const word of WORDS) {
      expect(hasAllowedLength(word), word).toBe(true);
      expect(word.length).toBeGreaterThanOrEqual(MIN_LENGTH);
      expect(word.length).toBeLessThanOrEqual(MAX_LENGTH);
    }
  });

  it('holds only lowercase Ukrainian letters', () => {
    for (const word of WORDS) {
      expect(isUkrainianLowercase(word), word).toBe(true);
      for (const letter of word) expect(UKRAINIAN_LOWERCASE).toContain(letter);
    }
  });

  it('gives every word a unique four-letter prefix', () => {
    expect(hasUniquePrefixes(WORDS)).toBe(true);
    expect(new Set(WORDS.map((word) => word.slice(0, PREFIX_LENGTH))).size).toBe(
      EXACT_COUNT
    );
  });

  it('holds no word that collides with another after final devoicing', () => {
    expect(hasNoHomophones(WORDS)).toBe(true);
  });

  it('holds no medical or neurological stem', () => {
    for (const word of WORDS) {
      const hit = STOP_STEMS.find((stem) => word.includes(stem));
      expect(hit, `${word} contains ${hit}`).toBeUndefined();
    }
  });

  it('is sorted, so a review diff stays readable', () => {
    expect([...WORDS].sort()).toEqual([...WORDS]);
  });

  it('declares that it has not passed a localization review', () => {
    expect(REVIEWED_BY_LOCALIZATION_EDITOR).toBe(false);
  });
});
