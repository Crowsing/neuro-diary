// AppProvider: useReducer + персист + toast-таймер.
// Порт життєвого циклу прототипу: constructor → load (966–972),
// componentDidUpdate → save (974), tset → таймер 2600 мс (1005).

import { createContext, useCallback, useContext, useLayoutEffect, useMemo, useReducer, useState } from 'react';
import type { Dispatch, ReactNode } from 'react';
import type { AppState } from '../lib/types';
import type { Action } from './actions';
import { appReducer } from './reducer';
import { load, save } from './persist';
import { useEnv } from './clock';

export interface AppContextValue {
  state: AppState;
  dispatch: Dispatch<Action>;
  persistenceError: string | null;
  retrySave: () => boolean;
}

const AppContext = createContext<AppContextValue | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  const env = useEnv();
  const reducer = useMemo(() => appReducer(env), [env]);
  const [state, dispatch] = useReducer(reducer, env.now, load);
  const [persistenceError, setPersistenceError] = useState<string | null>(null);

  // Layout effect prevents a success toast from painting when persistence failed.
  useLayoutEffect(() => {
    const result = save(state);
    setPersistenceError(result.ok ? null : 'Не вдалося зберегти зміни в браузері. Дані залишилися в памʼяті цієї вкладки.');
  }, [state]);

  const retrySave = useCallback(() => {
    const result = save(state);
    setPersistenceError(result.ok ? null : 'Не вдалося зберегти зміни в браузері. Дані залишилися в памʼяті цієї вкладки.');
    return result.ok;
  }, [state]);

  // Toast-таймер (tset, рядок 1005): новий текст тосту → авто-приховання через 2600 мс.
  // Зміна тексту перезапускає таймер (cleanup = clearTimeout, як clearTimeout(this._tt)).
  // useLayoutEffect, а не useEffect: cleanup має відпрацювати в тій самій таскі, що й
  // commit, інакше застарілий setTimeout(TOAST_HIDE) може вклинитися до passive-ефекту
  // і сховати щойно показаний тост.
  useLayoutEffect(() => {
    if (state.toast === null) return;
    const tt = setTimeout(() => dispatch({ type: 'TOAST_HIDE' }), 2600);
    return () => clearTimeout(tt);
  }, [state.toast]);

  const value = useMemo<AppContextValue>(
    () => ({ state, dispatch, persistenceError, retrySave }),
    [state, persistenceError, retrySave]
  );
  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

/** Доступ до стану і dispatch: const { state, dispatch } = useApp(). */
export function useApp(): AppContextValue {
  const v = useContext(AppContext);
  if (!v) throw new Error('useApp: AppProvider відсутній вище по дереву');
  return v;
}
