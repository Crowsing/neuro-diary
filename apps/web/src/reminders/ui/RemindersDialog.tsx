// Екран нагадувань. Живе стадією оверлея sync, а не значенням `Sub`.
//
// Причина та сама, що записана в `sync/provider.tsx`: доменний стан не
// змінюється, `types.ts` і `persist.ts` лишаються недоторканими, і жодне нове
// значення `Sub` не потрапляє в allowlist персистенції. Ресурс нагадувань не
// входить до `AppData` — це вимога §«Модель API», і `schemaVersion` лишається 4.
//
// Перехідний стан (що ввели, що читаємо, що не вдалося) живе тут, а не в
// провайдері: він не переживає закриття діалогу й нікому більше не потрібен.
//
// Порядок §8 дотриманий буквально: форма локальна й до згоди не робить жодного
// мережевого виклику, а «Увімкнути» веде спершу в діалог згоди.

import { useEffect, useState } from 'react';
import { QUIET_HOURS } from 'virtual:quiet-hours';
import { useSync } from '../../sync/provider';
import { loadReminders, saveReminders, type ReminderFailure } from '../client';
import { REMINDERS_COPY } from '../copy';
import { DEFAULT_TIME, detectTimezone } from '../policy';
import type { ReminderSettingsView } from '../transport';

const ERROR_COPY: Record<ReminderFailure, string> = {
  offline: REMINDERS_COPY.errorOffline,
  unauthenticated: REMINDERS_COPY.errorStepUp,
  consent_required: REMINDERS_COPY.errorConsentRequired,
  no_schedule: REMINDERS_COPY.errorNoSchedule,
  quiet_hours: REMINDERS_COPY.errorQuietHours,
  unknown_timezone: REMINDERS_COPY.errorUnknownTimezone,
  copy_not_frozen: REMINDERS_COPY.errorCopyNotFrozen,
  rate_limited: REMINDERS_COPY.errorRateLimited,
  invalid_time: REMINDERS_COPY.errorInvalidTime,
  server: REMINDERS_COPY.errorServer
};

const TIME_HINT =
  REMINDERS_COPY.timeHintPrefix +
  QUIET_HOURS.end +
  REMINDERS_COPY.timeHintJoin +
  QUIET_HOURS.start +
  REMINDERS_COPY.timeHintSuffix;

type Phase = 'reading' | 'ready' | 'working';

