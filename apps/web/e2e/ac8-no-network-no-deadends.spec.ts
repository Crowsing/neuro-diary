// AC8: жодного network-запиту на невласний origin; обхід усіх 5 табів,
// усіх user-facing sub-екранів (checkin, day, sym, catalog, privacy, safety, crisis)
// і всіх 5 діалогів (menses, delEntry, flare, pdf, delData) — на кожному екрані
// є робочий шлях назад (немає dead-end).

import { test, expect } from '@playwright/test';
import { gotoApp } from './helpers';

test('AC8: офлайн-чистота і відсутність глухих кутів на всіх екранах', async ({ page }) => {
  const external: string[] = [];
  page.on('request', (req) => {
    const url = req.url();
    if (!url.startsWith('http://localhost:4173/')) external.push(url);
  });

  await gotoApp(page);
  await expect(page.getByRole('heading', { name: 'Четвер, 15 січня' })).toBeVisible();

  // --- Сьогодні → safety → назад
  await page.getByRole('button', { name: 'Коли потрібна термінова допомога?', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Коли потрібна термінова допомога?' })).toBeVisible();
  await page.getByRole('button', { name: 'Назад', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Четвер, 15 січня' })).toBeVisible();

  // --- Діалог menses → Скасувати
  await page.getByRole('button', { name: 'Почалися місячні' }).click();
  await expect(page.locator('.dialog-title')).toHaveText('Почалися місячні');
  await page.getByRole('button', { name: 'Скасувати', exact: true }).click();
  await expect(page.locator('.dialog-title')).toBeHidden();

  // --- Чек-ін → каталог → назад → crisis → назад → закрити чернетку
  await page.getByRole('button', { name: 'Заповнити день', exact: true }).click();
  await page.getByRole('button', { name: 'Далі', exact: true }).click();
  await expect(page.getByText('Симптоми', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Повний каталог симптомів' }).click();
  await expect(page.getByRole('heading', { name: 'Мої симптоми' })).toBeVisible();
  await page.getByRole('button', { name: 'Назад', exact: true }).click();
  await expect(page.getByText('Симптоми', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Пригніченість настрою', exact: true }).click();
  await page.getByRole('button', { name: 'Далі', exact: true }).click();
  await page.getByRole('button', { name: 'Інтенсивність Пригніченість настрою: 5 із 5, дуже сильно, суттєво заважає' }).click();
  await page.getByRole('button', { name: 'Потрібна підтримка зараз' }).click();
  await expect(page.getByRole('heading', { name: 'Підтримка' })).toBeVisible();
  await page.getByRole('button', { name: 'Ні', exact: true }).click();
  await expect(page.getByText(/Дякуємо за відповідь/)).toBeVisible();
  await page.getByRole('button', { name: 'Назад', exact: true }).click();
  await expect(page.getByText('Симптом 1 із 1')).toBeVisible();
  await page.getByRole('button', { name: 'Закрити і зберегти чернетку' }).click();
  await expect(page.getByRole('button', { name: 'Продовжити', exact: true })).toBeVisible();

  // --- Історія → деталі дня → діалоги delEntry і flare → назад
  await page.getByRole('button', { name: 'Історія' }).click();
  await expect(page.getByRole('heading', { name: 'Історія' })).toBeVisible();
  await page.getByRole('button', { name: '14 січня, завершено' }).click();
  await expect(page.getByRole('heading', { name: 'Середа, 14 січня' })).toBeVisible();
  await page.getByRole('button', { name: 'Видалити запис', exact: true }).click();
  await expect(page.locator('.dialog-title')).toHaveText('Видалити запис?');
  await page.getByRole('button', { name: 'Скасувати', exact: true }).click();
  await expect(page.locator('.dialog-title')).toBeHidden();
  await page.getByRole('button', { name: 'Позначити важливу зміну симптомів' }).click();
  await expect(page.locator('.dialog-title')).toHaveText('Важлива зміна симптомів для обговорення з лікарем');
  await page.getByRole('button', { name: 'Скасувати', exact: true }).click();
  await expect(page.locator('.dialog-title')).toBeHidden();
  await page.getByRole('button', { name: 'Назад до історії' }).click();
  await expect(page.getByRole('heading', { name: 'Історія' })).toBeVisible();

  // --- Динаміка → графік симптому → назад
  await page.getByRole('button', { name: 'Динаміка' }).click();
  await expect(page.getByRole('heading', { name: 'Динаміка' })).toBeVisible();
  await page.getByRole('button', { name: /^Втома / }).click();
  await expect(page.getByRole('heading', { name: 'Втома' })).toBeVisible();
  await page.getByRole('button', { name: 'Назад до динаміки' }).click();
  await expect(page.getByRole('heading', { name: 'Динаміка' })).toBeVisible();

  // --- Звіт: 1 → 2 → 3 → pdf-діалог → назад до кроку 1
  await page.getByRole('button', { name: 'Звіт' }).click();
  await expect(page.getByRole('heading', { name: 'Звіт для лікаря' })).toBeVisible();
  await page.getByRole('button', { name: 'Далі: вибір даних' }).click();
  await page.getByRole('button', { name: 'Втома · Без групи', exact: true }).click();
  await page.getByRole('button', { name: 'Переглянути PDF' }).click();
  await page.getByRole('button', { name: 'Експортувати звіт' }).click();
  await expect(page.locator('.dialog-title')).toHaveText('Зберегти звіт');
  await page.getByRole('button', { name: 'Закрити', exact: true }).click();
  await expect(page.locator('.dialog-title')).toBeHidden();
  await page.getByRole('button', { name: 'Назад до налаштувань' }).click();
  await expect(page.getByText('Симптоми у звіті · виберіть явно')).toBeVisible();
  await page.getByRole('button', { name: 'Назад', exact: true }).click();
  await expect(page.getByText('Крок 1 · Період')).toBeVisible();

  // --- Налаштування: всі під-екрани і діалог delData мають шлях назад
  await page.getByRole('button', { name: 'Налаштування' }).click();
  await expect(page.getByRole('heading', { name: 'Налаштування' })).toBeVisible();

  await page.getByRole('button', { name: /Групи спостереження/ }).click();
  await expect(page.getByRole('heading', { name: 'Групи спостереження' })).toBeVisible();
  await page.getByRole('button', { name: 'Назад до налаштувань' }).click();
  await expect(page.getByRole('heading', { name: 'Налаштування' })).toBeVisible();

  await page.getByRole('button', { name: /Мої симптоми/ }).click();
  await expect(page.getByRole('heading', { name: 'Мої симптоми' })).toBeVisible();
  await page.getByRole('button', { name: 'Назад', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Налаштування' })).toBeVisible();

  await page.getByRole('button', { name: /Приватність і дані/ }).click();
  await expect(page.getByRole('heading', { name: 'Приватність і дані' })).toBeVisible();
  await page.getByRole('button', { name: 'Назад', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Налаштування' })).toBeVisible();

  await page.getByRole('button', { name: /Ключові ознаки і як діяти/ }).click();
  await expect(page.getByRole('heading', { name: 'Коли потрібна термінова допомога?' })).toBeVisible();
  await page.getByRole('button', { name: 'Назад', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Налаштування' })).toBeVisible();

  await page.getByRole('button', { name: 'Видалення даних' }).click();
  await expect(page.locator('.dialog-title')).toHaveText('Дані застосунку');
  await page.getByRole('button', { name: 'Закрити', exact: true }).click();
  await expect(page.locator('.dialog-title')).toBeHidden();
  await expect(page.getByRole('heading', { name: 'Налаштування' })).toBeVisible();

  // --- Жодного запиту на невласний origin за весь обхід
  expect(external).toEqual([]);
});
