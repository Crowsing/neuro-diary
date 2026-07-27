/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 'on' лише у sync-збірці; local-only бандл не містить мережевого коду. */
  readonly VITE_SYNC: string;
  /** Походження api для connect-src і для запитів; порожнє в local-only. */
  readonly VITE_API_ORIGIN: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

/**
 * Поверхня, яку створює `telegram-web-app.js` (index.html).
 *
 * Опціональна на кожному рівні: поза Telegram скрипт або не виконався, або
 * виконався і нічого не знайшов, тож `window.Telegram?.WebApp` — єдина форма
 * доступу, яка не бреше. Самі інтерфейси живуть у звичайному модулі, щоб їх
 * можна було імпортувати з коду й тестів.
 */
interface Window {
  readonly Telegram?: { readonly WebApp?: import('./telegram/types').TelegramWebApp };
}

/** Реєстр текстів згод, вбудований плагіном збірки з кореневого consent-copy/. */
declare module 'virtual:consent-copy' {
  export interface ConsentTextEntry {
    readonly kind: string;
    readonly textVersion: string;
    readonly locale: string;
    readonly sha256: string;
    readonly frozen: boolean;
    readonly body: string;
  }
  export const CONSENT_TEXTS: readonly ConsentTextEntry[];
}
