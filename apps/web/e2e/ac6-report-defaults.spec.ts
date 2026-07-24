// AC6: у звіті тумблери «День циклу» і «Вільні нотатки» вимкнені за
// замовчуванням; PDF preview без цих секцій; після ввімкнення секції зʼявляються.

import { test, expect } from '@playwright/test';
import { gotoApp } from './helpers';

test('AC6: цикл і нотатки вимкнені за замовчуванням і вмикаються явно', async ({ page }) => {
  await gotoApp(page);
  await page.getByRole('button', { name: 'Звіт' }).click();
  await expect(page.getByRole('heading', { name: 'Звіт для лікаря' })).toBeVisible();
  await page.getByRole('button', { name: 'Далі: вибір даних' }).click();

  // Крок 2: дефолти тумблерів
  const cycleTg = page.getByRole('switch', { name: 'День циклу' });
  const notesTg = page.getByRole('switch', { name: 'Вільні нотатки' });
  await expect(cycleTg).toHaveAttribute('aria-checked', 'false');
  await expect(notesTg).toHaveAttribute('aria-checked', 'false');
  await expect(page.getByRole('switch', { name: 'Контекстні фактори' })).toHaveAttribute('aria-checked', 'true');
  await expect(page.getByRole('switch', { name: 'Важливі зміни симптомів' })).toHaveAttribute('aria-checked', 'true');
  await page.getByRole('button', { name: 'Втома · Без групи', exact: true }).click();

  // Preview без секцій «Цикл» і «Нотатки»
  await page.getByRole('button', { name: 'Переглянути PDF' }).click();
  await expect(page.getByText('Неврологічний щоденник — звіт за період')).toBeVisible();
  await expect(page.getByText(/Цикл · дані всього дня/)).toBeHidden();
  await expect(page.getByText(/Нотатки · дані всього дня/)).toBeHidden();
  await expect(page.getByText('Контекст дня', { exact: true })).toBeVisible();

  // Увімкнути обидва → секції зʼявляються
  await page.getByRole('button', { name: 'Назад до налаштувань' }).click();
  await cycleTg.click();
  await notesTg.click();
  await expect(cycleTg).toHaveAttribute('aria-checked', 'true');
  await expect(notesTg).toHaveAttribute('aria-checked', 'true');
  await page.getByRole('button', { name: 'Переглянути PDF' }).click();
  await expect(page.getByText(/Цикл · дані всього дня/)).toBeVisible();
  await expect(
    page.getByText('Початок менструації: 3.01. День циклу рахується від останньої позначеної дати.')
  ).toBeVisible();
  await expect(page.getByText(/Нотатки · дані всього дня/)).toBeVisible();
  await expect(page.getByText(/11\.01 — Стресовий день на роботі/)).toBeVisible();
});
