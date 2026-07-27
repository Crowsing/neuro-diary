// Перелік чинних згод і відкликання кожної — Art. 7(3).
//
// Дірка тягнулася з Фази 2: `POST /v1/consents/revoke` разом із 409
// `confirm_required` був реалізований і під тестами, але відкликати могла лише
// сюїта, не користувачка. Жодним gate це не блоковане — навпаки, «відкликати
// має бути так само легко, як надати» не виконується, поки шляху немає.
//
// Три відповіді сервера, які не можна зливати в одну:
//
//  * 409 `confirm_required` — на сервері є записи, яких цей пристрій не бачив.
//    Питається один раз, і повтор іде з `acknowledge_incomplete`;
//  * `account_erased: true` — це була остання згода, і акаунт зник разом із нею;
//  * 409 `consent_precondition` — згоду вже відкликано (друга вкладка, воркер
//    заблокованого бота).
//
// Локальних даних цей екран не чіпає взагалі. §9.4 забороняє видаляти дані
// домену з неактивною згодою, і найтяжчий можливий дефект тут — «прибрати
// зайве» після успішного відкликання.

import { useEffect, useState } from 'react';
import { CONSENT_REVOKE_TEXTS } from 'virtual:consent-copy';
import { CONSENTS_COPY } from '../../reminders/copy';
import { consentFailureOf, meansNoAccount } from '../consentFailure';
import { useSync } from '../provider';
import type { ConsentView } from '../client';

const KIND_TITLE: Readonly<Record<string, string>> = {
  health_sync: CONSENTS_COPY.kindHealthSync,
  cycle_sync: CONSENTS_COPY.kindCycleSync,
  telegram_reminders: CONSENTS_COPY.kindTelegramReminders
};

type Phase = 'reading' | 'ready' | 'working' | 'erased';

/** Що показуємо замість переліку, коли сервер відмовив. */
function errorCopy(failure: string): string {
  if (failure === 'offline') return CONSENTS_COPY.errorOffline;
  if (failure === 'unauthenticated' || failure === 'step_up_required') {
    return CONSENTS_COPY.errorStepUp;
  }
  if (failure === 'no_account') return CONSENTS_COPY.errorNoAccount;
  return CONSENTS_COPY.errorServer;
}

