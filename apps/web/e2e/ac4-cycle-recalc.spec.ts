// AC4: «Почалися місячні» → підтвердити сьогодні → «день 1»; прибрати позначку
// в деталях дня → день циклу перераховано від попереднього старту (03.01 → день 13).

import { test, expect } from '@playwright/test';
import { gotoApp } from './helpers';

test('AC4: позначка місячних дає день 1, її видалення перераховує цикл', async ({ page }) => {
  await gotoApp(page);
  // Демо: останній старт циклу 2026-01-03 → сьогодні (15.01) — день 13
  await expect(page.getByText('Цикл · день 13', { exact: true })).toBeVisible();

  // «Почалися місячні» → чип «Сьогодні» вибраний за замовчуванням → Позначити
  await page.getByRole('button', { name: 'Почалися місячні' }).click();
  await expect(page.locator('.dialog-title')).toHaveText('Почалися місячні');
  await page.getByRole('button', { name: 'Позначити', exact: true }).click();
  await expect(page.locator('.dialog-title')).toBeHidden();
  await expect(page.getByText('Цикл · день 1', { exact: true })).toBeVisible();

  // Історія → деталі сьогоднішнього дня → прибрати позначку
  await page.getByRole('button', { name: 'Історія' }).click();
  await page.getByRole('button', { name: '15 січня, не заповнено' }).click();
  await expect(page.getByRole('heading', { name: 'Четвер, 15 січня' })).toBeVisible();
  await expect(page.getByText('Початок менструації', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Прибрати позначку початку менструації' }).click();
  await expect(page.getByText('Початок менструації', { exact: true })).toBeHidden();

  // День циклу перераховано від попереднього старту (03.01)
  await page.getByRole('button', { name: 'Назад до історії' }).click();
  await page.getByRole('button', { name: 'Сьогодні' }).click();
  await expect(page.getByText('Цикл · день 13', { exact: true })).toBeVisible();
});
