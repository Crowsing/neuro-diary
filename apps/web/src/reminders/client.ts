// Операції над ресурсом нагадувань — чисті функції над портом сесії.
//
// Порт, а не транспорт: сесію володіє `VaultSession`, бо initData купує рівно
// одну (anti-replay §8). Тут немає ані fetch, ані URL — і саме тому модуль
// тестується в `environment: node` без jsdom.
//
// Головна робота цього файлу — **розрізнити відмови, які HTTP-статус зливає в
// одну**. `SyncError.code` зводить 404 до `no_vault_key`, а обидва 422 — до
// `server`; для сейфа цього вистачало, для нагадувань ні: `no_schedule`,
// `quiet_hours_violation` і `unknown_timezone` ведуть до трьох різних дій
// користувачки. Розрізняє їх серверний ASCII-код (§11), а українська копія
// живе в UI.

import { SyncError, type GrantBody } from '../sync/client';
import { isQuietHour, parseLocalTime, type QuietHoursPolicy } from './policy';
import type {
  ReminderSettingsBody,
  ReminderSettingsView,
  ReminderTransport
} from './transport';

export type ReminderFailure =
  | 'offline'
  /** Сесії немає: initData одноразовий, і вкладку перезавантажили (§8). */
  | 'unauthenticated'
  | 'consent_required'
  | 'no_schedule'
  | 'quiet_hours'
  | 'unknown_timezone'
  /** Тексти згод не заморожені — fail-closed guard сервера, а не збій. */
  | 'copy_not_frozen'
  | 'rate_limited'
  /** Відхилено на пристрої: сервер відповів би так само, але витративши вікно. */
  | 'invalid_time'
  | 'server';

export class ReminderError extends Error {
  constructor(readonly failure: ReminderFailure) {
    // Жодних деталей у повідомленні: усе, що тут можна було б сказати, або
    // називає згоду, або називає розклад (§11).
    super(failure);
    this.name = 'ReminderError';
  }
}

/**
 * Сесія від сейфа плюс дві операції ресурсу — складені разом у провайдері.
 *
 * Саме складені, а не успадковані: `ensureSession` належить `VaultSession` (бо
 * сесія одна на вкладку), а читання й запис розкладу — модулю, який усувається
 * прапорцем. Порт — це шов між ними.
 */
export interface RemindersPort extends ReminderTransport {
  ensureSession(grant?: GrantBody): Promise<readonly string[]>;
}

/** Серверний код → причина з власним текстом. Порядок: код, потім статус. */
const FAILURE_BY_SERVER_CODE: Readonly<Record<string, ReminderFailure>> = {
  no_schedule: 'no_schedule',
  quiet_hours_violation: 'quiet_hours',
  unknown_timezone: 'unknown_timezone',
  consent_required: 'consent_required',
  consent_copy_not_frozen: 'copy_not_frozen',
  rate_limited: 'rate_limited',
  auth_invalid: 'unauthenticated',
  auth_stale: 'unauthenticated',
  auth_replay: 'unauthenticated',
  step_up_required: 'unauthenticated',
  no_account: 'consent_required'
};

const FAILURE_BY_CODE: Readonly<Record<string, ReminderFailure>> = {
  offline: 'offline',
  unauthenticated: 'unauthenticated',
  step_up_required: 'unauthenticated',
  consent_required: 'consent_required',
  no_account: 'consent_required',
  rate_limited: 'rate_limited'
};

export function asReminderFailure(error: unknown): ReminderFailure {
  if (error instanceof ReminderError) return error.failure;
  // `VaultError` розпізнається за формою, а не через `instanceof`, і це не
  // недбалість: імпорт класу затягнув би `sync/vault.ts` — разом із argon2 і
  // всім сейфом — у чанк, який має лишатися малим. Той самий прийом уже живе в
  // `sync/provider.tsx`.
  const failure = (error as { failure?: unknown }).failure;
  if (typeof failure === 'string') {
    return failure === 'unauthenticated' ? 'unauthenticated' : 'server';
  }
  if (error instanceof SyncError) {
    if (error.serverCode !== null) {
      const named = FAILURE_BY_SERVER_CODE[error.serverCode];
      if (named !== undefined) return named;
    }
    return FAILURE_BY_CODE[error.code] ?? 'server';
  }
  return 'server';
}