export default function ConsentsDialog() {
  const sync = useSync();
  const [phase, setPhase] = useState<Phase>('reading');
  const [consents, setConsents] = useState<readonly ConsentView[]>([]);
  const [target, setTarget] = useState<string | null>(null);
  const [needsAcknowledgement, setNeedsAcknowledgement] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  async function refresh(): Promise<void> {
    try {
      const port = await sync.consentsPort();
      await port.ensureSession();
      setConsents(await port.listConsents());
      setFailure(null);
    } catch (error) {
      // `ensureSession` кидає сирий `SyncError`, у якого немає `failure`, а
      // `VaultError` — навпаки; читати треба обидві форми (`consentFailure.ts`).
      const reason = consentFailureOf(error);
      // `no_account` — не збій: акаунта немає, отже й згод немає. Порожній
      // перелік тут чесніший за слово «помилка».
      if (meansNoAccount(reason)) setConsents([]);
      else setFailure(reason);
    } finally {
      setPhase('ready');
    }
  }

  // Читається один раз на відкриття: `refresh` сама доводить `phase` до 'ready'
  // у `finally`, тож жодного прибирання тут не треба.
  useEffect(() => {
    void refresh();
  }, []);

  async function revoke(kind: string, acknowledge: boolean): Promise<void> {
    setPhase('working');
    setFailure(null);
    try {
      const port = await sync.consentsPort();
      const outcome = await port.revokeConsent({
        kind,
        last_acked_revision: port.lastAckedRevision,
        acknowledge_incomplete: acknowledge
      });
      sync.noteConsentsRevoked(kind, outcome.accountErased);
      if (outcome.accountErased) {
        setConsents([]);
        setTarget(null);
        setPhase('erased');
        return;
      }
      setTarget(null);
      setNeedsAcknowledgement(false);
      await refresh();
    } catch (error) {
      const reason = consentFailureOf(error);
      if (reason === 'confirm_required') {
        // Питається рівно один раз: другий виклик іде з підтвердженням.
        setNeedsAcknowledgement(true);
        setPhase('ready');
        return;
      }
      if (reason === 'consent_precondition') {
        setFailure('already_revoked');
        setNeedsAcknowledgement(false);
        setTarget(null);
        await refresh();
        return;
      }
      setFailure(reason);
      setPhase('ready');
    }
  }

  if (phase === 'reading') {
    return (
      <section aria-label={CONSENTS_COPY.dialogTitle} data-screen-label="consents">
        <h2 className="dialog-title">{CONSENTS_COPY.dialogTitle}</h2>
        <p role="status" data-testid="consents-loading">
          {CONSENTS_COPY.loading}
        </p>
      </section>
    );
  }

  if (phase === 'erased') {
    return (
      <section aria-label={CONSENTS_COPY.dialogTitle} data-screen-label="consents">
        <h2 className="dialog-title">{CONSENTS_COPY.dialogTitle}</h2>
        <div className="dialog-body">
          <p role="status" data-testid="consents-erased">
            {CONSENTS_COPY.erased}
          </p>
        </div>
        <div className="dialog-actions">
          <button type="button" className="btn" data-autofocus onClick={sync.dismiss}>
            {CONSENTS_COPY.close}
          </button>
        </div>
      </section>
    );
  }

  // Підтвердження показує ДОСЛІВНИЙ текст відкликання з реєстру — той самий
  // файл і той самий хеш, що звіряє apps/api. Переказувати його тут своїми
  // словами означало б завести другий текст, якого ніхто не звіряє.
  if (target !== null) {
    const entry = CONSENT_REVOKE_TEXTS.find((item) => item.kind === target);
    const last = consents.length === 1;
    return (
      <section aria-label={CONSENTS_COPY.confirm} data-screen-label="consent-revoke">
        <h2 className="dialog-title">{CONSENTS_COPY.confirm}</h2>
        <div className="dialog-body" data-testid="consent-revoke-text">
          {entry === undefined
            ? null
            : entry.body
                .split('\n')
                .map((line, index) =>
                  line.trim() === '' ? null : <p key={index}>{line}</p>
                )}
          <p>{CONSENTS_COPY.localDataStay}</p>
          {last ? (
            <p role="alert" data-testid="consent-revoke-last">
              {CONSENTS_COPY.lastConsentWarning}
            </p>
          ) : null}
          {needsAcknowledgement ? (
            <p role="alert" data-testid="consent-revoke-incomplete">
              {CONSENTS_COPY.confirmIncomplete}
            </p>
          ) : null}
        </div>
        <div className="dialog-actions">
          <button
            type="button"
            className="btn"
            data-autofocus
            onClick={() => {
              setTarget(null);
              setNeedsAcknowledgement(false);
            }}
          >
            {CONSENTS_COPY.cancel}
          </button>
          <button
            type="button"
            className="btn"
            data-testid="consent-revoke-confirm"
            disabled={phase === 'working'}
            onClick={() => void revoke(target, needsAcknowledgement)}
          >
            {needsAcknowledgement
              ? CONSENTS_COPY.confirmIncompleteAccept
              : CONSENTS_COPY.confirm}
          </button>
        </div>
      </section>
    );
  }

  return (
    <section aria-label={CONSENTS_COPY.dialogTitle} data-screen-label="consents">
      <h2 className="dialog-title">{CONSENTS_COPY.dialogTitle}</h2>
      <div className="dialog-body">
        {failure !== null ? (
          <p role="alert" data-testid="consents-error">
            {failure === 'already_revoked'
              ? CONSENTS_COPY.errorAlreadyRevoked
              : errorCopy(failure)}
          </p>
        ) : null}
        {consents.length === 0 && failure === null ? (
          <p data-testid="consents-empty">{CONSENTS_COPY.empty}</p>
        ) : null}
        {consents.map((consent) => (
          <div
            key={consent.kind}
            data-testid={`consent-row-${consent.kind}`}
            style={{ display: 'flex', alignItems: 'center', gap: 10 }}
          >
            <span className="nd-row-main">
              <span style={{ fontSize: 14.5, fontWeight: 700 }}>
                {KIND_TITLE[consent.kind] ?? consent.kind}
              </span>
              <span style={{ fontSize: 12 }} className="text-muted">
                {CONSENTS_COPY.grantedAt + consent.grantedAt.slice(0, 10)}
              </span>
            </span>
            <button
              type="button"
              className="btn btn-secondary"
              data-testid={`consent-revoke-${consent.kind}`}
              disabled={phase === 'working'}
              onClick={() => {
                setNeedsAcknowledgement(false);
                setFailure(null);
                setTarget(consent.kind);
              }}
            >
              {CONSENTS_COPY.revoke}
            </button>
          </div>
        ))}
      </div>
      <div className="dialog-actions">
        <button type="button" className="btn" data-autofocus onClick={sync.dismiss}>
          {CONSENTS_COPY.close}
        </button>
      </div>
    </section>
  );
}
