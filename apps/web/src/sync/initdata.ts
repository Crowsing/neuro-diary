// Клієнтська гігієна initData — §8 плану.
//
// initData читається РІВНО один раз, тримається в пам'яті модуля і ніколи не
// потрапляє ані в state, ані в persist, ані в localStorage. Одразу після
// читання адресний рядок зачищається через `history.replaceState`, а
// `__telegram__initParams` видаляється з sessionStorage — інакше рядок, який
// автентифікує сесію, лишається в історії вкладки й у сховищі назавжди.
//
// Значення ніколи не логується: клієнтська лог-дисципліна дзеркалить серверний
// allowlist §11.

export interface BrowserEnv {
  readonly location: { readonly hash: string; readonly href: string };
  readonly history: { replaceState(state: unknown, title: string, url: string): void };
  readonly sessionStorage: Pick<Storage, 'getItem' | 'removeItem'>;
  readonly telegramInitData?: string;
}

const TELEGRAM_PARAM = '__telegram__initParams';
const HASH_FIELD = 'tgWebAppData';

let cached: string | null = null;
let read = false;

/** Витягує `tgWebAppData` із фрагмента, не чіпаючи решти параметрів. */
function fromHash(hash: string): string | null {
  const raw = hash.startsWith('#') ? hash.slice(1) : hash;
  if (!raw) return null;
  const value = new URLSearchParams(raw).get(HASH_FIELD);
  return value === null || value === '' ? null : value;
}

function fromSessionStorage(env: BrowserEnv): string | null {
  const raw = env.sessionStorage.getItem(TELEGRAM_PARAM);
  if (raw === null) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== 'object' || parsed === null) return null;
    const value = (parsed as Record<string, unknown>)[HASH_FIELD];
    return typeof value === 'string' && value !== '' ? value : null;
  } catch {
    return null;
  }
}

function scrub(env: BrowserEnv): void {
  env.sessionStorage.removeItem(TELEGRAM_PARAM);
  const url = env.location.href.split('#')[0];
  env.history.replaceState(null, '', url);
}

/**
 * Читає initData один раз за життя вкладки і одразу прибирає всі його сліди.
 *
 * Повторний виклик повертає те саме значення з пам'яті модуля й нічого не
 * читає: друге читання після зачистки дало б null і виглядало б як втрата
 * автентифікації.
 */
export function readInitDataOnce(env: BrowserEnv): string | null {
  if (read) return cached;
  read = true;
  cached =
    env.telegramInitData && env.telegramInitData !== ''
      ? env.telegramInitData
      : (fromHash(env.location.hash) ?? fromSessionStorage(env));
  scrub(env);
  return cached;
}

export function peekInitData(): string | null {
  return cached;
}

/** Лише для тестів: скидає пам'ять модуля між сценаріями. */
export function resetInitDataForTests(): void {
  cached = null;
  read = false;
}
