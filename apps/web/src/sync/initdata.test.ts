import { beforeEach, describe, expect, it } from 'vitest';
import {
  type BrowserEnv,
  peekInitData,
  readInitDataOnce,
  resetInitDataForTests
} from './initdata';

const RAW = 'auth_date=1768435200&user=%7B%22id%22%3A42%7D&signature=abc';

function env(over: Partial<BrowserEnv> = {}): BrowserEnv & {
  readonly calls: { url: string }[];
  readonly session: Map<string, string>;
} {
  const calls: { url: string }[] = [];
  const session = new Map<string, string>();
  const location = { hash: '', href: 'https://app.invalid/diary' };
  return {
    calls,
    session,
    location,
    history: {
      replaceState(_state: unknown, _title: string, url: string): void {
        calls.push({ url });
        location.hash = '';
        location.href = url;
      }
    },
    sessionStorage: {
      getItem: (key: string) => session.get(key) ?? null,
      removeItem: (key: string) => void session.delete(key)
    },
    ...over
  };
}

beforeEach(() => {
  resetInitDataForTests();
});

describe('readInitDataOnce — §8 hygiene', () => {
  it('reads the value out of the location fragment', () => {
    const browser = env({
      location: { hash: `#tgWebAppData=${encodeURIComponent(RAW)}`, href: 'https://app.invalid/diary#tgWebAppData=x' }
    });
    expect(readInitDataOnce(browser)).toBe(RAW);
  });

  it('clears the fragment immediately', () => {
    const browser = env({
      location: {
        hash: `#tgWebAppData=${encodeURIComponent(RAW)}`,
        href: 'https://app.invalid/diary#tgWebAppData=x'
      }
    });
    readInitDataOnce(browser);
    expect(browser.calls).toHaveLength(1);
    expect(browser.calls[0].url).toBe('https://app.invalid/diary');
    expect(browser.calls[0].url).not.toContain('tgWebAppData');
  });

  it('removes __telegram__initParams from sessionStorage', () => {
    const browser = env();
    browser.session.set(
      '__telegram__initParams',
      JSON.stringify({ tgWebAppData: RAW })
    );
    expect(readInitDataOnce(browser)).toBe(RAW);
    expect(browser.session.has('__telegram__initParams')).toBe(false);
  });

  it('prefers the value the Telegram SDK already parsed', () => {
    const browser = env({ telegramInitData: RAW });
    browser.session.set(
      '__telegram__initParams',
      JSON.stringify({ tgWebAppData: 'stale' })
    );
    expect(readInitDataOnce(browser)).toBe(RAW);
  });

  it('reads exactly once and serves the same value afterwards', () => {
    const browser = env({ telegramInitData: RAW });
    expect(readInitDataOnce(browser)).toBe(RAW);
    expect(readInitDataOnce(browser)).toBe(RAW);
    // Друге читання не чіпає ані історію, ані сховище.
    expect(browser.calls).toHaveLength(1);
  });

  it('returns null when there is nothing to read, and still scrubs', () => {
    const browser = env();
    expect(readInitDataOnce(browser)).toBeNull();
    expect(browser.calls).toHaveLength(1);
  });

  it('keeps the value in module memory only', () => {
    const browser = env({ telegramInitData: RAW });
    readInitDataOnce(browser);
    expect(peekInitData()).toBe(RAW);
    expect(browser.session.size).toBe(0);
  });
});

describe('the module never persists initData', () => {
  it('mentions no storage key in its source', async () => {
    const { readFileSync } = await import('node:fs');
    const { fileURLToPath } = await import('node:url');
    const source = readFileSync(
      fileURLToPath(new URL('./initdata.ts', import.meta.url)),
      'utf-8'
    );
    // Коментарі відкидаються: правило про код, а не про текст навколо нього.
    const code = source
      .split('\n')
      .filter((line) => !line.trimStart().startsWith('//'))
      .join('\n');
    expect(code).not.toContain('localStorage');
    expect(code).not.toContain('setItem');
    // Єдина взаємодія зі сховищем — видалення.
    expect(code).toContain('removeItem');
  });
});

