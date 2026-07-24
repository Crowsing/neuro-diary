// AC2: частково заповнений чек-ін, збережений як чернетка, переживає reload:
// Today показує «Чернетка», «Продовжити» повертає на той самий крок із даними,
// а незаповнені симптоми залишаються unknown — НЕ стають absent.

import { test, expect } from '@playwright/test';
import { gotoApp, readState, NOW_ISO } from './helpers';

test('AC2: чернетка переживає reload і продовжується з того самого кроку', async ({ page }) => {
  await gotoApp(page);

  // Часткове заповнення: самопочуття 6 → крок 2 → вибрано «Втома»
  await page.getByRole('button', { name: 'Заповнити день', exact: true }).click();
  await page.getByRole('button', { name: '6 із 10', exact: true }).click();
  await page.getByRole('button', { name: 'Далі', exact: true }).click();
  await page.getByRole('button', { name: 'Втома', exact: true }).click();
  await page.getByRole('button', { name: 'Зберегти як чернетку й вийти' }).click();

  // Today: статус «Чернетка», CTA «Продовжити»
  await expect(page.getByText('Чернетка', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Продовжити', exact: true })).toBeVisible();

  await page.reload();

  // Чернетка пережила reload
  await expect(page.getByText('Чернетка', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Продовжити', exact: true })).toBeVisible();

  // unknown НЕ стали absent: запис — draft, підтвердження нема, значень
  // невибраних симптомів нема (absent можливий лише після done + confirmed).
  const st = await readState(page);
  const entry = st.data.entries[NOW_ISO];
  expect(entry.status).toBe('draft');
  expect(entry.d.sel).toEqual(['fatigue']);
  expect(entry.d.absent).toEqual([]);
  expect(Object.keys(entry.d.sym)).toEqual([]);

  // «Продовжити» повертає на той самий крок (2 · Симптоми) з даними
  await page.getByRole('button', { name: 'Продовжити', exact: true }).click();
  await expect(page.getByText('Крок 2 із 4 · четвер, 15 січня')).toBeVisible();
  await expect(page.getByText('Симптоми', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Втома', exact: true })).toHaveAttribute('aria-pressed', 'true');

  // Крок 1 зберіг оцінку самопочуття
  await page.getByRole('button', { name: 'Назад', exact: true }).click();
  await expect(page.getByRole('button', { name: '6 із 10', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByText('6/10', { exact: true })).toBeVisible();
});
