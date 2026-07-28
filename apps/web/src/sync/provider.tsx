// Провайдер синхронізації.
//
// Три правила визначають форму цього файлу:
//
//  * local-only збірка не містить мережевого коду ФІЗИЧНО. Гілки нижче
//    перевіряють статичний прапорець, тож збірка усуває і `await import`, і
//    все, що за ним (§9.6);
//  * доменний стан не змінюється. Екрани sync живуть у власному оверлеї, яким
//    володіє провайдер, а результат злиття потрапляє в reducer наявною дією
//    `syncApply` — жодного нового значення `Sub`, жодної правки `types.ts`;
//  * локальна копія первинна. Патч, що видаляє записи, застосовується лише
//    після явного підтвердження на пристрої (§9.4).

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState
} from 'react';
import type { ReactNode } from 'react';
import { syncApply } from '../state/actions';
import { useApp } from '../state/store';
import type { AppData } from '../lib/types';
import type { VaultFailure, VaultSession } from './vault';
import type { ConsentView, RevokeBody } from './client';
import type { RemindersPort } from '../reminders/client';

export type SyncStage =
  | 'local_only'
  | 'idle'
  | 'consent'
  | 'passphrase'
  | 'working'
  | 'confirm'
  | 'change'
  | 'rekey'
  | 'reminders'
  | 'consents'
  | 'error';

/** Третій вид згоди незалежний від двох інших (§«Обовʼязковий контракт», п. 2). */
export type ConsentKind = 'health_sync' | 'cycle_sync' | 'telegram_reminders';

export interface SyncState {
  readonly stage: SyncStage;
  readonly enabled: boolean;
  readonly active: boolean;
  readonly lastError: VaultFailure | null;
  readonly passphrasePurpose: 'create' | 'unlock';
  readonly pendingDeletions: number;
  readonly consentKind: ConsentKind;
  readonly cycleSync: boolean;
  /** Чи взагалі є шлях нагадувань у цій збірці. */
  readonly remindersEnabled: boolean;
  /** `null` — сервера ще не питали; це не те саме, що «вимкнено». */
  readonly remindersActive: boolean | null;
  /**
   * Чи є чим автентифікуватися. `null` — читання ще не завершилося.
   *
   * initData одноразовий і читається рівно раз за життя вкладки (§8), тож після
   * перезавантаження його немає ані у фрагменті, ані в sessionStorage. Знати це
   * ДО екрана фрази — різниця між чесною відмовою і марно введеною фразою.
   */
  readonly initDataPresent: boolean | null;
}

export interface ConsentGrant {
  readonly kind: string;
  readonly textVersion: string;
  readonly sha256: string;
}

/** Рівно те, що потрібно екрану згод від сесії. */
export interface ConsentsPort {
  ensureSession(): Promise<readonly string[]>;
  listConsents(): Promise<readonly ConsentView[]>;
  revokeConsent(body: RevokeBody): Promise<{ accountErased: boolean }>;
  readonly lastAckedRevision: number;
}

export interface SyncApi extends SyncState {
  openConsent(kind?: ConsentKind): void;
  openChangePassphrase(): void;
  openRekey(): void;
  dismiss(): void;
  acceptConsent(grant: ConsentGrant): void;
  submitPassphrase(passphrase: string): void;
  submitChange(current: string, next: string): void;
  submitRekey(passphrase: string): void;
  confirmPending(apply: boolean): void;
  syncNow(): void;
  requestVaultReset(): void;
  markCycleDeleted(): void;
  openReminders(): void;
  openConsents(): void;
  /** Веде в діалог згоди, запам'ятавши чернетку розкладу з форми. */
  openRemindersConsent(draft: { time: string; timezone: string }): void;
  remindersPort(): Promise<RemindersPort>;
  consentsPort(): Promise<ConsentsPort>;
  noteRemindersChanged(enabled: boolean): void;
  noteConsentsRevoked(kind: string, accountErased: boolean): void;
}

const SYNC_ENABLED = import.meta.env.VITE_SYNC === 'on';

