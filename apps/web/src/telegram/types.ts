// Поверхня Telegram Mini App, якою користується оболонка.
//
// Оголошено рівно те, що викликається, і НІЧОГО більше: кожен зайвий член тут
// виглядав би як обіцянка, що клієнт його має. Повний перелік — у Bot API,
// цей файл лише фіксує наш контракт із ним.
//
// Усі методи опціональні, і це не перестрахування. Саме завдяки цьому старий
// клієнт (без Bot API 8.0), фейк у Playwright і «SDK не завантажився взагалі»
// зводяться в одну гілку `webApp.expand?.()` без жодного розгалуження.

export interface TelegramInset {
  readonly top: number;
  readonly bottom: number;
  readonly left: number;
  readonly right: number;
}

export interface TelegramWebApp {
  readonly initData?: string;
  readonly platform?: string;
  readonly version?: string;
  readonly isExpanded?: boolean;
  readonly isFullscreen?: boolean;
  readonly viewportHeight?: number;
  readonly viewportStableHeight?: number;
  /** Апаратна безпечна зона: виріз, Dynamic Island, home-indicator. */
  readonly safeAreaInset?: Partial<TelegramInset>;
  /** Смуга, яку перекриває власний UI Telegram: «Закрити», ⌄ ⋯. */
  readonly contentSafeAreaInset?: Partial<TelegramInset>;
  ready?(): void;
  expand?(): void;
  requestFullscreen?(): void;
  exitFullscreen?(): void;
  disableVerticalSwipes?(): void;
  isVersionAtLeast?(version: string): boolean;
  setHeaderColor?(color: string): void;
  setBackgroundColor?(color: string): void;
  setBottomBarColor?(color: string): void;
  onEvent?(event: string, handler: (payload?: unknown) => void): void;
  offEvent?(event: string, handler: (payload?: unknown) => void): void;
}

/**
 * Імена подій `onEvent` — camelCase.
 *
 * Це не стилістика: у самому протоколі postEvent ті самі події звуться
 * snake_case (`safe_area_changed`), і переплутати їх — помилка, яку не ловить
 * ніщо, крім живого пристрою. Тому імена живуть тут одним джерелом, і фейк
 * Telegram у Playwright імпортує саме їх — тоді зелений тест на фейку не може
 * приховати мертву підписку на пристрої.
 */
export const TG_EVENT = {
  safeArea: 'safeAreaChanged',
  contentSafeArea: 'contentSafeAreaChanged',
  fullscreen: 'fullscreenChanged',
  fullscreenFailed: 'fullscreenFailed',
  viewport: 'viewportChanged'
} as const;

/** Значення `error` у `fullscreenFailed` (Bot API 8.0). */
export const TG_FULLSCREEN_ERROR = {
  unsupported: 'UNSUPPORTED',
  alreadyFullscreen: 'ALREADY_FULLSCREEN'
} as const;