export default function RemindersDialog() {
  const sync = useSync();
  const [phase, setPhase] = useState<Phase>('reading');
  const [view, setView] = useState<ReminderSettingsView | null>(null);
  const [time, setTime] = useState(DEFAULT_TIME);
  const [timezone, setTimezone] = useState(() =>
    detectTimezone({
      resolvedTimeZone: () => Intl.DateTimeFormat().resolvedOptions().timeZone
    })
  );
  const [failure, setFailure] = useState<ReminderFailure | null>(null);

  // Перше читання. `consent_required` тут не помилка, а штатний стан «згоди ще
  // немає»: форма лишається порожньою й пропонує ввімкнути.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const port = await sync.remindersPort();
        const current = await loadReminders(port);
        if (cancelled) return;
        setView(current);
        setTime(current.time);
        setTimezone(current.timezone);
        setPhase('ready');
      } catch (error) {
        if (cancelled) return;
        const reason = (error as { failure?: ReminderFailure }).failure ?? 'server';
        if (reason !== 'consent_required' && reason !== 'no_schedule') {
          setFailure(reason);
        }
        setPhase('ready');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sync]);

  async function run(action: () => Promise<ReminderSettingsView>): Promise<void> {
    setFailure(null);
    setPhase('working');
    try {
      const next = await action();
      setView(next);
      setTime(next.time);
      setTimezone(next.timezone);
      sync.noteRemindersChanged(next.enabled);
    } catch (error) {
      setFailure((error as { failure?: ReminderFailure }).failure ?? 'server');
    } finally {
      setPhase('ready');
    }
  }

  const granted = view !== null;

  if (phase === 'reading') {
    return (
      <section
        aria-label={REMINDERS_COPY.dialogTitle}
        data-screen-label="reminders"
      >
        <h2 className="dialog-title">{REMINDERS_COPY.dialogTitle}</h2>
        <p role="status" data-testid="reminders-loading">
          {REMINDERS_COPY.loading}
        </p>
      </section>
    );
  }

  return (
    <section aria-label={REMINDERS_COPY.dialogTitle} data-screen-label="reminders">
      <h2 className="dialog-title">{REMINDERS_COPY.dialogTitle}</h2>
      <div className="dialog-body">
        {view !== null && !view.enabled && view.botBlocked ? (
          <p role="status" data-testid="reminders-bot-blocked">
            {REMINDERS_COPY.statusBotBlocked}
          </p>
        ) : null}
        {view !== null && !view.enabled && !view.botBlocked ? (
          <p role="status" data-testid="reminders-paused">
            {REMINDERS_COPY.statusPaused}
          </p>
        ) : null}
        {view !== null && view.enabled && view.quietBlocked ? (
          <p role="status" data-testid="reminders-quiet-blocked">
            {REMINDERS_COPY.statusQuietBlocked}
          </p>
        ) : null}

        <label htmlFor="reminders-time">{REMINDERS_COPY.timeLabel}</label>
        <input
          id="reminders-time"
          className="input"
          type="time"
          // `step` лишається хвилинним: секунда в значенні зробила б `HH:mm:ss`,
          // який сервер відхилить патерном.
          step={60}
          value={time}
          onChange={(event) => setTime(event.target.value)}
        />
        <span style={{ fontSize: 12 }} className="text-muted">
          {TIME_HINT}
        </span>

        <label htmlFor="reminders-timezone">{REMINDERS_COPY.timezoneLabel}</label>
        <input
          id="reminders-timezone"
          className="input"
          autoComplete="off"
          spellCheck={false}
          value={timezone}
          onChange={(event) => setTimezone(event.target.value)}
        />

        {failure !== null ? (
          <p role="alert" data-testid="reminders-error">
            {ERROR_COPY[failure]}
          </p>
        ) : null}

        {granted ? (
          <span style={{ fontSize: 12 }} className="text-muted">
            {REMINDERS_COPY.pauseExplain}
          </span>
        ) : null}
      </div>

      <div className="dialog-actions">
        <button type="button" className="btn" onClick={sync.dismiss}>
          {REMINDERS_COPY.close}
        </button>

        {!granted ? (
          <button
            type="button"
            className="btn"
            data-autofocus
            data-testid="reminders-enable"
            disabled={phase === 'working'}
            // Мережі тут немає: спершу діалог згоди, і скасування до нього не
            // лишає на сервері жодного рядка (§8). Чернетка їде провайдерові,
            // бо цей компонент розмонтується, поки показують текст згоди, — а
            // `settings` обов'язкові в самому grant.
            onClick={() => sync.openRemindersConsent({ time, timezone })}
          >
            {REMINDERS_COPY.enable}
          </button>
        ) : (
          <>
            <button
              type="button"
              className="btn"
              data-testid="reminders-save"
              disabled={phase === 'working'}
              onClick={() =>
                void run(async () =>
                  saveReminders(
                    await sync.remindersPort(),
                    { enabled: true, time, timezone },
                    QUIET_HOURS
                  )
                )
              }
            >
              {view.enabled ? REMINDERS_COPY.save : REMINDERS_COPY.resume}
            </button>
            {view.enabled ? (
              <button
                type="button"
                className="btn"
                data-testid="reminders-pause"
                disabled={phase === 'working'}
                onClick={() =>
                  void run(async () =>
                    saveReminders(
                      await sync.remindersPort(),
                      { enabled: false, time, timezone },
                      QUIET_HOURS
                    )
                  )
                }
              >
                {REMINDERS_COPY.pause}
              </button>
            ) : null}
          </>
        )}
      </div>

      {granted ? (
        <div className="dialog-actions">
          <button
            type="button"
            className="btn btn-secondary"
            data-testid="reminders-consents"
            onClick={sync.openConsents}
          >
            {REMINDERS_COPY.manageConsents}
          </button>
        </div>
      ) : null}

      {phase === 'working' ? (
        <p role="status" data-testid="reminders-working">
          {REMINDERS_COPY.saving}
        </p>
      ) : null}
    </section>
  );
}
