// Геометрія оболонки: усе, що позиціюється абсолютно і тому живе ПОЗА
// потоком екранів — верхні алерти, тост, діалог.
//
// Ці перевірки навмисно говорять про координати, а не про видимість.
// `toBeVisible` тут не годиться: Playwright уважає видимим елемент із
// непорожнім боксом навіть тоді, коли предок обрізав його цілком. Саме через
// це банер стенду й увесь sync-оверлей роками рендерилися нижче viewport при
// зеленій сюїті. Перевіряти треба положення в кадрі.
//
// Інсети тут нульові (звичайний браузер без вирізу). Їхній ненульовий випадок
// покриває telegram-fullscreen.spec.ts.

import { test, expect, type Locator, type Page } from '@playwright/test';
import { demoData, gotoApp, gotoWithBrokenStorage } from './helpers';

interface Box {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

async function boxOf(locator: Locator): Promise<Box> {
  const box = await locator.boundingBox();
  expect(box, 'елемент не має боксу — його немає в макеті').not.toBeNull();
  return box!;
}

function viewportOf(page: Page): { width: number; height: number } {
  const size = page.viewportSize();
  expect(size).not.toBeNull();
  return size!;
}

function expectInsideViewport(box: Box, page: Page, what: string): void {
  const viewport = viewportOf(page);
  expect(box.y, `${what}: верхній край вище кадру`).toBeGreaterThanOrEqual(0);
  expect(box.y + box.height, `${what}: нижній край нижче кадру`).toBeLessThanOrEqual(viewport.height);
  expect(box.x, `${what}: лівий край лівіше кадру`).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width, `${what}: правий край правіше кадру`).toBeLessThanOrEqual(viewport.width);
}

test('алерт помилки сховища лишається в кадрі', async ({ page }) => {
  await gotoWithBrokenStorage(page, { view: 'app', data: { ...demoData(), entries: {}, active: ['fatigue'] } });
  const alert = page.getByRole('alert');
  await expect(alert).toContainText('Не вдалося зберегти зміни в браузері');
  expectInsideViewport(await boxOf(alert), page, 'алерт сховища');
});

test('тост стоїть над нижньою навігацією, а без неї — опускається', async ({ page }) => {
  await gotoApp(page, { data: { ...demoData(), entries: {}, active: ['fatigue'] } });
  // Нижня навігація існує лише в мобільній оболонці; десктоп має бічну.
  test.skip(
    (await page.locator('.nd-page').getAttribute('data-layout')) === 'desktop',
    'нижньої навігації немає в десктопній оболонці'
  );

  // З навігацією: тост мусить бути ПОВНІСТЮ вище неї. Раніше це трималося на
  // `bottom: 96` — здогадці про висоту навігації, ні з чим не пов'язаній.
  await page.getByRole('button', { name: 'Сьогодні не було жодного з відстежуваних симптомів' }).click();
  const withNav = await boxOf(page.getByRole('status'));
  const nav = await boxOf(page.locator('.nd-bottom-nav'));
  expectInsideViewport(withNav, page, 'тост над навігацією');
  expect(withNav.y + withNav.height, 'тост перекриває нижню навігацію').toBeLessThanOrEqual(nav.y);

  // Без навігації (під-екран) тост має опуститися, а не висіти на висоті
  // навігації, якої тут немає взагалі.
  await page.getByRole('button', { name: 'Налаштування', exact: true }).click();
  await page.getByRole('button', { name: /Мої симптоми/ }).click();
  await expect(page.getByRole('heading', { name: 'Мої симптоми' })).toBeVisible();
  await expect(page.locator('.nd-bottom-nav')).toHaveCount(0);

  await page.getByRole('button', { name: 'Додати симптом «Тремор» до активного списку' }).click();
  const withoutNav = await boxOf(page.getByRole('status'));
  expectInsideViewport(withoutNav, page, 'тост без навігації');
  expect(withoutNav.y, 'тост без навігації лишився на висоті навігації').toBeGreaterThan(withNav.y);
});

test('високий діалог не виходить за кадр і має шлях закриття', async ({ page }) => {
  await gotoApp(page);
  await page.getByRole('button', { name: 'Налаштування', exact: true }).click();
  await page.getByRole('button', { name: 'Видалення даних' }).click();

  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  expectInsideViewport(await boxOf(dialog), page, 'діалог');

  // Кнопка закриття мусить лишатися досяжною: діалог із max-height не має
  // ховати власні дії за нижнім краєм.
  const close = page.getByRole('button', { name: 'Закрити', exact: true });
  expectInsideViewport(await boxOf(close), page, 'кнопка закриття діалогу');
});

test('останній елемент довгого екрана прокручується повністю', async ({ page }) => {
  await gotoApp(page, { tab: 'set' });
  await page.getByRole('button', { name: /Мої симптоми/ }).click();
  await expect(page.getByRole('heading', { name: 'Мої симптоми' })).toBeVisible();

  const add = page.getByRole('button', { name: 'Додати симптом' }).last();
  await add.scrollIntoViewIfNeeded();
  expectInsideViewport(await boxOf(add), page, 'остання кнопка каталогу');
});
