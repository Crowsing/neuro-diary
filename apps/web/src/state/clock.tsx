// Джерело «сьогодні» і генератора id для всього застосунку.
// ?now=YYYY-MM-DD в URL читається ОДИН раз при старті → фіксована Date
// (для e2e і відтворюваних демо); інакше — поточний час.
// newId відповідає 'c'+Date.now() у прототипі (рядки 1258, 1295).

import { createContext, useContext, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import type { Env } from './reducer';

function initialNow(): Date {
  try {
    const p = new URLSearchParams(window.location.search).get('now');
    if (p && /^\d{4}-\d{2}-\d{2}$/.test(p)) return new Date(p + 'T12:00:00');
  } catch {
    // window недоступний (SSR/тести) — беремо поточний час.
  }
  return new Date();
}

const NowContext = createContext<Env | null>(null);

export function NowProvider({ children, now: nowProp }: { children: ReactNode; now?: Date }) {
  // useState-ініціалізатор: значення фіксується один раз при старті.
  const [now] = useState<Date>(() => nowProp ?? initialNow());
  const env = useMemo<Env>(() => ({ now, newId: () => 'c' + Date.now() }), [now]);
  return <NowContext.Provider value={env}>{children}</NowContext.Provider>;
}

/** Повне оточення {now, newId} — для AppProvider/reducer. */
export function useEnv(): Env {
  const env = useContext(NowContext);
  if (!env) throw new Error('useEnv: NowProvider відсутній вище по дереву');
  return env;
}

/** Фіксована дата «сьогодні» — передавати в isoOff/genDemo/model тощо. */
export function useNow(): Date {
  return useEnv().now;
}

/** Генератор id власних симптомів ('c'+Date.now() у прототипі). */
export function useNewId(): () => string {
  return useEnv().newId;
}