/**
 * Другий статичний прапорець, і він **не** дублює перший.
 *
 * Нагадування ходять сесією синхронізації, тож без неї їх не буває; але зворотне
 * не так: sync-збірка штатно йде без нагадувань, і саме такою пішов би продакшен,
 * доки gate `docs/plans/future-telegram-reminders.md` відкритий. Обидві гілки
 * перевіряються на артефакті (`scripts/assert-bundle.mjs --reminders`).
 */
const REMINDERS_ENABLED =
  SYNC_ENABLED && import.meta.env.VITE_REMINDERS === 'on';

const SyncContext = createContext<SyncApi | null>(null);

/** Порт, який ніколи не викликається: у local-only режимі його нема кому дати. */
const OFFLINE_PORT = () => Promise.reject(new Error('sync_disabled'));

const OFFLINE_API: SyncApi = {
  stage: 'local_only',
  enabled: false,
  active: false,
  lastError: null,
  passphrasePurpose: 'create',
  pendingDeletions: 0,
  consentKind: 'health_sync',
  cycleSync: false,
  remindersEnabled: false,
  remindersActive: null,
  initDataPresent: null,
  openConsent: () => undefined,
  openChangePassphrase: () => undefined,
  openRekey: () => undefined,
  dismiss: () => undefined,
  acceptConsent: () => undefined,
  submitPassphrase: () => undefined,
  submitChange: () => undefined,
  submitRekey: () => undefined,
  confirmPending: () => undefined,
  syncNow: () => undefined,
  requestVaultReset: () => undefined,
  markCycleDeleted: () => undefined,
  openReminders: () => undefined,
  openConsents: () => undefined,
  openRemindersConsent: () => undefined,
  remindersPort: OFFLINE_PORT,
  consentsPort: OFFLINE_PORT,
  noteRemindersChanged: () => undefined,
  noteConsentsRevoked: () => undefined
};

interface Pending {
  readonly confirmedPatch: Partial<AppData>;
  readonly count: number;
}

