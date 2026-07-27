// DoD Фази 2: два браузерні профілі синхронізуються через локальний api.
//
// Сценарій обраний так, щоб він справді щось доводив, а не лише показував
// «запит пройшов»: A створює → B бачить → B редагує → A бачить правку →
// A видаляє → B бачить видалення і НЕ воскрешає запис. Останній крок і є
// найважливішим: саме воскресіння видаленого було одним із двох дефектів рівня
// «зупинка», знайдених незалежним review етапів A–C.
//
// Обидва профілі — окремі `browser.newContext()` з власним localStorage і
// власним підписаним initData: anti-replay §8 віддає сесію щонайбільше один раз
// на конкретний initData, тож спільний рядок дав би 401 на другому пристрої.

import { expect, test, type Page } from '@playwright/test';
import { genDemo } from '../src/lib/demo';
import { emptyAppState, openDevice } from './sync-helpers';

test.describe.configure({ mode: 'serial' });

const DEMO = () => genDemo(new Date('2026-01-15T12:00:00+02:00'));

/** Проходить згоду й екран фрази; повертає згенеровану фразу. */
async function enableAndCapturePassphrase(page: Page): Promise<string> {
  await page.getByTestId('sync-enable').click();
  await page.getByRole('button', { name: 'Погоджуюсь' }).click();

  const shown = page.getByTestId('generated-passphrase');
  await expect(shown).toBeVisible({ timeout: 30_000 });
  const passphrase = ((await shown.textContent()) ?? '').trim();
  expect(passphrase.split(/\s+/)).toHaveLength(6);

  await page.getByLabel('Введіть фразу повністю, щоб підтвердити.').fill(passphrase);
  await page.getByRole('button', { name: 'Продовжити' }).click();
  await expect(page.getByTestId('sync-overlay')).toBeHidden({ timeout: 60_000 });
  return passphrase;
}

/** Другий пристрій: та сама згода, але фраза вже існує. */
async function enableWithExistingPassphrase(page: Page, passphrase: string): Promise<void> {
  await page.getByTestId('sync-enable').click();
  await page.getByRole('button', { name: 'Погоджуюсь' }).click();

  const input = page.getByTestId('passphrase-input');
  await expect(input).toBeVisible({ timeout: 30_000 });
  await input.fill(passphrase);
  await page.getByRole('button', { name: 'Продовжити' }).click();
  await expect(page.getByTestId('sync-overlay')).toBeHidden({ timeout: 60_000 });
}

/** Головна навігація: у ній назви розділів однозначні. */
function nav(page: Page) {
  return page.getByRole('navigation', { name: 'Основна навігація' });
}

/**
 * Головна навігація рендериться лише поза підекранами (`state.sub === null`),
 * тож із деталей дня спершу треба вийти — інакше клік нікуди не влучає.
 */
async function leaveSubScreen(page: Page): Promise<void> {
  const back = page.getByRole('button', { name: 'Назад до історії' });
  if (await back.isVisible()) await back.click();
  await expect(nav(page)).toBeVisible();
}

async function syncNow(page: Page): Promise<void> {
  await leaveSubScreen(page);
  await nav(page).getByRole('button', { name: 'Налаштування' }).click();
  await page.getByTestId('sync-now').click();
  await expect(page.getByTestId('sync-overlay')).toBeHidden({ timeout: 120_000 });
}

async function openDay(page: Page): Promise<void> {
  await leaveSubScreen(page);
  await nav(page).getByRole('button', { name: 'Історія' }).click();
  await page.getByRole('button', { name: /Середа, 14 січня/ }).click();
  await expect(page.getByRole('heading', { name: 'Середа, 14 січня' })).toBeVisible();
}

/** Ключі записів у збереженому стані — ґрунт, на який дивиться сам застосунок. */
async function entryDates(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const raw = localStorage.getItem('nd_demo_v3');
    if (raw === null) return [];
    const parsed = JSON.parse(raw) as { data?: { entries?: Record<string, unknown> } };
    return Object.keys(parsed.data?.entries ?? {});
  });
}

/** День зник і з інтерфейсу, і зі збереженого стану. */
async function expectDayGone(page: Page): Promise<void> {
  await leaveSubScreen(page);
  await nav(page).getByRole('button', { name: 'Історія' }).click();
  await expect(page.getByRole('button', { name: '14 січня, не заповнено' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Середа, 14 січня/ })).toHaveCount(0);
  expect(await entryDates(page)).not.toContain('2026-01-14');
}

