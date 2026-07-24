// AC1: повний щоденний чек-ін (самопочуття → симптоми → деталі → огляд →
// чекбокс підтвердження → Завершити) → Today показує бейдж «Завершено»,
// а резюме дня містить внесені факти.

import { test, expect } from '@playwright/test';
import { gotoApp } from './helpers';

test('AC1: повний чек-ін завершує день і формує резюме з фактів', async ({ page }) => {
  await gotoApp(page);
  await expect(page.getByRole('heading', { name: 'Четвер, 15 січня' })).toBeVisible();
  await expect(page.getByText('Не заповнено', { exact: true })).toBeVisible();

  // Крок 1 · Самопочуття
  await page.getByRole('button', { name: 'Заповнити день', exact: true }).click();
  await expect(page.getByText('Крок 1 із 3 · четвер, 15 січня')).toBeVisible();
  await expect(page.getByText('Як ви загалом почувалися сьогодні?')).toBeVisible();
  await page.getByRole('button', { name: '6 із 10', exact: true }).click();
  await expect(page.getByText('6/10', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Далі', exact: true }).click();

  // Крок 2 · Симптоми
  await expect(page.getByText('Симптоми', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Втома', exact: true }).click();
  await expect(page.getByText('Крок 2 із 4 · четвер, 15 січня')).toBeVisible();
  await page.getByRole('button', { name: 'Далі', exact: true }).click();

  // Крок 3 · Деталі симптому (обовʼязкова лише інтенсивність)
  await expect(page.getByText('Симптом 1 із 1')).toBeVisible();
  await page.getByRole('button', { name: 'Інтенсивність Втома: 3 із 5, помірно' }).click();
  await expect(page.getByText('3 — помірно')).toBeVisible();
  await page.getByRole('button', { name: 'Додати деталі · сторона, уточнення, коментар' }).click();
  await page.getByRole('button', { name: 'Фізична', exact: true }).click();
  await page.getByRole('button', { name: 'Помітно', exact: true }).click();
  await page.getByLabel('Короткий коментар').fill('Після роботи');
  await page.getByRole('button', { name: 'Далі', exact: true }).click();

  // Крок 4 (огляд) · Огляд і збереження
  await expect(page.getByText('Огляд і збереження')).toBeVisible();
  await expect(page.getByText('6/10', { exact: true })).toBeVisible();
  await expect(page.getByText('3/5 — помірно; фізична; вплив: помітно; «Після роботи»')).toBeVisible();

  // Чекбокс підтвердження «не було»
  const conf = page.getByRole('checkbox', { name: /Підтвердити відсутність у показаній секції/ });
  await expect(conf).toHaveAttribute('aria-checked', 'false');
  await expect(
    page.getByText(/Можна явно підтвердити:/)
  ).toBeVisible();
  await conf.click();
  await expect(conf).toHaveAttribute('aria-checked', 'true');
  await expect(page.getByText(/Буде записано як «не було»:/)).toBeVisible();

  await page.getByRole('button', { name: 'Завершити запис' }).click();

  // Today: бейдж «Завершено» + резюме містить внесені факти
  await expect(page.getByText('Завершено', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Редагувати запис' })).toBeVisible();
  await expect(page.getByText('Підсумок дня')).toBeVisible();
  const sum = page.locator('.card', { hasText: 'Підсумок дня' }).locator('p');
  await expect(sum).toContainText('самопочуття 6/10');
  await expect(sum).toContainText('втома 3/5 (фізична)');
  await expect(sum).toContainText('цикл — день 13');
});