export function SyncProvider({ children }: { children: ReactNode }) {
  const { state, dispatch } = useApp();
  const [stage, setStage] = useState<SyncStage>(SYNC_ENABLED ? 'idle' : 'local_only');
  const [lastError, setLastError] = useState<VaultFailure | null>(null);
  const [active, setActive] = useState(false);
  const [purpose, setPurpose] = useState<'create' | 'unlock'>('create');
  const [pending, setPending] = useState<Pending | null>(null);
  const [consentKind, setConsentKind] = useState<ConsentKind>('health_sync');
  const [cycleSync, setCycleSync] = useState(false);
  const [remindersActive, setRemindersActive] = useState<boolean | null>(null);
  const [initDataPresent, setInitDataPresent] = useState<boolean | null>(null);
  /**
   * Час і зона, введені у формі до показу тексту згоди.
   *
   * `settings` обов'язкові рівно для `telegram_reminders`, а компонент форми
   * розмонтовується, поки показують текст, — тож чернетка мусить пережити
   * перехід десь поза ним.
   */
  const reminderDraft = useRef<{ time: string; timezone: string }>({
    time: '20:00',
    timezone: ''
  });

  const session = useRef<VaultSession | null>(null);
  const grant = useRef<ConsentGrant | null>(null);
  // Що робити з введеною фразою: створювати/відкривати сейф після згоди — чи
  // просто розблокувати вже налаштований пристрій після перезавантаження.
  const phraseMode = useRef<'enable' | 'unlock'>('enable');
  const deferred = useRef<Record<string, unknown>>({});
  // Асинхронні колбеки інакше захопили б стан того рендеру, у якому їх
  // створили, і push відправляв би застарілий щоденник.
  const data = useRef(state.data);
  data.current = state.data;
  const checkinDate = useRef<string | null>(null);
  checkinDate.current = state.checkin?.date ?? null;

  // §8 вимагає зачистити initData ОДРАЗУ після читання, а не тоді, коли
  // користувачка нарешті вмикає синхронізацію: інакше `#tgWebAppData` і
  // sessionStorage лишаються цілими всю сесію вкладки в тих, хто її так і не
  // ввімкнув. Дефект знайдено незалежним review Фази 2.
  //
  // Прапорця `SYNC_ENABLED` тут свідомо НЕМАЄ, хоч раніше був. Він повертав
  // рівно той самий дефект з іншого боку: local-only збірка не виконувала
  // зачистку взагалі, а з підключенням `telegram-web-app.js` (index.html)
  // Telegram сам почав писати `__telegram__initParams` у sessionStorage — і
  // підписаний рядок автентифікації жив би всю сесію вкладки саме в тій
  // збірці, яка обіцяє не зберігати нічого, крім щоденника.
  //
  // Мережевого коду це не тягне: initdata.ts — чистий розбір фрагмента й
  // sessionStorage, без жодного fetch (перевіряється scripts/assert-bundle.mjs).
  useEffect(() => {
    void import('./initdata').then(({ readInitDataOnce }) => {
      const value = readInitDataOnce({
        location: window.location,
        history: window.history,
        sessionStorage: window.sessionStorage,
        telegramInitData: window.Telegram?.WebApp?.initData
      });
      // Результат зберігається, бо він відповідає на питання «чи є сесія», яке
      // інакше з'ясовується лише після введеної парольної фрази. Саме значення
      // нікуди не потрапляє — ані в стан, ані в persist (§8).
      setInitDataPresent(value !== null);
    });
  }, []);

  const openSession = useCallback(async (): Promise<VaultSession> => {
    // Прапорець статичний, тож у local-only збірці цей рядок стає безумовним
    // `throw`, а все нижче — мертвим кодом, який збірка усуває разом із
    // `import()`. Саме це, а не наміри, робить бандл фізично безмережевим
    // (§9.6, перевіряється `scripts/assert-bundle.mjs`).
    if (!SYNC_ENABLED) throw new Error('sync_disabled');
    if (session.current !== null) return session.current;
    const { createVaultSession } = await import('./vault');
    session.current = createVaultSession({
      origin: import.meta.env.VITE_API_ORIGIN,
      storage: window.localStorage,
      browser: {
        location: window.location,
        history: window.history,
        sessionStorage: window.sessionStorage,
        telegramInitData: window.Telegram?.WebApp?.initData
      }
    });
    return session.current;
  }, []);

  // Пристрій, який уже синхронізувався, після перезавантаження не має виглядати
  // так, ніби синхронізації не було: підключі не персистяться, а факт
  // налаштування — так.
  useEffect(() => {
    if (!SYNC_ENABLED) return;
    void import('./meta').then(({ loadMeta, newDeviceId }) => {
      const meta = loadMeta(window.localStorage, newDeviceId());
      if (meta.lastSuccessfulSyncAt !== null) setActive(true);
    });
  }, []);

  const fail = useCallback((error: unknown) => {
    const failure = (error as { failure?: VaultFailure }).failure;
    setLastError(failure ?? 'server');
    setStage('error');
  }, []);

  /** Патч застосовується лише після того, як його ухвалено як безпечний. */
  const applyOutcome = useCallback(
    (outcome: {
      patch: Partial<AppData>;
      confirmedPatch: Partial<AppData>;
      deferredEntries: Readonly<Record<string, unknown>>;
      prunePaths: readonly string[];
      conflictPaths: readonly string[];
      massDelete: boolean;
      vaultWasReset: boolean;
    }) => {
      dispatch(syncApply(outcome.patch));
      deferred.current = { ...deferred.current, ...outcome.deferredEntries };
      const count =
        outcome.prunePaths.length +
        outcome.conflictPaths.length +
        (outcome.massDelete ? 1 : 0) +
        (outcome.vaultWasReset ? 1 : 0);
      if (count > 0) {
        setPending({ confirmedPatch: outcome.confirmedPatch, count });
        setStage('confirm');
        return false;
      }
      return true;
    },
    [dispatch]
  );

  const runSync = useCallback(async () => {
    const vault = await openSession();
    if (!vault.unlocked) return;

    // §9.3: сервер не вміє мерджити, тож 409 — не помилка, а штатний крок
    // протоколу. Він гарантований щоразу, коли інший пристрій щось записав:
    // manifest іде в кожному push і має власний `record_key`, тож конфліктує
    // завжди. Цикл повторюється, а не показує «спробуйте ще раз».
    for (let round = 0; round < 3; round += 1) {
      const outcome = await vault.pull({
        data: data.current,
        nowMs: Date.now(),
        checkinDate: checkinDate.current
      });
      const clean = applyOutcome(outcome);
      // Push відкладається до рішення користувачки: інакше пристрій закріпив би
      // на сервері той стан, який вона ще не підтвердила.
      if (!clean) return;
      try {
        await vault.push({ data: { ...data.current, ...outcome.patch }, nowMs: Date.now() });
        setStage('idle');
        return;
      } catch (error) {
        if ((error as { failure?: string }).failure !== 'conflict') throw error;
      }
    }
    throw new Error('sync_conflict_unresolved');
  }, [applyOutcome, openSession]);

  const openConsent = useCallback((kind: ConsentKind = 'health_sync') => {
    if (!SYNC_ENABLED) return;
    setLastError(null);
    setConsentKind(kind);
    phraseMode.current = 'enable';
    setStage('consent');
  }, []);

  const openReminders = useCallback(() => {
    if (!REMINDERS_ENABLED) return;
    setLastError(null);
    setStage('reminders');
  }, []);

  const openConsents = useCallback(() => {
    if (!SYNC_ENABLED) return;
    setLastError(null);
    setStage('consents');
  }, []);

  const openRemindersConsent = useCallback(
    (draft: { time: string; timezone: string }) => {
      if (!REMINDERS_ENABLED) return;
      reminderDraft.current = draft;
      openConsent('telegram_reminders');
    },
    [openConsent]
  );

  /**
   * Порт нагадувань: сесія від сейфа плюс дві операції ресурсу.
   *
   * `await import` під статично хибним прапорцем — те саме, що робить
   * `openSession` для sync: у збірці без нагадувань цей рядок недосяжний, і
   * `src/reminders/transport.ts` — єдине місце з адресою `/v1/reminders/...` —
   * не входить у граф модулів узагалі.
   */
  const remindersPort = useCallback(async (): Promise<RemindersPort> => {
    if (!REMINDERS_ENABLED) throw new Error('reminders_disabled');
    const vault = await openSession();
    const caller = vault.caller;
    if (caller === null) throw new Error('reminders_need_http');
    const { createReminderTransport } = await import('../reminders/transport');
    const transport = createReminderTransport(caller);
    return {
      ensureSession: (grant) => vault.ensureSession(grant),
      readReminderSettings: () => transport.readReminderSettings(),
      writeReminderSettings: (body) => transport.writeReminderSettings(body)
    };
  }, [openSession]);

  const consentsPort = useCallback(async (): Promise<ConsentsPort> => {
    if (!SYNC_ENABLED) throw new Error('sync_disabled');
    return openSession();
  }, [openSession]);

  const noteRemindersChanged = useCallback((enabled: boolean) => {
    setRemindersActive(enabled);
  }, []);

  /**
   * Відкликання сталося — і локальних даних це не змінює.
   *
   * Тут навмисно немає жодного `dispatch`: §9.4 забороняє видаляти локальні дані
   * домену з неактивною згодою, і «прибрати зайве» після успішного відкликання
   * було б найтяжчим можливим дефектом цього екрана.
   */
  const noteConsentsRevoked = useCallback((kind: string, accountErased: boolean) => {
    if (kind === 'telegram_reminders') setRemindersActive(null);
    if (kind === 'health_sync' || kind === 'cycle_sync') setCycleSync(false);
    if (kind === 'health_sync' || accountErased) setActive(false);
    if (accountErased) setRemindersActive(null);
  }, []);

  const acceptConsent = useCallback(
    (accepted: ConsentGrant) => {
      grant.current = accepted;
      setStage('working');
      void (async () => {
        try {
          const vault = await openSession();
          const body = {
            kind: accepted.kind,
            text_version: accepted.textVersion,
            text_sha256: accepted.sha256
          };
          if (accepted.kind === 'cycle_sync') {
            await vault.grantCycleSync(body);
            setCycleSync(true);
            setStage('idle');
            return;
          }
          if (accepted.kind === 'telegram_reminders') {
            // Згода й розклад одним викликом: `settings` обов'язкові в самому
            // grant, тож вікна «згода є, розкладу немає» не існує. Сейфа цей
            // шлях не торкається — акаунт може мати самі нагадування.
            const { enableReminders } = await import('../reminders/client');
            const { QUIET_HOURS } = await import('virtual:quiet-hours');
            const view = await enableReminders(
              await remindersPort(),
              body,
              { enabled: true, ...reminderDraft.current },
              QUIET_HOURS
            );
            setRemindersActive(view.enabled);
            setStage('reminders');
            return;
          }
          // Сейф, що вже існує, означає другий пристрій: там потрібна наявна
          // фраза, а не нова. Мережевий виклик тут — уже ПІСЛЯ згоди (§8).
          const mode = await vault.beginEnable(body);
          setPurpose(mode === 'open' ? 'unlock' : 'create');
          setStage('passphrase');
        } catch (error) {
          fail(error);
        }
      })();
    },
    [fail, openSession, remindersPort]
  );

  const submitPassphrase = useCallback(
    (passphrase: string) => {
      setStage('working');
      void (async () => {
        try {
          const vault = await openSession();
          if (phraseMode.current === 'unlock') {
            await vault.open({ passphrase, nowMs: Date.now() });
          } else {
            await vault.finishEnable({
              passphrase,
              data: data.current,
              nowMs: Date.now()
            });
          }
          setActive(true);
          setCycleSync(vault.activeConsents.includes('cycle_sync'));
          await runSync();
          setStage((current) => (current === 'working' ? 'idle' : current));
        } catch (error) {
          fail(error);
        }
      })();
    },
    [fail, openSession, runSync]
  );

  const syncNow = useCallback(() => {
    if (!SYNC_ENABLED) return;
    setStage('working');
    void (async () => {
      try {
        const vault = await openSession();
        setCycleSync(vault.activeConsents.includes('cycle_sync'));
        if (!vault.unlocked) {
          // Порядок питань має значення. Раніше фраза питалася першою, і лише
          // після мільйона ітерацій PBKDF2 і мережевого виклику ставало ясно,
          // що сесії немає взагалі: initData одноразовий, а після
          // перезавантаження вкладки його вже немає ані у фрагменті, ані в
          // sessionStorage (§8). Питати фразу, щоб нею нічого не відкрити, —
          // це змусити людину працювати за наперед відому відмову.
          if (initDataPresent === false) {
            setLastError('step_up_required');
            setStage('error');
            return;
          }
          // Підключі живуть лише в пам'яті, тож після перезавантаження вкладки
          // синхронізація починається з фрази, а не з нової згоди.
          phraseMode.current = 'unlock';
          setPurpose('unlock');
          setStage('passphrase');
          return;
        }
        await runSync();
        setStage((current) => (current === 'working' ? 'idle' : current));
      } catch (error) {
        fail(error);
      }
    })();
  }, [fail, initDataPresent, openSession, runSync]);

  const confirmPending = useCallback(
    (apply: boolean) => {
      const decision = pending;
      setPending(null);
      setStage('working');
      void (async () => {
        try {
          // Патч треба накласти на дані ЯВНО, а не читати `data.current` після
          // dispatch: React ще не перерендерив, тож там лежить стан ДО
          // підтвердження — і push повернув би на сервер саме ті записи, які
          // користувачка щойно погодилася видалити.
          const next =
            apply && decision !== null
              ? { ...data.current, ...decision.confirmedPatch }
              : data.current;
          if (apply && decision !== null) dispatch(syncApply(decision.confirmedPatch));
          const vault = await openSession();
          await vault.push({ data: next, nowMs: Date.now() });
          setStage('idle');
        } catch (error) {
          fail(error);
        }
      })();
    },
    [dispatch, fail, openSession, pending]
  );

  const submitChange = useCallback(
    (current: string, next: string) => {
      setStage('working');
      void (async () => {
        try {
          const vault = await openSession();
          await vault.changePassphrase({ current, next });
          setStage('idle');
        } catch (error) {
          fail(error);
        }
      })();
    },
    [fail, openSession]
  );

  const submitRekey = useCallback(
    (passphrase: string) => {
      setStage('working');
      void (async () => {
        try {
          const vault = await openSession();
          await vault.rekey({ passphrase, data: data.current, nowMs: Date.now() });
          setStage('idle');
        } catch (error) {
          fail(error);
        }
      })();
    },
    [fail, openSession]
  );

  const requestVaultReset = useCallback(() => {
    if (!SYNC_ENABLED) return;
    // Видалення всіх даних мусить супроводжуватися скиданням серверної копії
    // (§9.4) — і саме через автентифіковану сесію, а не через новий транспорт
    // без токена.
    setStage('working');
    void (async () => {
      try {
        const vault = await openSession();
        await vault.resetVault();
        setActive(false);
        setStage('idle');
      } catch (error) {
        fail(error);
      }
    })();
  }, [fail, openSession]);

  const markCycleDeleted = useCallback(() => {
    if (!SYNC_ENABLED) return;
    void (async () => {
      const vault = await openSession();
      // Намір «видалити дані циклу» неможливо відновити зі стану: порожня
      // множина виглядає так само, як прибрані по одній позначки (§9.4).
      vault.markTombstone('cycle');
    })();
  }, [openSession]);

  // Відкладені записи чекають на вихід із чек-іна: застосувати їх раніше
  // означало б розсинхронізувати дзеркало відкритої форми з даними.
  useEffect(() => {
    if (state.checkin !== null) return;
    const held = deferred.current;
    if (Object.keys(held).length === 0) return;
    deferred.current = {};
    dispatch(
      syncApply({ entries: { ...data.current.entries, ...(held as AppData['entries']) } })
    );
  }, [dispatch, state.checkin]);

  const dismiss = useCallback(() => {
    setStage(SYNC_ENABLED ? 'idle' : 'local_only');
    setLastError(null);
  }, []);

  const value = useMemo<SyncApi>(
    () => ({
      stage,
      enabled: SYNC_ENABLED,
      active,
      lastError,
      passphrasePurpose: purpose,
      pendingDeletions: pending?.count ?? 0,
      consentKind,
      cycleSync,
      remindersEnabled: REMINDERS_ENABLED,
      remindersActive,
      initDataPresent,
      openConsent,
      openChangePassphrase: () => setStage('change'),
      openRekey: () => setStage('rekey'),
      dismiss,
      acceptConsent,
      submitPassphrase,
      submitChange,
      submitRekey,
      confirmPending,
      syncNow,
      requestVaultReset,
      markCycleDeleted,
      openReminders,
      openConsents,
      openRemindersConsent,
      remindersPort,
      consentsPort,
      noteRemindersChanged,
      noteConsentsRevoked
    }),
    [
      stage,
      active,
      lastError,
      purpose,
      pending,
      consentKind,
      cycleSync,
      remindersActive,
      initDataPresent,
      openConsent,
      dismiss,
      acceptConsent,
      submitPassphrase,
      submitChange,
      submitRekey,
      confirmPending,
      syncNow,
      requestVaultReset,
      markCycleDeleted,
      openReminders,
      openConsents,
      openRemindersConsent,
      remindersPort,
      consentsPort,
      noteRemindersChanged,
      noteConsentsRevoked
    ]
  );

  return <SyncContext.Provider value={value}>{children}</SyncContext.Provider>;
}

export function useSync(): SyncApi {
  const value = useContext(SyncContext);
  // Локальний режим лишається повністю робочим без провайдера: відсутність
  // синхронізації нічого не ламає (§2).
  return value ?? OFFLINE_API;
}
