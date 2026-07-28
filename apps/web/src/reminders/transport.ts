// Єдине місце в apps/web, де записана адреса ресурсу нагадувань.
//
// Модуль існує окремо саме заради цього. `assert-bundle.mjs` перевіряє
// **артефакт**: доки метод із рядком `/v1/reminders/settings` жив у
// `SyncTransport`, він потрапляв у sync-бандл безумовно — тобто «вимкнено
// прапорцем» було твердженням про наміри, а не про байти. Тут шлях лежить у
// модулі, який підвантажується динамічно під статично хибним прапорцем, тож
// збірка усуває і гілку, і чанк.
//
// Мережевої механіки тут немає: токен, заголовки й переклад статусів у причини
// лишаються в `HttpSyncTransport`, який приходить сюди як `AuthedCaller`. Друга
// копія цього перекладу розійшлася б із першою на першій же новій відмові.

import type { AuthedCaller } from '../sync/client';

/**
 * Канонічний ресурс §10 — рівно п'ять полів, і жодного про доставку.
 *
 * Ані «коли надіслано», ані «скільки разів»: історія доставок — це часовий ряд
 * взаємодії людини з медичним застосунком, і API його не має за побудовою.
 */
export interface ReminderSettingsView {
  readonly enabled: boolean;
  /** Локальний час у строгому `HH:mm`; сервер ніколи не віддає секунд. */
  readonly time: string;
  readonly timezone: string;
  /** Збережений час опинився в quiet hours після зсуву політики. */
  readonly quietBlocked: boolean;
  readonly botBlocked: boolean;
}

/** §10 замінює ресурс, а не патчить його, тож усі три поля обов'язкові. */
export interface ReminderSettingsBody {
  readonly enabled: boolean;
  readonly time: string;
  readonly timezone: string;
}

/** Дві операції на одному ресурсі — і більше §10 не дозволяє жодної. */
export interface ReminderTransport {
  readReminderSettings(): Promise<ReminderSettingsView>;
  writeReminderSettings(body: ReminderSettingsBody): Promise<ReminderSettingsView>;
}

interface ReminderSettingsWire {
  enabled: boolean;
  time: string;
  timezone: string;
  quiet_blocked: boolean;
  bot_blocked: boolean;
}

const SETTINGS = '/v1/reminders/settings';

function toView(wire: ReminderSettingsWire): ReminderSettingsView {
  return {
    enabled: wire.enabled,
    time: wire.time,
    timezone: wire.timezone,
    quietBlocked: wire.quiet_blocked,
    botBlocked: wire.bot_blocked
  };
}

export function createReminderTransport(caller: AuthedCaller): ReminderTransport {
  return {
    async readReminderSettings(): Promise<ReminderSettingsView> {
      return toView(await caller.call<ReminderSettingsWire>('GET', SETTINGS));
    },
    async writeReminderSettings(
      body: ReminderSettingsBody
    ): Promise<ReminderSettingsView> {
      // Саме PUT: §10 замінює ресурс цілком, і сервер перераховує `next_fire_at`
      // від локальної дати. Частковий запис лишив би розклад, що описує час,
      // яким він уже не є. `PUT` — неспростий крос-origin запит, тож він
      // потребує preflight; що api його дозволяє, тримає
      // `test_openapi_surface.py::test_every_routed_method_survives_a_browser_preflight`.
      return toView(await caller.call<ReminderSettingsWire>('PUT', SETTINGS, body));
    }
  };
}
