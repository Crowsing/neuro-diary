// Провайдер синхронізації.
//
// Два правила визначають форму цього файлу:
//
//  * local-only збірка не містить мережевого коду ФІЗИЧНО. Гілка нижче
//    перевіряє статичний прапорець, тож збірка усуває і `await import`, і все,
//    що за ним (§9.6);
//  * доменний стан не змінюється. Екрани sync живуть у власному оверлеї, яким
//    володіє провайдер, — жодного нового значення `Sub`, жодної правки
//    `types.ts` чи `persist.ts`.

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

export type SyncStage =
  | 'local_only'
  | 'idle'
  | 'consent'
  | 'passphrase'
  | 'working'
  | 'error';

export interface SyncState {
  readonly stage: SyncStage;
  readonly enabled: boolean;
  readonly lastError: string | null;
}

export interface SyncApi extends SyncState {
  openConsent(): void;
  openPassphrase(): void;
  dismiss(): void;
  requestVaultReset(): void;
}

const SYNC_ENABLED = import.meta.env.VITE_SYNC === 'on';

const SyncContext = createContext<SyncApi | null>(null);

export function SyncProvider({ children }: { children: ReactNode }) {
  const [stage, setStage] = useState<SyncStage>(
    SYNC_ENABLED ? 'idle' : 'local_only'
  );
  const [lastError, setLastError] = useState<string | null>(null);

  // §8 вимагає зачистити initData ОДРАЗУ після читання, а не тоді, коли
  // користувачка нарешті вмикає синхронізацію: інакше `#tgWebAppData` і
  // sessionStorage лишаються цілими всю сесію вкладки в тих, хто її так і не
  // ввімкнув. Дефект знайдено незалежним review Фази 2.
  useEffect(() => {
    if (!SYNC_ENABLED) return;
    void import('./initdata').then(({ readInitDataOnce }) => {
      readInitDataOnce({
        location: window.location,
        history: window.history,
        sessionStorage: window.sessionStorage,
        telegramInitData: (
          window as unknown as { Telegram?: { WebApp?: { initData?: string } } }
        ).Telegram?.WebApp?.initData
      });
    });
  }, []);

  const openConsent = useCallback(() => {
    if (!SYNC_ENABLED) return;
    setStage('consent');
  }, []);

  const openPassphrase = useCallback(() => {
    if (!SYNC_ENABLED) return;
    setStage('passphrase');
  }, []);

  const dismiss = useCallback(() => {
    setStage(SYNC_ENABLED ? 'idle' : 'local_only');
    setLastError(null);
  }, []);

  const requestVaultReset = useCallback(() => {
    if (!SYNC_ENABLED) return;
    // Видалення всіх даних мусить супроводжуватися скиданням серверної копії
    // (§9.4). Движок підвантажується лише тут — у local-only збірці цієї гілки
    // не існує після збірки.
    setStage('working');
    void import('./session')
      .then(async ({ createTransport }) => {
        const transport = createTransport(import.meta.env.VITE_API_ORIGIN);
        await transport.vaultReset();
        setStage('idle');
      })
      .catch(() => {
        setLastError('sync_unavailable');
        setStage('error');
      });
  }, []);

  const value = useMemo<SyncApi>(
    () => ({
      stage,
      enabled: SYNC_ENABLED,
      lastError,
      openConsent,
      openPassphrase,
      dismiss,
      requestVaultReset
    }),
    [stage, lastError, openConsent, openPassphrase, dismiss, requestVaultReset]
  );

  return <SyncContext.Provider value={value}>{children}</SyncContext.Provider>;
}

export function useSync(): SyncApi {
  const value = useContext(SyncContext);
  if (value === null) {
    // Локальний режим лишається повністю робочим без провайдера: відсутність
    // синхронізації нічого не ламає (§2).
    return {
      stage: 'local_only',
      enabled: false,
      lastError: null,
      openConsent: () => undefined,
      openPassphrase: () => undefined,
      dismiss: () => undefined,
      requestVaultReset: () => undefined
    };
  }
  return value;
}
