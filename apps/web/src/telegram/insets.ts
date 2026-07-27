// Арифметика безпечних зон Telegram.
//
// Чисті функції без DOM — саме тому вони покриті юніт-тестами в
// `environment: node`, а весь бруд (підписки, запис у style) лишається в
// bootstrap.ts. Тут живе єдине місце, де легко помилитися і дорого це
// налагоджувати на пристрої.

import type { TelegramInset, TelegramWebApp } from './types';

export interface Insets {
  readonly top: number;
  readonly right: number;
  readonly bottom: number;
  readonly left: number;
}

export const ZERO_INSETS: Insets = { top: 0, right: 0, bottom: 0, left: 0 };

/**
 * Число від клієнта, приведене до придатного для CSS.
 *
 * Клієнти надсилають і `undefined`, і `null`, і від'ємні значення; будь-що з
 * цього, потрапивши в `calc()`, зробило б відступ або від'ємним, або
 * недійсним — а недійсна змінна ламає все правило цілком, тобто екран
 * поїхав би замість того, щоб просто не зсунутися.
 *
 * Округлення до цілого свідоме: субпіксельний інсет не має сенсу, а
 * `93.33333px` у DevTools лише заважає читати.
 */
function px(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return 0;
  return Math.round(value);
}

/**
 * Сумарний інсет Telegram: safeArea + contentSafeArea.
 *
 * Саме СУМА, а не максимум. Telegram дає два незалежні виміри, і в
 * повноекранному режимі обидва ненульові одночасно:
 *
 *   safeAreaInset.top        — залізо (виріз / Dynamic Island), ≈47px на iOS;
 *   contentSafeAreaInset.top — смуга під ним, яку перекриває власний UI
 *                              Telegram: «Закрити» ліворуч, ⌄ ⋯ праворуч, ≈46px.
 *
 * Контент має починатися нижче ОБОХ, тобто на ≈93px. Максимум дав би 47 і
 * лишив би заголовок під пігулкою «Закрити» — рівно той дефект, з якого все
 * почалося.
 *
 * Зовнішній `env(safe-area-inset-*)` сюди НЕ додається: він міряє той самий
 * апаратний виріз удруге. Поєднання шарів робить CSS через `max()`, див.
 * `--nd-inset-*` в app.css.
 */
export function resolveInsets(
  safeArea: Partial<TelegramInset> | undefined,
  contentSafeArea: Partial<TelegramInset> | undefined
): Insets {
  return {
    top: px(safeArea?.top) + px(contentSafeArea?.top),
    right: px(safeArea?.right) + px(contentSafeArea?.right),
    bottom: px(safeArea?.bottom) + px(contentSafeArea?.bottom),
    left: px(safeArea?.left) + px(contentSafeArea?.left)
  };
}

/** Пари «CSS-змінна → значення» для шару 2 контракту (`--nd-tg-*`). */
export function insetVars(insets: Insets): Record<string, string> {
  return {
    '--nd-tg-top': `${insets.top}px`,
    '--nd-tg-right': `${insets.right}px`,
    '--nd-tg-bottom': `${insets.bottom}px`,
    '--nd-tg-left': `${insets.left}px`
  };
}

/**
 * Найменша висота, яку ми готові взяти від клієнта.
 *
 * Клієнт цілком може назвати 0 до того, як обробив `ready()`. Прийняти це
 * означало б схлопнути оболонку в нуль, а `.nd-page` має `overflow: hidden` —
 * тобто застосунок став би порожнім екраном без жодної помилки в консолі.
 */
const MIN_VIEWPORT_HEIGHT = 200;

/**
 * Висота для `--nd-viewport-h`, або null — тоді лишається `100dvh`.
 *
 * Береться `viewportStableHeight`, а не `viewportHeight`: друга стискається,
 * коли на Android відкривається клавіатура, і оболонка стрибала б просто під
 * час набору нотатки.
 */
export function resolveViewportHeight(webApp: TelegramWebApp): string | null {
  const height = webApp.viewportStableHeight;
  if (typeof height !== 'number' || !Number.isFinite(height)) return null;
  if (height < MIN_VIEWPORT_HEIGHT) return null;
  return `${Math.round(height)}px`;
}
