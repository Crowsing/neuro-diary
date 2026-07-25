// DoD Фази 2: `location.hash` і `sessionStorage` очищені від initData.
//
// Unit-тест модуля (`src/sync/initdata.test.ts`) перевіряє правило на
// підставленому оточенні. Тут перевіряється зібраний застосунок у справжньому
// браузері — бо саме там ланцюг «провайдер змонтувався → модуль підвантажився →
// зачистка відбулася» може розірватися й не показати цього жодним тестом.

import { expect, test } from '@playwright/test';
import { emptyAppState, seedEmptyDiary, signedInitData, syncAppUrl } from './sync-helpers';

test('initData не лишає слідів ані в адресі, ані в sessionStorage', async ({ page }) => {
  const initData = signedInitData({ queryId: 'hygiene-1' });

  await seedEmptyDiary(page, emptyAppState());
  // Telegram Web кладе initParams у sessionStorage; сюди їх записує сам
  // Telegram, тож тест мусить створити рівно ту саму умову.
  await page.addInitScript((raw) => {
    sessionStorage.setItem('__telegram__initParams', JSON.stringify({ tgWebAppData: raw }));
  }, initData);

  await page.goto(syncAppUrl(initData));
  await page.getByRole('heading', { name: 'Налаштування' }).waitFor();

  await expect
    .poll(async () => page.evaluate(() => window.location.hash))
    .toBe('');

  const stored = await page.evaluate(() =>
    sessionStorage.getItem('__telegram__initParams')
  );
  expect(stored).toBeNull();

  const href = await page.evaluate(() => window.location.href);
  expect(href).not.toContain('tgWebAppData');
  expect(href).not.toContain('signature');
});

test('initData не потрапляє в localStorage', async ({ page }) => {
  const initData = signedInitData({ queryId: 'hygiene-2' });
  await seedEmptyDiary(page, emptyAppState());
  await page.goto(syncAppUrl(initData));
  await page.getByRole('heading', { name: 'Налаштування' }).waitFor();

  const dump = await page.evaluate(() => JSON.stringify(localStorage));
  expect(dump).not.toContain('tgWebAppData');
  expect(dump).not.toContain('auth_date');
  expect(dump).not.toContain('signature');
});
