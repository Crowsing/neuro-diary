// Клієнтська політика нагадувань — §10.
//
// Тут немає жодного рішення: діапазон quiet hours ухвалений Gate D і живе в
// `app/domain/reminders.py`, а `fixtures/contract/quiet-hours.json` — його
// експорт. Цей модуль лише **читає** політику, передану ззовні, і саме тому
// приймає її параметром: константа `22:00` у коді web була б другою копією
// рішення, а §10 вимагає рівно однієї.
//
// Авторитет усе одно на сервері. Клієнт зупиняє лише очевидне сміття, щоб не
// витрачати вікно §11 на запит, який заздалегідь приречений, — і ніколи не
// показує «збережено» замість серверної відповіді.
//
// Чистий модуль над інжектованим портом: `vitest` працює в `environment: node`,
// без jsdom, тож `Intl` приходить сюди аргументом, а не з `globalThis`.

/** Політика §10 у тому вигляді, у якому її експортує фікстура. */
export interface QuietHoursPolicy {
  readonly start: string;
  readonly end: string;
}

/**
 * Префіл пікера, названий §10.
 *
 * Це саме префіл, а не серверний дефолт: провізія без явного значення
 * відхиляється (`test_reminder_settings.py`), тож значення завжди приходить
 * від користувачки, навіть якщо вона його не міняла.
 */
export const DEFAULT_TIME = '20:00';

/** Той самий патерн, що `TIME_PATTERN` у `app/schemas/reminders.py`. */
const TIME_PATTERN = /^([01]\d|2[0-3]):[0-5]\d$/;

export interface LocalTime {
  readonly hours: number;
  readonly minutes: number;
}

/**
 * Розбирає строгий `HH:mm` — і нічого іншого.
 *
 * `9:00` без нуля, `24:00` і `20:00:00` відхиляються тут, бо сервер відхилить
 * їх однаково, але вже витративши вікно §11.
 */
export function parseLocalTime(value: string): LocalTime | null {
  if (!TIME_PATTERN.test(value)) return null;
  return {
    hours: Number.parseInt(value.slice(0, 2), 10),
    minutes: Number.parseInt(value.slice(3, 5), 10)
  };
}

function minutesOfDay(value: LocalTime): number {
  return value.hours * 60 + value.minutes;
}

/**
 * Предикат фікстури дослівно: `t >= start OR t < end`.
 *
 * Межі несиметричні навмисно, і саме на них ламаються переписані «з голови»
 * версії: 08:00 дозволено, 07:59 ні; 21:59 дозволено, 22:00 ні.
 * Нерозбірливий час не є тихою годиною — він просто невалідний, і про це
 * говорить `parseLocalTime`, а не ця функція.
 */
export function isQuietHour(value: string, policy: QuietHoursPolicy): boolean {
  const time = parseLocalTime(value);
  const start = parseLocalTime(policy.start);
  const end = parseLocalTime(policy.end);
  if (time === null || start === null || end === null) return false;
  return minutesOfDay(time) >= minutesOfDay(start) || minutesOfDay(time) < minutesOfDay(end);
}

/** Порт браузерного `Intl`, щоб модуль лишався чистим. */
export interface TimezoneEnv {
  resolvedTimeZone(): string;
}

/**
 * Зона пристрою як префіл поля.
 *
 * Валідність перевіряє сервер проти запіненої `tzdata` (§10, `zone_for`), тож
 * тут немає жодного переліку зон: власний список web розійшовся б із серверним
 * рівно тоді, коли Telegram оновить клієнт, а ми — ні. Порожнє значення
 * означає «не змогли визначити», і поле лишається за користувачкою.
 */
export function detectTimezone(env: TimezoneEnv): string {
  try {
    return env.resolvedTimeZone();
  } catch {
    return '';
  }
}