describe('перезавантаження вкладки — чому «свіжий initData» не існує', () => {
  /**
   * Модель того, як `telegram-web-app.js` знаходить initData при завантаженні.
   *
   * Він читає ті самі два джерела, що й ми: фрагмент `tgWebAppData` і
   * `__telegram__initParams` у sessionStorage. Іншого носія в нього немає, і
   * методу «видай новий рядок» у Bot API теж — `initData` є знімком моменту
   * запуску Mini App.
   */
  function sdkInitData(location: { hash: string }, session: Map<string, string>): string {
    const fromHash = new URLSearchParams(location.hash.replace(/^#/, '')).get(
      'tgWebAppData'
    );
    if (fromHash !== null && fromHash !== '') return fromHash;
    const stored = session.get('__telegram__initParams');
    if (stored === undefined) return '';
    return (JSON.parse(stored) as { tgWebAppData?: string }).tgWebAppData ?? '';
  }

  /** Одна вкладка: `location` і `sessionStorage` переживають перезавантаження. */
  function tab(
    location: { hash: string; href: string },
    session: Map<string, string>
  ): BrowserEnv {
    return {
      location,
      history: {
        replaceState(_state: unknown, _title: string, url: string): void {
          location.hash = '';
          location.href = url;
        }
      },
      sessionStorage: {
        getItem: (key: string) => session.get(key) ?? null,
        removeItem: (key: string) => void session.delete(key)
      },
      telegramInitData: sdkInitData(location, session)
    };
  }

  it('після F5 у SDK не лишається чого віддати — обидва його джерела зачищені', () => {
    // Це і є доказ, що названий у threat-model §3.1 шлях «запросити свіжий
    // initData у Telegram SDK при монтуванні» не є роботою, яку відкладали:
    // такого API немає, а обидва носії, з яких SDK будує значення, прибирає
    // наша ж гігієна §8 — і прибирає навмисно.
    // Telegram кладе значення у фрагмент URL-кодованим — інакше `&` усередині
    // розірвав би параметр на кілька.
    const location = {
      hash: `#tgWebAppData=${encodeURIComponent(RAW)}`,
      href: 'https://app.invalid/diary'
    };
    const session = new Map<string, string>([
      ['__telegram__initParams', JSON.stringify({ tgWebAppData: RAW })]
    ]);

    // Перше завантаження вкладки: значення є, і одразу по прочитанні зникає.
    expect(readInitDataOnce(tab(location, session))).toBe(RAW);

    // Перезавантаження: пам'ять модуля порожня, але сторінка та сама.
    resetInitDataForTests();
    const afterReload = tab(location, session);

    expect(afterReload.telegramInitData).toBe('');
    expect(readInitDataOnce(afterReload)).toBeNull();
    expect(location.href).toBe('https://app.invalid/diary');
    expect(session.size).toBe(0);
  });

  it('і навіть якби рядок уцілів, він уже витрачений: сесію купують один раз', () => {
    // Друга, незалежна причина. Anti-replay §8 віддає сесію щонайбільше один
    // раз на конкретний initData, тож повторна автентифікація тим самим рядком
    // — це 401 `auth_replay`, а не відновлений доступ. Тут це видно як
    // властивість самого модуля: значення НЕ перечитується, а віддається з
    // пам'яті, і другого рядка взятися нізвідки.
    // Telegram кладе значення у фрагмент URL-кодованим — інакше `&` усередині
    // розірвав би параметр на кілька.
    const location = {
      hash: `#tgWebAppData=${encodeURIComponent(RAW)}`,
      href: 'https://app.invalid/diary'
    };
    const session = new Map<string, string>();

    const first = readInitDataOnce(tab(location, session));
    const second = readInitDataOnce(tab(location, session));

    expect(second).toBe(first);
    expect(peekInitData()).toBe(RAW);
  });
});
