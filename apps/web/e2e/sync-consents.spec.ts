// Відкликання згод проти живого api — Art. 7(3).
//
// До Фази 6 `POST /v1/consents/revoke` перевіряли лише серверні тести: у
// `apps/web` виклику не було взагалі. Тобто право відкликати виконувалося
// сюїтою, а не користувачкою, і журнал прогресу називав це успадкованою діркою
// з Фази 2.
//
// Найважливіше твердження тут — не «згода зникла», а **що локальні дані
// лишилися**. §9.4 забороняє видаляти дані домену з неактивною згодою, і
// «прибрати зайве» після успішного відкликання було б найтяжчим можливим
// дефектом цього екрана: користувачка натискає «відкликати згоду на
// синхронізацію» і втрачає щоденник.

import { expect, test } from '@playwright/test';
import { emptyAppState, freshTelegramUserId, openDevice } from './sync-helpers';

test.describe.configure({ mode: 'serial' });

/** Щоденник із одним днем — щоб було що не втратити. */
function diaryWithOneDay(): Record<string, unknown> {
  const state = emptyAppState();
  const data = state.data as Record<string, unknown>;
  data.entries = {
    '2026-01-15': {
      status: 'done',
      wb: 3,
      sym: { fatigue: { int: 2 } },
      absent: [],
      ctx: {},
      note: 'нотатка, яку не можна втратити',
      flare: null,
      noSymptoms: false,
      filledLater: false
    }
  };
  data.active = ['fatigue'];
  return state;
}

test('нагадування відкликаються, і розклад зникає разом зі згодою', async ({
  context
}) => {
  const { page } = await openDevice(context, {
    queryId: `consents-rem-${Date.now()}`,
    telegramUserId: freshTelegramUserId(),
    seed: emptyAppState()
  });

  // Спершу згода на нагадування — акаунт лише з нею, без сейфа.
  await page.getByTestId('reminders-card').click();
  await expect(page.getByTestId('reminders-enable')).toBeVisible({ timeout: 30_000 });
  await page.locator('#reminders-time').fill('20:00');
  await page.locator('#reminders-timezone').fill('Europe/Kyiv');
  await page.getByTestId('reminders-enable').click();
  await page.getByRole('button', { name: 'Погоджуюсь' }).click();
  await expect(page.getByTestId('reminders-save')).toBeVisible({ timeout: 60_000 });

  await page.getByTestId('reminders-consents').click();
  await expect(page.getByTestId('consent-row-telegram_reminders')).toBeVisible({
    timeout: 30_000
  });

  await page.getByTestId('consent-revoke-telegram_reminders').click();
  // Показується дослівний текст із реєстру, а не переказ своїми словами.
  await expect(page.getByTestId('consent-revoke-text')).toContainText(
    'Вимкнути нагадування?'
  );
  // Це остання згода цього акаунта — і UI мусить це сказати до натискання.
  await expect(page.getByTestId('consent-revoke-last')).toBeVisible();
  await page.getByTestId('consent-revoke-confirm').click();

  await expect(page.getByTestId('consents-erased')).toBeVisible({ timeout: 60_000 });
});

test('відкликання health_sync не забирає локальних записів', async ({ context }) => {
  const { page } = await openDevice(context, {
    queryId: `consents-health-${Date.now()}`,
    telegramUserId: freshTelegramUserId(),
    seed: diaryWithOneDay()
  });

  await page.getByTestId('sync-enable').click();
  await page.getByRole('button', { name: 'Погоджуюсь' }).click();
  const shown = page.getByTestId('generated-passphrase');
  await expect(shown).toBeVisible({ timeout: 30_000 });
  const passphrase = ((await shown.textContent()) ?? '').trim();
  await page.getByLabel('Введіть фразу повністю, щоб підтвердити.').fill(passphrase);
  await page.getByRole('button', { name: 'Продовжити' }).click();
  await expect(page.getByTestId('sync-overlay')).toBeHidden({ timeout: 60_000 });

  await page.getByTestId('sync-consents').click();
  await expect(page.getByTestId('consent-row-health_sync')).toBeVisible({
    timeout: 30_000
  });

  await page.getByTestId('consent-revoke-health_sync').click();
  await page.getByTestId('consent-revoke-confirm').click();
  await expect(page.getByTestId('consents-erased')).toBeVisible({ timeout: 60_000 });
  await page.getByRole('button', { name: 'Закрити' }).click();

  // Ось воно. Серверної копії немає, акаунта немає — а щоденник на пристрої
  // цілий, включно з нотаткою.
  const persisted = await page.evaluate(() =>
    JSON.parse(localStorage.getItem('nd_demo_v3') || 'null')
  );
  expect(Object.keys(persisted.data.entries)).toEqual(['2026-01-15']);
  expect(persisted.data.entries['2026-01-15'].note).toBe(
    'нотатка, яку не можна втратити'
  );
  expect(persisted.data.active).toEqual(['fatigue']);
});
