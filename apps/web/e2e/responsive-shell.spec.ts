import { test, expect } from '@playwright/test';
import { APP_URL, gotoApp, readState, stubTelegramSdk } from './helpers';

test('responsive shell: 390/430/899 are mobile; 900/1440 are desktop without page overflow', async ({ page }) => {
  await gotoApp(page);
  const cases = [
    { width: 320, height: 568, layout: 'mobile' },
    { width: 390, height: 844, layout: 'mobile' },
    { width: 430, height: 932, layout: 'mobile' },
    { width: 844, height: 390, layout: 'mobile' },
    { width: 899, height: 900, layout: 'mobile' },
    { width: 900, height: 900, layout: 'desktop' },
    { width: 1440, height: 900, layout: 'desktop' }
  ] as const;

  for (const viewport of cases) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await expect(page.locator('.nd-page')).toHaveAttribute('data-layout', viewport.layout);
    await expect(page.locator(`[data-navigation="${viewport.layout}"]`)).toBeVisible();
    await expect(page.locator(`[data-navigation="${viewport.layout === 'mobile' ? 'desktop' : 'mobile'}"]`)).toHaveCount(0);
    const dimensions = await page.evaluate(() => ({
      client: document.documentElement.clientWidth,
      scroll: document.documentElement.scrollWidth
    }));
    expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.client);
  }

  await expect(page.locator('.nd-statusbar')).toHaveCount(0);
  await expect(page.getByText(/Demo-прототип|Sitemap|Reusable components|Desktop-адаптація/)).toHaveCount(0);
});

test('trend cards keep their content height in a short mobile viewport', async ({ page }) => {
  await page.setViewportSize({ width: 472, height: 676 });
  await gotoApp(page, { tab: 'trends' });

  const cards = page.locator('[data-screen-label="Динаміка"] > button.card');
  await expect(cards.first()).toBeVisible();

  const layout = await cards.evaluateAll((nodes) => nodes.map((node) => {
    const element = node as HTMLElement;
    const box = element.getBoundingClientRect();
    return {
      top: box.top,
      bottom: box.bottom,
      clientHeight: element.clientHeight,
      scrollHeight: element.scrollHeight
    };
  }));

  expect(layout.every((card) => card.clientHeight >= card.scrollHeight)).toBe(true);
  expect(layout.slice(1).every((card, index) => card.top >= layout[index].bottom)).toBe(true);
});

test('desktop sidebar keeps all five sections in the desktop shell', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await gotoApp(page);
  const nav = page.locator('[data-navigation="desktop"]');
  await expect(nav.getByRole('button')).toHaveCount(5);

  const sections = [
    ['Сьогодні', 'Четвер, 15 січня'],
    ['Історія', 'Історія'],
    ['Динаміка', 'Динаміка'],
    ['Звіт', 'Звіт для лікаря'],
    ['Налаштування', 'Налаштування']
  ] as const;

  for (const [button, heading] of sections) {
    await nav.getByRole('button', { name: button, exact: true }).click();
    await expect(page.locator('.nd-page')).toHaveAttribute('data-layout', 'desktop');
    await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible();
  }
});

test('mobile → desktop → mobile preserves check-in draft, dialog, active report and typed data', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await gotoApp(page);

  await page.getByRole('button', { name: 'Заповнити день', exact: true }).click();
  await page.getByRole('button', { name: '6 із 10', exact: true }).click();
  await page.getByRole('button', { name: 'Далі', exact: true }).click();
  await page.getByRole('button', { name: 'Втома', exact: true }).click();

  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(page.locator('.nd-page')).toHaveAttribute('data-layout', 'desktop');
  await expect(page.getByText('Крок 2 із 4 · четвер, 15 січня')).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByText('Крок 2 із 4 · четвер, 15 січня')).toBeVisible();
  await page.getByRole('button', { name: 'Зберегти як чернетку й вийти' }).click();

  await page.getByRole('button', { name: 'Почалися місячні' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(1);
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(page.getByRole('dialog', { name: 'Почалися місячні' })).toBeVisible();
  await expect(page.getByRole('dialog')).toHaveCount(1);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('dialog', { name: 'Почалися місячні' })).toBeVisible();
  await page.keyboard.press('Escape');

  await page.getByRole('button', { name: 'Звіт', exact: true }).click();
  await page.getByRole('button', { name: 'Далі: вибір даних' }).click();
  await page.getByRole('button', { name: 'Втома · Без групи', exact: true }).click();
  await page.getByLabel('Імʼя').fill('Олена');
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(page.getByLabel('Імʼя')).toHaveValue('Олена');
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByLabel('Імʼя')).toHaveValue('Олена');
  expect((await readState(page)).report.name).toBe('');
  await page.getByRole('button', { name: 'Переглянути PDF' }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  await page.setViewportSize({ width: 900, height: 900 });
  await expect(page.locator('.nd-page')).toHaveAttribute('data-layout', 'desktop');
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(900);
  await page.reload();
  await expect(page.locator('.nd-print-report')).not.toContainText('Олена');
  await page.getByRole('button', { name: 'Назад до налаштувань' }).click();
  await expect(page.getByLabel('Імʼя')).toHaveValue('');
});

for (const desk of [true, false]) {
  test(`legacy desk:${desk} never controls presentation`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await gotoApp(page, { desk });
    await expect(page.locator('.nd-page')).toHaveAttribute('data-layout', 'mobile');
    await page.setViewportSize({ width: 1440, height: 900 });
    await expect(page.locator('.nd-page')).toHaveAttribute('data-layout', 'desktop');
    await expect.poll(async () => (await readState(page)).desk).toBeUndefined();
  });
}

test('onboarding stays inside the active responsive shell', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await stubTelegramSdk(page);
  await page.goto(APP_URL);
  await expect(page.locator('.nd-page')).toHaveAttribute('data-layout', 'mobile');
  await expect(page.locator('[data-screen-label="Onboarding"]')).toBeVisible();
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(page.locator('.nd-page')).toHaveAttribute('data-layout', 'desktop');
  await expect(page.locator('[data-screen-label="Onboarding"]')).toBeVisible();
});
