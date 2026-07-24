import { test, expect } from '@playwright/test';
import { gotoApp } from './helpers';

test('CSV/JSON are real downloads while unavailable reminder and lock gaps stay explicit', async ({ page }) => {
  await gotoApp(page);
  await page.getByRole('button', { name: 'Налаштування', exact: true }).click();
  const reminderCard = page.locator('.card').filter({ hasText: 'Нагадування недоступні' });
  await expect(reminderCard).toHaveCount(1);
  await expect(reminderCard).toContainText('Майбутні зовнішні нагадування можливі лише через Telegram');
  await expect(reminderCard.locator('button, input, select, [role="switch"]')).toHaveCount(0);
  await page.getByRole('button', { name: /Приватність і дані/ }).click();
  await expect(page.getByText('Локальні toast-підтвердження')).toBeVisible();
  await expect(page.getByText('Поточне локальне зберігання')).toBeVisible();
  await expect(page.getByText('Майбутні Telegram-нагадування')).toBeVisible();
  await expect(page.getByText(/Окремий PIN або біометричний lock у вебверсії не реалізовано/)).toBeVisible();
  await expect(page.getByRole('switch', { name: 'App lock' })).toHaveCount(0);

  const csvEvent = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Експортувати CSV записів' }).click();
  expect((await csvEvent).suggestedFilename()).toMatch(/^neuro-diary-\d{4}-\d{2}-\d{2}\.csv$/);

  const jsonEvent = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Експортувати JSON усіх локальних даних' }).click();
  expect((await jsonEvent).suggestedFilename()).toMatch(/^neuro-diary-\d{4}-\d{2}-\d{2}\.json$/);
});
