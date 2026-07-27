// Політика нагадувань проти спільної фікстури, а не проти власної пам'яті.
//
// Межі quiet hours читаються з `fixtures/contract/quiet-hours.json` — того
// самого файлу, який читає `apps/api/tests/contract`. Якби вони були
// переписані сюди числами, тест доводив би узгодженість коду із самим собою.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
  DEFAULT_TIME,
  detectTimezone,
  isQuietHour,
  parseLocalTime,
  type QuietHoursPolicy
} from './policy';

const FIXTURE = fileURLToPath(
  new URL('../../../../fixtures/contract/quiet-hours.json', import.meta.url)
);

const fixture = JSON.parse(readFileSync(FIXTURE, 'utf-8')) as QuietHoursPolicy & {
  predicate: string;
  boundaries: Record<string, boolean>;
};

describe('quiet hours §10', () => {
  // Повний перелік меж проти спільної фікстури живе в `src/sync/fixtures.test.ts`
  // — саме його README фікстур називає web-читачем. Тут — властивості функції.

  it('реалізує саме предикат фікстури, а не «з 22 до 8»', () => {
    // Формулювання «з 22:00 до 08:00» природно читається як включно з обох
    // боків; фікстура каже `t >= start OR t < end`, і різниця — рівно 08:00.
    expect(fixture.predicate).toBe('t >= start OR t < end');
    expect(isQuietHour(fixture.end, fixture)).toBe(false);
    expect(isQuietHour(fixture.start, fixture)).toBe(true);
  });

  it('префіл §10 лежить поза тихими годинами', () => {
    expect(DEFAULT_TIME).toBe('20:00');
    expect(isQuietHour(DEFAULT_TIME, fixture)).toBe(false);
  });
});

describe('розбір локального часу', () => {
  it('приймає строгий HH:mm', () => {
    expect(parseLocalTime('00:00')).toEqual({ hours: 0, minutes: 0 });
    expect(parseLocalTime('09:15')).toEqual({ hours: 9, minutes: 15 });
    expect(parseLocalTime('23:59')).toEqual({ hours: 23, minutes: 59 });
  });

  it.each(['9:00', '24:00', '23:60', '20:00:00', '', '2000', 'ранок'])(
    'відхиляє %s — сервер відхилив би так само, але витративши вікно §11',
    (value) => {
      expect(parseLocalTime(value)).toBeNull();
    }
  );

  it('нерозбірливий час не оголошується тихою годиною', () => {
    // Інакше UI сказав би «нічний час недоступний» там, де насправді просто
    // не той формат, — і користувачка правила б не те.
    expect(isQuietHour('ранок', fixture)).toBe(false);
  });
});

describe('часовий пояс пристрою', () => {
  it('береться з Intl через порт', () => {
    expect(detectTimezone({ resolvedTimeZone: () => 'Europe/Kyiv' })).toBe(
      'Europe/Kyiv'
    );
  });

  it('порожній рядок, коли пристрій зони не називає', () => {
    // Поле лишається за користувачкою, а перелік зон і далі знає лише сервер.
    expect(
      detectTimezone({
        resolvedTimeZone: () => {
          throw new Error('no Intl');
        }
      })
    ).toBe('');
  });
});
