import { describe, expect, it } from 'vitest';
import { insetVars, resolveInsets, resolveViewportHeight, ZERO_INSETS } from './insets';
import type { TelegramWebApp } from './types';

describe('resolveInsets', () => {
  it('додає contentSafeArea до safeArea — саме це геометрія фулскріна на iOS', () => {
    // 47 — виріз, 46 — смуга з «Закрити» і ⌄ ⋯. Максимум дав би 47 і лишив би
    // заголовок під пігулкою: рівно дефект, заради якого все це існує.
    expect(
      resolveInsets(
        { top: 47, bottom: 34, left: 0, right: 0 },
        { top: 46, bottom: 0, left: 0, right: 0 }
      )
    ).toEqual({ top: 93, right: 0, bottom: 34, left: 0 });
  });

  it('поза фулскріном contentSafeArea нульовий, і сума вироджується в апаратний інсет', () => {
    expect(resolveInsets({ top: 47, bottom: 34 }, { top: 0, bottom: 0 })).toEqual({
      top: 47,
      right: 0,
      bottom: 34,
      left: 0
    });
  });

  it('відсутні обидва об’єкти — нулі, а не NaN', () => {
    expect(resolveInsets(undefined, undefined)).toEqual(ZERO_INSETS);
  });

  it('часткові об’єкти доповнюються нулями', () => {
    expect(resolveInsets({ top: 20 }, { left: 5 })).toEqual({
      top: 20,
      right: 0,
      bottom: 0,
      left: 5
    });
  });

  it('сміття від клієнта прирівнюється до нуля, а не тече в CSS', () => {
    // Недійсне значення в calc() робить недійсним усе правило цілком: екран
    // поїхав би замість того, щоб просто не зсунутися.
    const junk = {
      top: Number.NaN,
      right: Number.POSITIVE_INFINITY,
      bottom: -12,
      left: null
    } as unknown as Partial<{ top: number; right: number; bottom: number; left: number }>;
    expect(resolveInsets(junk, undefined)).toEqual(ZERO_INSETS);
  });

  it('дробові значення округлюються — субпіксельний інсет не має сенсу', () => {
    expect(resolveInsets({ top: 46.5 }, { top: 46.4 })).toEqual({
      top: 93,
      right: 0,
      bottom: 0,
      left: 0
    });
  });

  it('нечислові типи не кидають виняток', () => {
    const wrong = { top: '47' } as unknown as Partial<{ top: number }>;
    expect(resolveInsets(wrong, undefined)).toEqual(ZERO_INSETS);
  });
});

describe('insetVars', () => {
  it('дає рівно чотири змінні шару Telegram із суфіксом px', () => {
    expect(insetVars({ top: 93, right: 0, bottom: 34, left: 0 })).toEqual({
      '--nd-tg-top': '93px',
      '--nd-tg-right': '0px',
      '--nd-tg-bottom': '34px',
      '--nd-tg-left': '0px'
    });
  });
});

describe('resolveViewportHeight', () => {
  const app = (over: Partial<TelegramWebApp>): TelegramWebApp => over;

  it('бере viewportStableHeight, а не viewportHeight', () => {
    // viewportHeight стискається під клавіатуру: оболонка стрибала б під час
    // набору нотатки.
    expect(resolveViewportHeight(app({ viewportStableHeight: 844, viewportHeight: 420 }))).toBe(
      '844px'
    );
  });

  it('відмовляється від абсурдної висоти — клієнт міг не встигнути обробити ready()', () => {
    expect(resolveViewportHeight(app({ viewportStableHeight: 0 }))).toBeNull();
    expect(resolveViewportHeight(app({ viewportStableHeight: 120 }))).toBeNull();
    expect(resolveViewportHeight(app({ viewportStableHeight: Number.NaN }))).toBeNull();
    expect(resolveViewportHeight(app({}))).toBeNull();
  });

  it('округлює дробову висоту', () => {
    expect(resolveViewportHeight(app({ viewportStableHeight: 843.6 }))).toBe('844px');
  });
});