test('щоденник переживає обидва пристрої, включно з видаленням', async ({ browser }) => {
  const first = await browser.newContext();
  const second = await browser.newContext();

  // A — пристрій із щоденником. B — чистий профіль без жодного запису.
  const a = await openDevice(first, { queryId: 'device-a', seed: { view: 'app', tab: 'set', data: DEMO() } });
  const b = await openDevice(second, { queryId: 'device-b', seed: emptyAppState() });

  const passphrase = await enableAndCapturePassphrase(a.page);

  // 1. B бачить те, чого в нього не було.
  await enableWithExistingPassphrase(b.page, passphrase);
  await openDay(b.page);
  const summaryOnB = b.page.locator('.card', { hasText: 'Підсумок' }).first().locator('p');
  await expect(summaryOnB).toContainText('слабкість у руці/руках 2/5, права');

  // 2. B редагує інтенсивність і надсилає правку.
  await b.page.getByRole('button', { name: 'Редагувати запис' }).click();
  await expect(b.page.getByText('Огляд і збереження')).toBeVisible();
  await b.page.getByRole('button', { name: 'Назад', exact: true }).click();
  await b.page.getByRole('button', { name: /Інтенсивність Слабкість у руці\/руках: 4 із 5, сильно/ }).click();
  await b.page.getByRole('button', { name: 'Далі', exact: true }).click();
  await b.page.getByRole('button', { name: 'Завершити запис' }).click();
  await expect(summaryOnB).toContainText('слабкість у руці/руках 4/5, права');
  await syncNow(b.page);

  // 3. A бачить правку B.
  await syncNow(a.page);
  await openDay(a.page);
  const summaryOnA = a.page.locator('.card', { hasText: 'Підсумок' }).first().locator('p');
  await expect(summaryOnA).toContainText('слабкість у руці/руках 4/5, права');

  // 4. A видаляє запис і надсилає надгробок.
  await a.page.getByRole('button', { name: 'Видалити запис' }).click();
  await a.page.getByRole('button', { name: 'Видалити', exact: true }).click();
  await syncNow(a.page);

  // 5. B бачить видалення — і НЕ воскрешає запис на наступному синку.
  await syncNow(b.page);
  await expectDayGone(b.page);

  // Другий синк — саме тут воскресіння й ловиться: пристрій, у якого запис ще
  // був у пам'яті, мусив би надіслати його назад, якби журнал не працював.
  await syncNow(b.page);
  await expectDayGone(b.page);

  // І на A він теж не повертається після зворотного синку.
  await syncNow(a.page);
  await expectDayGone(a.page);

  await first.close();
  await second.close();
});

test('банер стенду видимий, бо APP_ENV=development', async ({ browser }) => {
  const context = await browser.newContext();
  const device = await openDevice(context, { queryId: 'banner-1', seed: emptyAppState() });
  const banner = device.page.getByTestId('development-banner');
  await expect(banner).toBeVisible({ timeout: 30_000 });

  // `toBeVisible` тут недостатньо. Банер був статичним сусідом оболонки
  // висотою 100% усередині .nd-page з overflow:hidden, тобто лягав рівно на
  // y = висота сторінки й обрізався — а Playwright уважає обрізаний елемент
  // із непорожнім боксом видимим, тож цей тест роками зеленів на банері,
  // якого користувачка не бачила. Перевіряється саме положення в кадрі.
  const box = await banner.boundingBox();
  const viewport = device.page.viewportSize();
  expect(box).not.toBeNull();
  expect(viewport).not.toBeNull();
  expect(box!.y).toBeGreaterThanOrEqual(0);
  expect(box!.y + box!.height).toBeLessThanOrEqual(viewport!.height);

  // А тепер — друга половина тієї самої історії. Перша спроба зробити банер
  // видимим зробила його плавучим, і він мовчки почав перехоплювати кліки по
  // кнопці «Назад» під собою: клік ретраївся 462 рази до таймауту. Банер
  // постійний і неінтерактивний, тож він мусить ЗАЙМАТИ місце, а не накривати
  // його. Перевіряється те й те: він вище за зміст і не ловить вказівник.
  const screen = await device.page.locator('.nd-screen, .nd-checkin, .nd-onboarding').first().boundingBox();
  expect(screen).not.toBeNull();
  expect(box!.y + box!.height, 'банер накриває зміст замість того, щоб його посунути')
    .toBeLessThanOrEqual(screen!.y);

  await device.page.getByRole('button', { name: 'Налаштування', exact: true }).click();
  await device.page.getByRole('button', { name: /Мої симптоми/ }).click();
  await expect(device.page.getByRole('heading', { name: 'Мої симптоми' })).toBeVisible();
  // Клік із таймаутом, коротшим за типовий: якщо банер знову ловитиме
  // вказівник, тест впаде швидко й зрозуміло, а не через чотири хвилини.
  await device.page.getByRole('button', { name: 'Назад', exact: true }).click({ timeout: 10_000 });
  await expect(device.page.getByRole('heading', { name: 'Налаштування' })).toBeVisible();

  await context.close();
});
