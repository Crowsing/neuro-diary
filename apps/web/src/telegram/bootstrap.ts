// Оболонка Telegram Mini App: фулскрін і безпечні зони.
//
// Живе поза React навмисно. Жодному компонентові не треба знати, що він у
// Telegram: усе, що звідси виходить, — це CSS-змінні й data-атрибути на
// <html>, а розкладка гілкується вже в CSS. Тому тут немає ані контексту, ані
// провайдера, ані подвійного виклику в StrictMode, про який довелося б думати.
//
// Залежності передаються портом (`ShellEnv`), як у sync/initdata.ts. Це не
// церемонія: завдяки цьому найважливіша гарантія — «поза Telegram модуль не
// робить НІЧОГО» — перевіряється юніт-тестом у node, а не декларується.

import { insetVars, resolveInsets, resolveViewportHeight } from './insets';
import { TG_EVENT, TG_FULLSCREEN_ERROR, type TelegramWebApp } from './types';

export interface ShellRoot {
  readonly dataset: Record<string, string | undefined>;
  readonly style: { setProperty(property: string, value: string): void };
}

export interface ShellEnv {
  /** null — звичайний браузер, e2e, або SDK не завантажився. */
  readonly webApp: TelegramWebApp | null;
  readonly root: ShellRoot;
  /** Колір тла у форматі #rrggbb; будь-що інше ігнорується. */
  readonly readBackgroundColor: () => string;
  readonly schedule: (callback: () => void, ms: number) => void;
}

/** Bot API 8.0 — версія, з якої існують фулскрін і безпечні зони. */
const FULLSCREEN_API = '8.0';
/** 7.7 — disableVerticalSwipes. */
const SWIPES_API = '7.7';
/** 6.1 — setHeaderColor / setBackgroundColor. */
const COLORS_API = '6.1';
/** 7.10 — setBottomBarColor. */
const BOTTOM_BAR_API = '7.10';

/**
 * Скільки щонайбільше тримати шторку першого кадру.
 *
 * Клієнт, який ніколи не надішле подію про безпечні зони, не має права
 * лишити застосунок порожнім екраном. 400 мс — стеля, а не затримка:
 * у нормальному випадку шторка знімається першою ж подією, тобто за кадр-два.
 */
const INSETS_WATCHDOG_MS = 400;

const HEX_COLOR = /^#[0-9a-f]{6}$/i;

function isFullscreenError(payload: unknown, code: string): boolean {
  return (
    typeof payload === 'object' &&
    payload !== null &&
    (payload as { error?: unknown }).error === code
  );
}

/**
 * Піднімає оболонку Telegram: фулскрін, безпечні зони, висота, кольори.
 *
 * Ідемпотентності не обіцяє й не потребує: викликається один раз із main.tsx
 * до першого рендера.
 */
export function initTelegramShell(env: ShellEnv): void {
  const { webApp, root } = env;

  // Поза Telegram — жодного атрибута, жодної змінної, нуль побічних ефектів.
  // Верстка в цьому випадку тримається на env(safe-area-inset-*) і 100dvh,
  // тобто рівно на тому, що було до появи цього модуля.
  if (webApp === null) return;

  const atLeast = (version: string): boolean => webApp.isVersionAtLeast?.(version) === true;

  root.dataset.tg = '1';
  root.dataset.tgPlatform = webApp.platform ?? 'unknown';
  root.dataset.tgInsets = 'pending';

  let insetsReady = false;
  const markInsetsReady = (): void => {
    if (insetsReady) return;
    insetsReady = true;
    root.dataset.tgInsets = 'ready';
  };

  const applyInsets = (): void => {
    const insets = resolveInsets(webApp.safeAreaInset, webApp.contentSafeAreaInset);
    for (const [name, value] of Object.entries(insetVars(insets))) {
      root.style.setProperty(name, value);
    }
  };

  const applyViewport = (): void => {
    const height = resolveViewportHeight(webApp);
    // Абсурдну висоту ігноруємо мовчки: --nd-viewport-h лишається 100dvh.
    // Записати нуль означало б схлопнути .nd-page, яка має overflow: hidden,
    // тобто показати порожній екран без жодної помилки в консолі.
    if (height !== null) root.style.setProperty('--nd-viewport-h', height);
  };

  const onInsetsChanged = (): void => {
    applyInsets();
    markInsetsReady();
  };

  webApp.ready?.();
  // expand() — і базовий стан, і повний фолбек для клієнтів до 8.0: там
  // фулскріна немає, але розгорнути на всю доступну висоту можна завжди.
  webApp.expand?.();

  applyInsets();
  applyViewport();

  // Підписка ДО requestFullscreen. Інакше перша ж пара подій —
  // fullscreenChanged і contentSafeAreaChanged — може прилетіти раніше за
  // підписку, і застосунок лишиться з інсетами нефулскрінного стану.
  webApp.onEvent?.(TG_EVENT.safeArea, onInsetsChanged);
  webApp.onEvent?.(TG_EVENT.contentSafeArea, onInsetsChanged);
  webApp.onEvent?.(TG_EVENT.viewport, applyViewport);
  webApp.onEvent?.(TG_EVENT.fullscreen, () => {
    root.dataset.tgFullscreen = webApp.isFullscreen === true ? '1' : '0';
    onInsetsChanged();
  });
  webApp.onEvent?.(TG_EVENT.fullscreenFailed, (payload) => {
    // ALREADY_FULLSCREEN — не помилка: ми вже там, куди просилися.
    root.dataset.tgFullscreen = isFullscreenError(payload, TG_FULLSCREEN_ERROR.alreadyFullscreen)
      ? '1'
      : '0';
    // Повторного запиту немає свідомо: на UNSUPPORTED він зациклиться, а на
    // ALREADY_FULLSCREEN безглуздий. Клієнт без фулскріна просто лишається
    // розгорнутим — contentSafeAreaInset там нульовий, і верстка сама
    // вироджується у звичайну геометрію без жодного спецкейсу.
    onInsetsChanged();
  });

  if (atLeast(FULLSCREEN_API)) {
    webApp.requestFullscreen?.();
  } else {
    // Ні фулскріна, ні подій про безпечні зони не буде — тримати шторку
    // нема заради чого.
    markInsetsReady();
  }

  // Знімає перетягування вниз як закриття застосунку. У щоденнику з довгими
  // списками цей жест воює з кожним скролом; закрити Mini App лишається чим —
  // власною кнопкою Telegram.
  if (atLeast(SWIPES_API)) webApp.disableVerticalSwipes?.();

  const background = env.readBackgroundColor();
  // Telegram приймає лише #rrggbb. Якщо стилі ще не застосувалися й токен
  // порожній — краще лишити клієнту його власний колір, ніж дублювати тут
  // копію значення з дизайн-системи, яка потім розійдеться.
  if (HEX_COLOR.test(background)) {
    if (atLeast(COLORS_API)) {
      webApp.setBackgroundColor?.(background);
      webApp.setHeaderColor?.(background);
    }
    if (atLeast(BOTTOM_BAR_API)) webApp.setBottomBarColor?.(background);
  }

  env.schedule(markInsetsReady, INSETS_WATCHDOG_MS);
}