/**
 * Локальна відсіч перед мережею.
 *
 * Дві причини, і жодна з них не «валідація на клієнті замість сервера»:
 * відмова за лімітом §11 витрачається **в дверях**, до читання згоди, тож
 * запит, приречений на 422, коштує користувачці вікно; і відповідь на очевидно
 * невалідне значення має бути миттєвою, а не через круговий шлях.
 *
 * Авторитет лишається серверним: усе, що пройшло тут, усе одно перевіряється
 * там, і саме серверна відповідь потрапляє на екран.
 */
function refuseObviousGarbage(
  body: ReminderSettingsBody,
  policy: QuietHoursPolicy
): void {
  if (parseLocalTime(body.time) === null) throw new ReminderError('invalid_time');
  if (isQuietHour(body.time, policy)) throw new ReminderError('quiet_hours');
  if (body.timezone.trim() === '') throw new ReminderError('unknown_timezone');
}

/**
 * Читання канонічного ресурсу — разом із сесією, якої ще може не бути.
 *
 * `ensureSession` тут обов'язковий і був пропущений у першій редакції: екран
 * відкривався, одразу йшов у `GET /v1/reminders/settings` без заголовка
 * `Authorization` і діставав 401. Локально це виглядало як «щось не так із
 * нагадуваннями», а справжня причина — що сесію ніхто не відкрив.
 *
 * `no_account` (403) на цьому шляху не помилка: акаунта немає, отже й згоди
 * немає, і виклик мапиться в `consent_required` — той самий стан, що «ще не
 * вмикали».
 */
export async function loadReminders(
  port: RemindersPort
): Promise<ReminderSettingsView> {
  try {
    await port.ensureSession();
    return await port.readReminderSettings();
  } catch (error) {
    throw new ReminderError(asReminderFailure(error));
  }
}

/**
 * Перше ввімкнення: згода і розклад одним рухом.
 *
 * `settings` обов'язкові рівно для `telegram_reminders` (перехресний валідатор
 * `GrantInput`), тож час і зона їдуть у самому grant — окремого «спершу згода,
 * потім розклад» не існує, і це добре: між ними не буває вікна, у якому згода
 * є, а розкладу немає.
 */
export async function enableReminders(
  port: RemindersPort,
  grant: Omit<GrantBody, 'settings'>,
  body: ReminderSettingsBody,
  policy: QuietHoursPolicy
): Promise<ReminderSettingsView> {
  refuseObviousGarbage(body, policy);
  try {
    await port.ensureSession({
      ...grant,
      settings: { time: body.time, timezone: body.timezone }
    });
    // Відповідь на grant — перелік згод, а не розклад. Канонічний ресурс
    // читається окремо, бо показувати треба те, що записав сервер.
    return await port.readReminderSettings();
  } catch (error) {
    throw new ReminderError(asReminderFailure(error));
  }
}

/** Заміна ресурсу цілком: §10 не патчить. */
export async function saveReminders(
  port: RemindersPort,
  body: ReminderSettingsBody,
  policy: QuietHoursPolicy
): Promise<ReminderSettingsView> {
  // Пауза не проходить перевірку часу як «намір надсилати»: §10 зберігає
  // збережений час, і вимкнення з часом у тихих годинах — це штатний шлях
  // виходу зі стану `quiet_blocked`, а не спроба надіслати вночі.
  if (body.enabled) refuseObviousGarbage(body, policy);
  else if (parseLocalTime(body.time) === null) throw new ReminderError('invalid_time');
  try {
    return await port.writeReminderSettings(body);
  } catch (error) {
    throw new ReminderError(asReminderFailure(error));
  }
}
