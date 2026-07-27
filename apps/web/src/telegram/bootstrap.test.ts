import { describe, expect, it } from 'vitest';
import { initTelegramShell, type ShellEnv } from './bootstrap';
import { TG_EVENT, TG_FULLSCREEN_ERROR, type TelegramWebApp } from './types';

interface Harness {
  readonly env: ShellEnv;
  /** Імена викликаних методів у порядку виклику, разом із onEvent:<подія>. */
  readonly calls: string[];
  readonly dataset: Record<string, string | undefined>;
  readonly vars: Record<string, string>;
  /** Відкладені колбеки — таймер не тікає сам. */
  readonly timers: { callback: () => void; ms: number }[];
  emit(event: string, payload?: unknown): void;
}

function harness(over: Partial<TelegramWebApp> & { supports?: string[] } = {}): Harness {
  const calls: string[] = [];
  const dataset: Record<string, string | undefined> = {};
  const vars: Record<string, string> = {};
  const timers: { callback: () => void; ms: number }[] = [];
  const listeners = new Map<string, ((payload?: unknown) => void)[]>();
  const { supports, ...webAppOver } = over;

  const track = <T extends unknown[]>(name: string, fn?: (...args: T) => void) =>
    (...args: T) => {
      calls.push(name);
      fn?.(...args);
    };

  const webApp: TelegramWebApp = {
    platform: 'ios',
    safeAreaInset: { top: 47, bottom: 34, left: 0, right: 0 },
    contentSafeAreaInset: { top: 46, bottom: 0, left: 0, right: 0 },
    viewportStableHeight: 844,
    ready: track('ready'),
    expand: track('expand'),
    requestFullscreen: track('requestFullscreen'),
    disableVerticalSwipes: track('disableVerticalSwipes'),
    setHeaderColor: track('setHeaderColor'),
    setBackgroundColor: track('setBackgroundColor'),
    setBottomBarColor: track('setBottomBarColor'),
    isVersionAtLeast: (version: string) => supports === undefined || supports.includes(version),
    onEvent: (event, handler) => {
      calls.push(`onEvent:${event}`);
      listeners.set(event, [...(listeners.get(event) ?? []), handler]);
    },
    offEvent: () => {},
    ...webAppOver
  };

  return {
    calls,
    dataset,
    vars,
    timers,
    emit: (event, payload) => listeners.get(event)?.forEach((handler) => handler(payload)),
    env: {
      webApp,
      root: {
        dataset,
        style: {
          setProperty: (property, value) => {
            vars[property] = value;
          }
        }
      },
      readBackgroundColor: () => '#f5ead8',
      schedule: (callback, ms) => timers.push({ callback, ms })
    }
  };
}

describe('поза Telegram', () => {
  it('не робить нічого — ані змінної, ані атрибута, ані таймера', () => {
    // Це не стилістична перевірка. Саме вона перетворює обіцянку «застосунок
    // лишається повністю робочим у звичайному браузері» на факт: будь-який
    // побічний ефект тут означав би, що оболонка Telegram почала впливати на
    // верстку там, де жодного Telegram немає.
    const setProperty: string[] = [];
    const dataset: Record<string, string | undefined> = {};
    const timers: number[] = [];
    initTelegramShell({
      webApp: null,
      root: { dataset, style: { setProperty: (name) => void setProperty.push(name) } },
      readBackgroundColor: () => '#f5ead8',
      schedule: (_callback, ms) => void timers.push(ms)
    });
    expect(setProperty).toEqual([]);
    expect(dataset).toEqual({});
    expect(timers).toEqual([]);
  });
});

describe('порядок виклику', () => {
  it('ready і expand ідуть до запиту фулскріна', () => {
    const h = harness();
    initTelegramShell(h.env);
    expect(h.calls.indexOf('ready')).toBeLessThan(h.calls.indexOf('expand'));
    expect(h.calls.indexOf('expand')).toBeLessThan(h.calls.indexOf('requestFullscreen'));
  });

  it('підписка на всі події відбувається ДО requestFullscreen', () => {
    // Інакше перша ж пара подій — fullscreenChanged і contentSafeAreaChanged —
    // може прилетіти раніше за підписку, і застосунок лишиться з інсетами
    // нефулскрінного стану, тобто заголовок під пігулкою «Закрити».
    const h = harness();
    initTelegramShell(h.env);
    const fullscreen = h.calls.indexOf('requestFullscreen');
    for (const event of Object.values(TG_EVENT)) {
      expect(h.calls.indexOf(`onEvent:${event}`), event).toBeGreaterThanOrEqual(0);
      expect(h.calls.indexOf(`onEvent:${event}`), event).toBeLessThan(fullscreen);
    }
  });

  it('фулскрін запитується рівно один раз', () => {
    const h = harness();
    initTelegramShell(h.env);
    expect(h.calls.filter((call) => call === 'requestFullscreen')).toHaveLength(1);
  });
});

