// Спільні хелпери e2e: фіксація «сьогодні» через ?now, посів localStorage
// (формат apps/web/src/state/persist.ts: load() мержить збережене поверх
// baseState, data ??= genDemo(now)), клавіатурна навігація для AC7.

import type { Page } from '@playwright/test';
import { genDemo } from '../src/lib/demo';

/** Фіксоване «сьогодні» всіх тестів: четвер, 15 січня 2026. */
export const NOW_ISO = '2026-01-15';
export const APP_URL = '/?now=' + NOW_ISO;
export const STORAGE_KEY = 'nd_demo_v3';

export function demoData() {
  return genDemo(new Date('2026-01-15T12:00:00+02:00'));
}

/**
 * Кладе state у localStorage ДО старту застосунку. Маркер nd_e2e_seeded
 * гарантує, що посів відбувається лише при ПЕРШОМУ завантаженні контексту:
 * після page.reload() зберігається стан, який записав сам застосунок (AC2).
 */
export async function seedState(page: Page, state: Record<string, unknown>): Promise<void> {
  await page.addInitScript(
    ([key, json]) => {
      if (!localStorage.getItem('nd_e2e_seeded')) {
        localStorage.setItem(key, json);
        localStorage.setItem('nd_e2e_seeded', '1');
      }
    },
    [STORAGE_KEY, JSON.stringify(state)] as const
  );
}

/**
 * Відкриває застосунок одразу в режимі app (пропуск онбордингу).
 * data не сіється → load() підставляє genDemo(now) — детерміновані демо-дані.
 */
export async function gotoApp(page: Page, extra: Record<string, unknown> = {}): Promise<void> {
  await seedState(page, { view: 'app', data: demoData(), ...extra });
  await page.goto(APP_URL);
}

/** Посів валідного стану перед симуляцією quota/security failure у setItem. */
export async function gotoWithBrokenStorage(page: Page, state: Record<string, unknown>): Promise<void> {
  await page.addInitScript(
    ([key, json]) => {
      const setItem = Storage.prototype.setItem;
      setItem.call(localStorage, key, json);
      setItem.call(localStorage, 'nd_e2e_seeded', '1');
      Storage.prototype.setItem = () => {
        throw new DOMException('Storage unavailable', 'QuotaExceededError');
      };
    },
    [STORAGE_KEY, JSON.stringify(state)] as const
  );
  await page.goto(APP_URL);
}

/** Поточний збережений стан застосунку (для точних перевірок unknown/absent). */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function readState(page: Page): Promise<any> {
  return page.evaluate((key) => JSON.parse(localStorage.getItem(key) || 'null'), STORAGE_KEY);
}

export interface TabTarget {
  /** Точний textContent.trim() елемента у фокусі. */
  text?: string;
  /** Точний aria-label елемента у фокусі. */
  aria?: string;
}

/**
 * Тисне Tab (або Shift+Tab), поки фокус не стане на елементі з точним текстом
 * або aria-label. Кидає помилку після max натискань — фокус-ціль недосяжна.
 */
export async function tabUntil(
  page: Page,
  target: TabTarget,
  opts: { shift?: boolean; max?: number } = {}
): Promise<void> {
  const max = opts.max ?? 40;
  const key = opts.shift ? 'Shift+Tab' : 'Tab';
  for (let i = 0; i < max; i++) {
    await page.keyboard.press(key);
    const found = await page.evaluate((m) => {
      const el = document.activeElement;
      if (!el || el === document.body) return false;
      if (m.text !== undefined) return (el.textContent || '').trim() === m.text;
      if (m.aria !== undefined) return el.getAttribute('aria-label') === m.aria;
      return false;
    }, target);
    if (found) return;
  }
  throw new Error('tabUntil: фокус-ціль не знайдено: ' + JSON.stringify(target));
}

/** aria-label елемента, що зараз у фокусі (null — нема). */
export async function activeAria(page: Page): Promise<string | null> {
  return page.evaluate(() => document.activeElement?.getAttribute('aria-label') ?? null);
}
