import { test, expect } from '@playwright/test';
import { APP_URL, demoData, readState, seedState, stubTelegramSdk, TELEGRAM_SDK_URL } from './helpers';

test('onboarding has five steps, ends after cycle, and never requests browser notifications', async ({ page }) => {
  // Виняток той самий і з тих самих причин, що в AC8: рівно один піно́ваний
  // рядок, звірений на повний збіг. Предмет цього тесту — канал нагадувань,
  // і SDK Telegram ним не є: він не має ані тіла запиту, ані параметрів.
  const external: string[] = [];
  const sdk: string[] = [];
  page.on('request', (request) => {
    const url = request.url();
    if (url.startsWith('http://localhost:4173/')) return;
    if (url === TELEGRAM_SDK_URL) sdk.push(url);
    else external.push(url);
  });
  await page.addInitScript(() => {
    const tracked = window as Window & { __notificationRequests?: number };
    tracked.__notificationRequests = 0;
    Object.defineProperty(window, 'Notification', {
      configurable: true,
      value: {
        permission: 'default',
        requestPermission: () => {
          tracked.__notificationRequests = (tracked.__notificationRequests || 0) + 1;
          return Promise.resolve('denied');
        }
      }
    });
  });

  await stubTelegramSdk(page);
  await page.goto(APP_URL);
  const headings = [
    'Пам’ятати менше.',
    'Приватність',
    'Групи спостереження',
    'Ваш список симптомів',
    'Менструальний цикл'
  ];
  for (let step = 0; step < headings.length; step++) {
    const progress = page.getByLabel(`Крок ${step + 1} із 5`);
    await expect(progress).toBeVisible();
    await expect(progress.locator('span')).toHaveCount(5);
    await expect(page.getByRole('heading', { name: headings[step], exact: false })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Нагадування' })).toHaveCount(0);
    await expect(page.getByText(/08:00|13:00|20:00|21:30/)).toHaveCount(0);
    await expect(page.getByText('Можна налаштувати пізніше')).toHaveCount(0);
    if (step < headings.length - 1) {
      await page.getByRole('button', { name: 'Далі', exact: true }).click();
    }
  }

  await expect(page.getByRole('button', { name: 'Почати', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Пропустити решту налаштувань' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Почати', exact: true }).click();
  await expect(page.getByRole('heading', { name: /четвер, 15 січня/i })).toBeVisible();
  expect(await page.evaluate(() => (window as Window & { __notificationRequests?: number }).__notificationRequests)).toBe(0);
  expect(external).toEqual([]);
  expect(sdk).toEqual([TELEGRAM_SDK_URL]);
});

test('legacy reminder state preserves health data and resumes on the cycle step without consent', async ({ page }) => {
  const data = {
    ...demoData(),
    groups: [{ id: 'g1', name: 'Неврологічна група', archived: false }],
    symptomGroupIds: { mood: ['g1'] },
    remOn: true,
    remTime: '08:00'
  };
  await seedState(page, {
    view: 'ob',
    obStep: 5,
    obSyms: ['mood'],
    obCycle: true,
    obRemOn: true,
    obTime: '08:00',
    data
  });
  await stubTelegramSdk(page);
  await page.goto(APP_URL);

  await expect(page.getByLabel('Крок 5 із 5')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Менструальний цикл' })).toBeVisible();
  await page.getByRole('button', { name: 'Почати', exact: true }).click();

  const persisted = await readState(page);
  expect(persisted.view).toBe('app');
  expect(persisted.data.active).toEqual(['mood']);
  expect(persisted.data.cycleOn).toBe(true);
  expect(persisted.data.groups).toEqual([{ id: 'g1', name: 'Неврологічна група', archived: false }]);
  expect(persisted.data.symptomGroupIds).toEqual({ mood: ['g1'] });
  expect(Object.keys(persisted.data.entries).length).toBeGreaterThan(20);
  expect(persisted.data.cycleStarts).toEqual(data.cycleStarts);
  expect(persisted.data.remOn).toBeUndefined();
  expect(persisted.data.remTime).toBeUndefined();
  expect(persisted.obRemOn).toBeUndefined();
  expect(persisted.obTime).toBeUndefined();
});
