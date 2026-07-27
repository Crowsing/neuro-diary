// Клієнт нагадувань проти двійника сесії.
//
// Предмет тесту — саме розрізнення відмов. HTTP зливає їх у грубі класи
// (404 → `no_vault_key`, обидва 422 → `server`), а користувачці треба сказати
// три різні речі, тож помилка мапінгу виглядала б як «щось пішло не так» —
// найдорожчий різновид дефекту, бо він не падає.

import { describe, expect, it } from 'vitest';
import { SyncError, type GrantBody } from '../sync/client';
import type { ReminderSettingsBody, ReminderSettingsView } from './transport';
import { VaultError } from '../sync/vault';
import {
  asReminderFailure,
  enableReminders,
  loadReminders,
  ReminderError,
  saveReminders,
  type RemindersPort
} from './client';
import type { QuietHoursPolicy } from './policy';

const POLICY: QuietHoursPolicy = { start: '22:00', end: '08:00' };

const GRANT: Omit<GrantBody, 'settings'> = {
  kind: 'telegram_reminders',
  text_version: 'telegram_reminders@0.9',
  text_sha256: 'a'.repeat(64)
};

const SETTINGS: ReminderSettingsBody = {
  enabled: true,
  time: '20:00',
  timezone: 'Europe/Kyiv'
};

const VIEW: ReminderSettingsView = {
  enabled: true,
  time: '20:00',
  timezone: 'Europe/Kyiv',
  quietBlocked: false,
  botBlocked: false
};

/** Двійник сесії: запам'ятовує виклики й віддає задану відповідь чи відмову. */
class PortDouble implements RemindersPort {
  readonly calls: string[] = [];
  grants: GrantBody[] = [];
  saved: ReminderSettingsBody[] = [];
  failWith: unknown = null;

  async ensureSession(grant?: GrantBody): Promise<readonly string[]> {
    this.calls.push('ensureSession');
    if (grant !== undefined) this.grants.push(grant);
    if (this.failWith !== null) throw this.failWith;
    return ['telegram_reminders'];
  }

  async readReminderSettings(): Promise<ReminderSettingsView> {
    this.calls.push('reminders');
    if (this.failWith !== null) throw this.failWith;
    return VIEW;
  }

  async writeReminderSettings(body: ReminderSettingsBody): Promise<ReminderSettingsView> {
    this.calls.push('saveReminders');
    this.saved.push(body);
    if (this.failWith !== null) throw this.failWith;
    return { ...VIEW, ...body, quietBlocked: false, botBlocked: false };
  }
}

/** Відмова рівно тієї форми, яку збирає `HttpSyncTransport.call`. */
function refusal(status: number, serverCode: string): SyncError {
  const code =
    status === 404
      ? 'no_vault_key'
      : status === 403
        ? 'consent_required'
        : status === 429
          ? 'rate_limited'
          : status === 401
            ? 'unauthenticated'
            : 'server';
  return new SyncError(code, null, [], serverCode);
}

describe('розрізнення серверних відмов', () => {
  it('404 на цьому шляху — «немає розкладу», а не «немає конверта ключа»', () => {
    // Той самий статус, той самий `code`, різні наслідки: конверт створюють
    // парольною фразою, розклад — згодою.
    expect(asReminderFailure(refusal(404, 'no_schedule'))).toBe('no_schedule');
    expect(asReminderFailure(new SyncError('no_vault_key'))).toBe('server');
  });

  it('два різні 422 не зливаються в одну відмову', () => {
    expect(asReminderFailure(refusal(422, 'quiet_hours_violation'))).toBe('quiet_hours');
    expect(asReminderFailure(refusal(422, 'unknown_timezone'))).toBe('unknown_timezone');
  });

  it('503 про незаморожений текст називається своїм ім’ям', () => {
    // Сказати тут «не вдалося» означало б сховати єдину справжню причину, з
    // якої функція недоступна поза стендом.
    expect(asReminderFailure(refusal(503, 'consent_copy_not_frozen'))).toBe(
      'copy_not_frozen'
    );
  });

  it('усі три коди 401 §8 ведуть до перезапуску через кнопку бота', () => {
    for (const code of ['auth_invalid', 'auth_stale', 'auth_replay']) {
      expect(asReminderFailure(refusal(401, code))).toBe('unauthenticated');
    }
  });

  it('відсутній initData приходить як VaultError і не стає «server»', () => {
    expect(asReminderFailure(new VaultError('unauthenticated'))).toBe('unauthenticated');
  });

  it('невідомий серверний код деградує до server, а не кидає', () => {
    expect(asReminderFailure(refusal(500, 'нове_щось'))).toBe('server');
    expect(asReminderFailure(new Error('boom'))).toBe('server');
  });

  it('обрив мережі лишається offline: щоденник працює далі', () => {
    expect(asReminderFailure(new SyncError('offline'))).toBe('offline');
  });
});

