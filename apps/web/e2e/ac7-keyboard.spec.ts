// AC7: основний flow доступний із клавіатури: Tab до CTA, Enter, вибір шкал
// через Tab+Enter; aria-label шкал формату «N із M, …»; фокус не губиться
// при відкритті та закритті діалогу.

import { test, expect } from '@playwright/test';
import { gotoApp, tabUntil, activeAria } from './helpers';

test('AC7: чек-ін проходиться клавіатурою, фокус не губиться в діалозі', async ({ page }) => {
  await gotoApp(page);
  await expect(page.getByRole('heading', { name: 'Четвер, 15 січня' })).toBeVisible();

  // Tab до CTA → Enter відкриває чек-ін
  await tabUntil(page, { text: 'Заповнити день' });
  await page.keyboard.press('Enter');
  await expect(page.getByText('Крок 1 із 3 · четвер, 15 січня')).toBeVisible();

  // Шкала самопочуття 1–10: aria-label формату «N із M, …»
  await tabUntil(page, { aria: '1 із 10, дуже погано' });
  expect(await activeAria(page)).toMatch(/^1 із 10, /);
  await tabUntil(page, { aria: '6 із 10' });
  await page.keyboard.press('Enter');
  await expect(page.getByText('6/10', { exact: true })).toBeVisible();

  // Далі → крок 2, вибір симптому клавіатурою
  await tabUntil(page, { text: 'Далі' });
  await page.keyboard.press('Enter');
  await expect(page.getByText('Симптоми', { exact: true })).toBeVisible();
  await tabUntil(page, { text: 'Втома' }, { shift: true });
  await page.keyboard.press('Enter');
  await tabUntil(page, { text: 'Далі' });
  await page.keyboard.press('Enter');
  await expect(page.getByText('Симптом 1 із 1')).toBeVisible();

  // Шкала 1–5 симптому: aria-label «N із M, …» + вибір Enter-ом
  await tabUntil(page, { aria: 'Інтенсивність Втома: 3 із 5, помірно' }, { shift: true });
  expect(await activeAria(page)).toMatch(/^Інтенсивність Втома: 3 із 5, /);
  await page.keyboard.press('Enter');
  await expect(page.getByText('3 — помірно')).toBeVisible();

  // Вихід із чернеткою — клавіатурою
  await tabUntil(page, { text: 'Зберегти як чернетку й вийти' });
  await page.keyboard.press('Enter');
  await expect(page.getByRole('button', { name: 'Продовжити', exact: true })).toBeVisible();

  // Діалог: фокус не губиться при відкритті…
  await tabUntil(page, { text: 'Почалися місячні' });
  await page.keyboard.press('Enter');
  await expect(page.locator('.dialog-title')).toHaveText('Почалися місячні');
  expect(
    await page.evaluate(() => document.activeElement !== null && document.activeElement !== document.body)
  ).toBe(true);

  // …і при закритті (фокус повертається на живий елемент, не на body)
  await tabUntil(page, { text: 'Скасувати' });
  await page.keyboard.press('Enter');
  await expect(page.locator('.dialog-title')).toBeHidden();
  expect(
    await page.evaluate(() => {
      const el = document.activeElement;
      return el !== null && el !== document.body && el.isConnected;
    })
  ).toBe(true);
});
