// Гігієна initData у LOCAL-ONLY збірці.
//
// Дзеркалить sync-initdata-hygiene.spec.ts, але без стенду й без api — і саме
// тому існує окремо: sync-специфікації запускаються лише за SYNC_E2E=1, тобто
// на машині без PostgreSQL цієї гарантії не перевіряв ніхто.
//
// Перевіряється те, чого раніше не було зовсім: зачистка стояла за прапорцем
// VITE_SYNC, тож local-only збірка не прибирала за собою нічого. Поки SDK не
// був підключений, це нічим не загрожувало — `__telegram__initParams` просто
// ніхто не писав. З підключеним telegram-web-app.js його пише сам Telegram.

import { test, expect } from '@playwright/test';
import { APP_URL, demoData, seedState, STORAGE_KEY } from './helpers';

const TELEGRAM_PARAM = '__telegram__initParams';
const RAW_INIT_DATA = 'auth_date=1768435200&query_id=local-1&user=%7B%22id%22%3A42%7D&signature=fake';

test('local-only збірка прибирає initData з фрагмента, sessionStorage і localStorage', async ({ page }) => {
  await seedState(page, { view: 'app', data: demoData() });
  // Так це виглядає у Telegram Web: SDK кладе initParams у sessionStorage, а
  // клієнт лишає `#tgWebAppData` в адресі.
  await page.addInitScript(
    ([key, raw]) => {
      sessionStorage.setItem(key, JSON.stringify({ tgWebAppData: raw }));
    },
    [TELEGRAM_PARAM, RAW_INIT_DATA] as const
  );

  await page.goto(`${APP_URL}#tgWebAppData=${encodeURIComponent(RAW_INIT_DATA)}`);
  await expect(page.getByRole('heading', { name: 'Четвер, 15 січня' })).toBeVisible();

  await expect
    .poll(() => page.evaluate((key) => sessionStorage.getItem(key), TELEGRAM_PARAM))
    .toBeNull();
  await expect.poll(() => page.evaluate(() => window.location.href)).not.toContain('tgWebAppData');

  // Найважливіше: рядок не осів у сховищі щоденника жодним шляхом.
  const persisted = await page.evaluate((key) => localStorage.getItem(key) ?? '', STORAGE_KEY);
  expect(persisted).not.toContain('tgWebAppData');
  expect(persisted).not.toContain('signature=fake');
  expect(persisted).not.toContain('query_id');
});
