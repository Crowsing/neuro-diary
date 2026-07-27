// Оболонка Telegram: фулскрін і безпечні зони наскрізь, від виклику SDK до
// координат заголовка на екрані.
//
// Telegram підроблено через `page.addInitScript` — він виконується раніше за
// будь-який скрипт сторінки, тож перемагає застабований порожній SDK.
//
// Імена подій НЕ дублюються рядками, а імпортуються з `../src/telegram/types`.
// Це єдиний спосіб не проґавити одну конкретну поломку: `onEvent` приймає
// camelCase, тоді як сам протокол postEvent — snake_case. Розбіжність тут дала б
// зелений тест на фейку і мертву підписку на живому пристрої.

import { test, expect, type Page } from '@playwright/test';
import { demoData, seedState, APP_URL } from './helpers';
import { TG_EVENT, TG_FULLSCREEN_ERROR } from '../src/telegram/types';

interface Inset {
  top: number;
  bottom: number;
  left: number;
  right: number;
}

interface FakeConfig {
  readonly events: Record<string, string>;
  /** null — клієнт підтримує все. Інакше — точний перелік версій. */
  readonly supports: string[] | null;
  readonly platform: string;
  readonly safeAreaInset: Inset;
  readonly contentSafeAreaInset: Inset;
  readonly viewportStableHeight: number;
}

const NO_INSET: Inset = { top: 0, bottom: 0, left: 0, right: 0 };

/** iPhone у фулскріні: 47 — виріз, 46 — смуга «Закрити» і ⌄ ⋯, 34 — home-indicator. */
const IOS_FULLSCREEN = {
  safeAreaInset: { top: 47, bottom: 34, left: 0, right: 0 },
  contentSafeAreaInset: { top: 46, bottom: 0, left: 0, right: 0 },
  expectedTop: 93
} as const;

async function fakeTelegram(page: Page, over: Partial<FakeConfig> = {}): Promise<void> {
  const config: FakeConfig = {
    events: TG_EVENT,
    supports: null,
    platform: 'ios',
    safeAreaInset: NO_INSET,
    contentSafeAreaInset: NO_INSET,
    viewportStableHeight: 844,
    ...over
  };

  await page.addInitScript((cfg: FakeConfig) => {
    const calls: string[] = [];
    const listeners = new Map<string, ((payload?: unknown) => void)[]>();
    const track = (name: string) => () => void calls.push(name);

    const webApp = {
      platform: cfg.platform,
      isFullscreen: false,
      viewportStableHeight: cfg.viewportStableHeight,
      safeAreaInset: { ...cfg.safeAreaInset },
      contentSafeAreaInset: { ...cfg.contentSafeAreaInset },
      ready: track('ready'),
      expand: track('expand'),
      requestFullscreen: track('requestFullscreen'),
      exitFullscreen: track('exitFullscreen'),
      disableVerticalSwipes: track('disableVerticalSwipes'),
      setHeaderColor: track('setHeaderColor'),
      setBackgroundColor: track('setBackgroundColor'),
      setBottomBarColor: track('setBottomBarColor'),
      isVersionAtLeast: (version: string) =>
        cfg.supports === null || cfg.supports.includes(version),
      onEvent: (event: string, handler: (payload?: unknown) => void) => {
        calls.push(`onEvent:${event}`);
        listeners.set(event, [...(listeners.get(event) ?? []), handler]);
      },
      offEvent: () => {}
    };

    const scope = window as unknown as Record<string, unknown>;
    scope.Telegram = { WebApp: webApp };
    scope.__ndTgFake = {
      calls,
      emit: (event: string, payload?: unknown) =>
        listeners.get(event)?.forEach((handler) => handler(payload)),
      setInsets: (safeArea: Inset, contentSafeArea: Inset) => {
        webApp.safeAreaInset = { ...safeArea };
        webApp.contentSafeAreaInset = { ...contentSafeArea };
      },
      setFullscreen: (value: boolean) => {
        webApp.isFullscreen = value;
      }
    };
  }, config);
}

interface Fake {
  readonly calls: string[];
  emit(event: string, payload?: unknown): void;
  setInsets(safeArea: Inset, contentSafeArea: Inset): void;
  setFullscreen(value: boolean): void;
}