describe('увімкнення нагадувань', () => {
  it('несе час і зону в самому grant — вікна «згода є, розкладу немає» не буває', async () => {
    const port = new PortDouble();

    const view = await enableReminders(port, GRANT, SETTINGS, POLICY);

    expect(port.grants).toEqual([
      { ...GRANT, settings: { time: '20:00', timezone: 'Europe/Kyiv' } }
    ]);
    // Канонічний ресурс читається окремо: відповідь на grant — перелік згод.
    expect(port.calls).toEqual(['ensureSession', 'reminders']);
    expect(view).toEqual(VIEW);
  });

  it('не витрачає вікно §11 на час у тихих годинах', async () => {
    const port = new PortDouble();

    await expect(
      enableReminders(port, GRANT, { ...SETTINGS, time: '23:00' }, POLICY)
    ).rejects.toMatchObject({ failure: 'quiet_hours' });

    expect(port.calls).toEqual([]);
  });

  it('не витрачає вікно §11 на нерозбірливий час', async () => {
    const port = new PortDouble();

    await expect(
      enableReminders(port, GRANT, { ...SETTINGS, time: '9:00' }, POLICY)
    ).rejects.toMatchObject({ failure: 'invalid_time' });

    expect(port.calls).toEqual([]);
  });
});

describe('збереження розкладу', () => {
  it('замінює ресурс цілком', async () => {
    const port = new PortDouble();

    const view = await saveReminders(
      port,
      { enabled: true, time: '09:15', timezone: 'Europe/Lisbon' },
      POLICY
    );

    expect(port.saved).toEqual([
      { enabled: true, time: '09:15', timezone: 'Europe/Lisbon' }
    ]);
    expect(view.time).toBe('09:15');
  });

  it('пауза проходить навіть із часом у тихих годинах', async () => {
    // §10 зберігає час і при вимкненні, а `quiet_blocked` — це стан, з якого
    // мусить бути вихід. Локальна відсіч, що не пускає паузу, замкнула б
    // користувачку в стані, який вона хоче припинити.
    const port = new PortDouble();

    await saveReminders(
      port,
      { enabled: false, time: '23:00', timezone: 'Europe/Kyiv' },
      POLICY
    );

    expect(port.saved).toEqual([
      { enabled: false, time: '23:00', timezone: 'Europe/Kyiv' }
    ]);
  });

  it('серверна відмова перемагає локальний дозвіл', async () => {
    // Якби політики розійшлися, авторитетною лишається серверна: локальна
    // перевірка тільки економить вікно, а не ухвалює рішення.
    const port = new PortDouble();
    port.failWith = refusal(422, 'quiet_hours_violation');

    await expect(saveReminders(port, SETTINGS, POLICY)).rejects.toBeInstanceOf(
      ReminderError
    );
    await expect(saveReminders(port, SETTINGS, POLICY)).rejects.toMatchObject({
      failure: 'quiet_hours'
    });
    expect(port.calls).toEqual(['saveReminders', 'saveReminders']);
  });

  it('порожня зона відхиляється до мережі', async () => {
    const port = new PortDouble();

    await expect(
      saveReminders(port, { ...SETTINGS, timezone: '  ' }, POLICY)
    ).rejects.toMatchObject({ failure: 'unknown_timezone' });

    expect(port.calls).toEqual([]);
  });
});

describe('читання розкладу', () => {
  it('віддає канонічний ресурс', async () => {
    expect(await loadReminders(new PortDouble())).toEqual(VIEW);
  });

  it('відкриває сесію ПЕРЕД читанням, а не покладається на чужу', async () => {
    // Перша редакція цього не робила, і екран діставав 401 одразу після
    // відкриття: `GET` ішов без заголовка `Authorization`. Дефект спіймав
    // прогін проти живого api — локально його не було видно взагалі.
    const port = new PortDouble();

    await loadReminders(port);

    expect(port.calls).toEqual(['ensureSession', 'reminders']);
  });

  it('відсутній акаунт читається як «згоди ще немає», а не як збій', async () => {
    const port = new PortDouble();
    port.failWith = refusal(403, 'no_account');

    await expect(loadReminders(port)).rejects.toMatchObject({
      failure: 'consent_required'
    });
  });

  it('відсутню згоду називає згодою, а не збоєм', async () => {
    const port = new PortDouble();
    port.failWith = refusal(403, 'consent_required');

    await expect(loadReminders(port)).rejects.toMatchObject({
      failure: 'consent_required'
    });
  });
});
