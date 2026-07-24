// AC3: пропущені дні рвуть полілінію графіка (сегментів ≥ 2), а взаємодія
// з пропущеним днем показує стан «не заповнено», відмінний від «не було».
// Демо-дані (?now=2026-01-15) детерміновані: у вікні 30 днів пропущені
// 19.12, 27.12, 04.01, чернетка 13.01 і незаповнене сьогодні → 5 сегментів.

import { test, expect } from '@playwright/test';
import { gotoApp } from './helpers';

test('AC3: прогалини рвуть лінію; «не заповнено» ≠ «не було»', async ({ page }) => {
  await gotoApp(page);
  await page.getByRole('button', { name: 'Динаміка' }).click();
  await expect(page.getByRole('heading', { name: 'Динаміка' })).toBeVisible();
  await page.getByRole('button', { name: /^Втома / }).click();
  await expect(page.getByRole('heading', { name: 'Втома' })).toBeVisible();

  const svg = page.locator('svg[viewBox="0 0 308 150"]');
  await expect(svg).toBeVisible();

  // Сегменти полілінії — лише path зі stroke (grid — <line>, flare — path із fill).
  const segs = svg.locator('path[stroke]');
  await expect(segs).toHaveCount(5);
  expect(await segs.count()).toBeGreaterThanOrEqual(2);

  // SVG presentational; повна доступна взаємодія відбувається в таблиці.
  await page.getByRole('button', { name: 'Таблиця', exact: true }).click();
  await page.getByRole('button', { name: /^4\.01: день не заповнено/ }).click();
  await expect(page.getByText('Неділя, 4 січня')).toBeVisible();
  await expect(page.getByText(/Цей день не заповнено/)).toBeVisible();
  await expect(page.getByText(/Пропущений день не означає, що симптомів не було/)).toBeVisible();

  // День 2026-01-08 (індекс 22): done + confirmed, втоми не було → інший стан
  await page.getByRole('button', { name: 'Назад до історії' }).click();
  await page.getByRole('button', { name: 'Динаміка' }).click();
  await page.getByRole('button', { name: /^Втома / }).click();
  await page.getByRole('button', { name: 'Таблиця', exact: true }).click();
  await expect(page.getByRole('button', { name: /^8\.01: підтверджено: не було/ })).toBeVisible();
});