/**
 * Керує фейком зі сторони тесту.
 *
 * Кожна дія — окремий `page.evaluate`: жодного `new Function` і жодного
 * серіалізованого коду. CSP застосунку не дозволяє `unsafe-eval`, і тест не
 * має права бути єдиним місцем, де це правило порушується.
 */
function fakeOf(page: Page) {
  return {
    calls: () =>
      page.evaluate(() => (window as unknown as { __ndTgFake: Fake }).__ndTgFake.calls.slice()),
    emit: (event: string) =>
      page.evaluate(
        (name) => (window as unknown as { __ndTgFake: Fake }).__ndTgFake.emit(name),
        event
      ),
    emitFailure: (error: string) =>
      page.evaluate(
        ([name, code]) =>
          (window as unknown as { __ndTgFake: Fake }).__ndTgFake.emit(name, { error: code }),
        [TG_EVENT.fullscreenFailed, error] as const
      ),
    setInsets: (safeArea: Inset, contentSafeArea: Inset) =>
      page.evaluate(
        ([a, b]) => (window as unknown as { __ndTgFake: Fake }).__ndTgFake.setInsets(a, b),
        [safeArea, contentSafeArea] as const
      )
  };
}

async function cssVar(page: Page, name: string): Promise<string> {
  return page.evaluate(
    (variable) =>
      getComputedStyle(document.documentElement).getPropertyValue(variable).trim(),
    name
  );
}

async function openApp(page: Page): Promise<void> {
  await seedState(page, { view: 'app', data: demoData() });
  await page.goto(APP_URL);
  await expect(page.getByRole('heading', { name: 'Четвер, 15 січня' })).toBeVisible();
}

test('оболонка сама просить фулскрін, а підписку ставить раніше за запит', async ({ page }) => {
  await fakeTelegram(page);
  await openApp(page);

  const calls = await fakeOf(page).calls();
  expect(calls.indexOf('ready')).toBeLessThan(calls.indexOf('expand'));
  expect(calls.indexOf('expand')).toBeLessThan(calls.indexOf('requestFullscreen'));
  expect(calls).toContain('disableVerticalSwipes');
  expect(calls.filter((call) => call === 'requestFullscreen')).toHaveLength(1);

  // Підписка ДО запиту: інакше перша ж подія про інсети губиться.
  const fullscreen = calls.indexOf('requestFullscreen');
  for (const event of Object.values(TG_EVENT)) {
    expect(calls.indexOf(`onEvent:${event}`), event).toBeGreaterThanOrEqual(0);
    expect(calls.indexOf(`onEvent:${event}`), event).toBeLessThan(fullscreen);
  }

  await expect(page.locator('html')).toHaveAttribute('data-tg', '1');
  await expect(page.locator('html')).toHaveAttribute('data-tg-platform', 'ios');
  await expect(page.locator('html')).toHaveAttribute('data-tg-insets', 'ready');
});

test('інсети зсувають зміст нижче за власний UI Telegram', async ({ page }) => {
  await fakeTelegram(page, IOS_FULLSCREEN);
  await openApp(page);

  // Діагностика: сума saveArea + contentSafeArea.
  expect(await cssVar(page, '--nd-tg-top')).toBe(`${IOS_FULLSCREEN.expectedTop}px`);

  // Головне ж — геометричний інваріант, а не значення змінної: жоден піксель
  // змісту не має опинитися вище за смугу, яку перекриває Telegram.
  const screen = await page.locator('.nd-screen').boundingBox();
  expect(screen).not.toBeNull();
  expect(screen!.y).toBeGreaterThanOrEqual(IOS_FULLSCREEN.expectedTop);

  const heading = await page.getByRole('heading', { name: 'Четвер, 15 січня' }).boundingBox();
  expect(heading).not.toBeNull();
  expect(heading!.y).toBeGreaterThanOrEqual(IOS_FULLSCREEN.expectedTop);
});

