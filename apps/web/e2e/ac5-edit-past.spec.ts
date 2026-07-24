// AC5: редагування минулого done-дня (Історія → Редагувати → зміна інтенсивності
// → Завершити) оновлює резюме дня І значення в таблиці Динаміки.
// Демо-день 2026-01-14 (?now=2026-01-15): done, «Слабкість у руці/руках» 2/5, права.

import { test, expect } from '@playwright/test';
import { gotoApp } from './helpers';

test('AC5: зміна інтенсивності в минулому дні оновлює резюме і Динаміку', async ({ page }) => {
  await gotoApp(page);

  // Історія → минулий done-день
  await page.getByRole('button', { name: 'Історія' }).click();
  await page.getByRole('button', { name: /Середа, 14 січня/ }).click();
  await expect(page.getByRole('heading', { name: 'Середа, 14 січня' })).toBeVisible();
  await expect(page.getByText('Завершено', { exact: true })).toBeVisible();
  const daySum = page.locator('.card', { hasText: 'Підсумок' }).first().locator('p');
  await expect(daySum).toContainText('слабкість у руці/руках 2/5, права');

  // Редагувати → огляд → Назад веде на картку симптому → інтенсивність 2 → 4
  await page.getByRole('button', { name: 'Редагувати запис' }).click();
  await expect(page.getByText('Огляд і збереження')).toBeVisible();
  await page.getByRole('button', { name: 'Назад', exact: true }).click();
  await expect(page.getByText('Симптом 1 із 1')).toBeVisible();
  await expect(page.getByRole('button', { name: /Інтенсивність Слабкість у руці\/руках: 2 із 5, легко/ })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: /Інтенсивність Слабкість у руці\/руках: 4 із 5, сильно/ }).click();
  await expect(page.getByText('4 — сильно')).toBeVisible();
  await page.getByRole('button', { name: 'Далі', exact: true }).click();
  await page.getByRole('button', { name: 'Завершити запис' }).click();

  // Повернення в деталі дня (back:'day'): резюме оновилось
  await expect(page.getByRole('heading', { name: 'Середа, 14 січня' })).toBeVisible();
  await expect(daySum).toContainText('слабкість у руці/руках 4/5, права');

  // Динаміка → Слабкість у руці/руках → Таблиця: значення за 14.01 оновилось
  await page.getByRole('button', { name: 'Назад до історії' }).click();
  await page.getByRole('button', { name: 'Динаміка' }).click();
  await page.getByRole('button', { name: /^Слабкість у руці\/руках / }).click();
  await expect(page.getByRole('heading', { name: 'Слабкість у руці/руках' })).toBeVisible();
  await page.getByRole('button', { name: 'Таблиця', exact: true }).click();
  const row = page.getByRole('button', { name: /^14\.01/ });
  await expect(row).toContainText('4/5');
});
