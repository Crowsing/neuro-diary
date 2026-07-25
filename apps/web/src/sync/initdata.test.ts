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