test('інсети, що прийшли після першого кадру, доїжджають до розкладки', async ({ page }) => {
  // На пристрої так і буває: клієнт відповідає асинхронно вже після рендера.
  await fakeTelegram(page);
  await openApp(page);
  expect(await cssVar(page, '--nd-tg-top')).toBe('0px');

  await fakeOf(page).setInsets({ top: 59, bottom: 34, left: 0, right: 0 }, { top: 46, bottom: 0, left: 0, right: 0 });
  await fakeOf(page).emit(TG_EVENT.contentSafeArea);

  await expect.poll(() => cssVar(page, '--nd-tg-top')).toBe('105px');
  const screen = await page.locator('.nd-screen').boundingBox();
  expect(screen!.y).toBeGreaterThanOrEqual(105);
});

test('нижній інсет піднімає навігацію над home-indicator', async ({ page }) => {
  await fakeTelegram(page, IOS_FULLSCREEN);
  await openApp(page);
  test.skip(
    (await page.locator('.nd-page').getAttribute('data-layout')) === 'desktop',
    'нижньої навігації немає в десктопній оболонці'
  );

  const nav = await page.locator('.nd-bottom-nav').boundingBox();
  const height = page.viewportSize()!.height;
  expect(nav).not.toBeNull();
  expect(nav!.y + nav!.height).toBeLessThanOrEqual(height - IOS_FULLSCREEN.safeAreaInset.bottom);
});

test('клієнт без підтримки фулскріна лишається робочим', async ({ page }) => {
  await fakeTelegram(page, { supports: ['6.1', '7.7'] });
  await openApp(page);

  const calls = await fakeOf(page).calls();
  expect(calls).toContain('expand');
  expect(calls).not.toContain('requestFullscreen');

  // Шторка не має зависнути на клієнті, який ніколи не надішле подію.
  await expect(page.locator('html')).toHaveAttribute('data-tg-insets', 'ready');
  await page.getByRole('button', { name: 'Історія', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Історія' })).toBeVisible();
});

test('UNSUPPORTED не призводить до ретраю і не ламає застосунок', async ({ page }) => {
  await fakeTelegram(page);
  await openApp(page);
  await fakeOf(page).emitFailure(TG_FULLSCREEN_ERROR.unsupported);

  await expect(page.locator('html')).toHaveAttribute('data-tg-fullscreen', '0');
  await expect(page.locator('html')).toHaveAttribute('data-tg-insets', 'ready');
  expect((await fakeOf(page).calls()).filter((c) => c === 'requestFullscreen')).toHaveLength(1);

  await page.getByRole('button', { name: 'Динаміка', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Динаміка' })).toBeVisible();
});

test('ALREADY_FULLSCREEN не вважається відмовою', async ({ page }) => {
  await fakeTelegram(page);
  await openApp(page);
  await fakeOf(page).emitFailure(TG_FULLSCREEN_ERROR.alreadyFullscreen);
  await expect(page.locator('html')).toHaveAttribute('data-tg-fullscreen', '1');
});

test('вихід із фулскріна вручну не втягує користувачку назад', async ({ page }) => {
  await fakeTelegram(page, IOS_FULLSCREEN);
  await openApp(page);

  // Telegram вийшов із фулскріна: власний UI більше не перекриває зміст, тож
  // contentSafeAreaInset обнуляється, а апаратний виріз лишається.
  await fakeOf(page).setInsets(IOS_FULLSCREEN.safeAreaInset, NO_INSET);
  await fakeOf(page).emit(TG_EVENT.fullscreen);

  await expect.poll(() => cssVar(page, '--nd-tg-top')).toBe('47px');
  expect((await fakeOf(page).calls()).filter((c) => c === 'requestFullscreen')).toHaveLength(1);
  await expect(page.getByRole('heading', { name: 'Четвер, 15 січня' })).toBeVisible();
});

test('поза Telegram оболонка не лишає слідів', async ({ page }) => {
  // Без init-скрипта: SDK застабований порожнім тілом, window.Telegram немає.
  await openApp(page);
  await expect(page.locator('html')).not.toHaveAttribute('data-tg', '1');
  expect(await cssVar(page, '--nd-tg-top')).toBe('0px');

  // Перевіряється саме оболонка, а не екран: у десктопній розкладці власні
  // відступи робочої області додали б своє, і тест міряв би не те.
  const shellPadding = await page.evaluate(() => {
    const shell = document.querySelector('.nd-mobile-shell, .nd-desktop-shell');
    return shell === null ? null : getComputedStyle(shell).paddingTop;
  });
  expect(shellPadding).toBe('0px');
});