describe('версійні гейти', () => {
  it('клієнт до 8.0 не отримує requestFullscreen, але отримує expand', () => {
    const h = harness({ supports: ['6.1', '7.7', '7.10'] });
    initTelegramShell(h.env);
    expect(h.calls).toContain('expand');
    expect(h.calls).not.toContain('requestFullscreen');
  });

  it('клієнт до 8.0 знімає шторку одразу — подій про інсети він не надішле', () => {
    const h = harness({ supports: [] });
    initTelegramShell(h.env);
    expect(h.dataset.tgInsets).toBe('ready');
  });

  it('клієнт без isVersionAtLeast не отримує жодного версійного виклику і не падає', () => {
    const h = harness({ isVersionAtLeast: undefined });
    expect(() => initTelegramShell(h.env)).not.toThrow();
    expect(h.calls).toContain('ready');
    expect(h.calls).not.toContain('requestFullscreen');
    expect(h.calls).not.toContain('disableVerticalSwipes');
    expect(h.calls).not.toContain('setBackgroundColor');
  });

  it('setBottomBarColor вимагає 7.10 окремо від решти кольорів', () => {
    const h = harness({ supports: ['6.1', '7.7', '8.0'] });
    initTelegramShell(h.env);
    expect(h.calls).toContain('setBackgroundColor');
    expect(h.calls).not.toContain('setBottomBarColor');
  });
});

describe('змінні й атрибути', () => {
  it('пише суму інсетів і стабільну висоту', () => {
    const h = harness();
    initTelegramShell(h.env);
    expect(h.vars['--nd-tg-top']).toBe('93px');
    expect(h.vars['--nd-tg-bottom']).toBe('34px');
    expect(h.vars['--nd-viewport-h']).toBe('844px');
    expect(h.dataset.tg).toBe('1');
    expect(h.dataset.tgPlatform).toBe('ios');
  });

  it('оновлює інсети на safeAreaChanged і на contentSafeAreaChanged', () => {
    const webApp: TelegramWebApp = { safeAreaInset: { top: 0 }, contentSafeAreaInset: { top: 0 } };
    const h = harness(webApp);
    initTelegramShell(h.env);
    expect(h.vars['--nd-tg-top']).toBe('0px');

    // Так це й відбувається на пристрої: значення приходять асинхронно вже
    // після першого кадру.
    Object.assign(h.env.webApp!, {
      safeAreaInset: { top: 59 },
      contentSafeAreaInset: { top: 46 }
    });
    h.emit(TG_EVENT.contentSafeArea);
    expect(h.vars['--nd-tg-top']).toBe('105px');
  });

  it('абсурдну висоту ігнорує, лишаючи 100dvh із CSS', () => {
    const h = harness({ viewportStableHeight: 0 });
    initTelegramShell(h.env);
    expect(h.vars['--nd-viewport-h']).toBeUndefined();
  });

  it('не чіпає кольори Telegram, якщо токен тла ще не читається', () => {
    const h = harness();
    initTelegramShell({ ...h.env, readBackgroundColor: () => '' });
    expect(h.calls).not.toContain('setBackgroundColor');
    expect(h.calls).not.toContain('setHeaderColor');
  });
});

describe('фулскрін не вдався', () => {
  it('UNSUPPORTED позначається нулем і НЕ призводить до повторного запиту', () => {
    // Ретрай тут зациклюється: клієнт відповість тією самою помилкою.
    const h = harness();
    initTelegramShell(h.env);
    h.emit(TG_EVENT.fullscreenFailed, { error: TG_FULLSCREEN_ERROR.unsupported });
    expect(h.dataset.tgFullscreen).toBe('0');
    expect(h.calls.filter((call) => call === 'requestFullscreen')).toHaveLength(1);
  });

  it('ALREADY_FULLSCREEN — не помилка: ми вже там, куди просилися', () => {
    const h = harness();
    initTelegramShell(h.env);
    h.emit(TG_EVENT.fullscreenFailed, { error: TG_FULLSCREEN_ERROR.alreadyFullscreen });
    expect(h.dataset.tgFullscreen).toBe('1');
  });

  it('будь-яка відмова знімає шторку — інакше застосунок лишився б порожнім', () => {
    const h = harness();
    initTelegramShell(h.env);
    expect(h.dataset.tgInsets).toBe('pending');
    h.emit(TG_EVENT.fullscreenFailed, { error: TG_FULLSCREEN_ERROR.unsupported });
    expect(h.dataset.tgInsets).toBe('ready');
  });

  it('fullscreenChanged веде атрибут за фактичним станом клієнта', () => {
    const h = harness();
    initTelegramShell(h.env);
    Object.assign(h.env.webApp!, { isFullscreen: true });
    h.emit(TG_EVENT.fullscreen);
    expect(h.dataset.tgFullscreen).toBe('1');
    Object.assign(h.env.webApp!, { isFullscreen: false });
    h.emit(TG_EVENT.fullscreen);
    expect(h.dataset.tgFullscreen).toBe('0');
    // І жодної спроби втягнути користувачку назад у фулскрін.
    expect(h.calls.filter((call) => call === 'requestFullscreen')).toHaveLength(1);
  });
});

describe('шторка першого кадру', () => {
  it('знімається першою ж подією про інсети', () => {
    const h = harness();
    initTelegramShell(h.env);
    expect(h.dataset.tgInsets).toBe('pending');
    h.emit(TG_EVENT.safeArea);
    expect(h.dataset.tgInsets).toBe('ready');
  });

  it('сторож знімає її й тоді, коли подія не прийшла ніколи', () => {
    // Клієнт, який мовчить, не має права лишити застосунок порожнім екраном.
    const h = harness();
    initTelegramShell(h.env);
    expect(h.dataset.tgInsets).toBe('pending');
    expect(h.timers).toHaveLength(1);
    expect(h.timers[0].ms).toBeGreaterThan(0);
    h.timers[0].callback();
    expect(h.dataset.tgInsets).toBe('ready');
  });

  it('сторож після зняття нічого не змінює', () => {
    const h = harness();
    initTelegramShell(h.env);
    h.emit(TG_EVENT.safeArea);
    h.timers[0].callback();
    expect(h.dataset.tgInsets).toBe('ready');
  });
});
