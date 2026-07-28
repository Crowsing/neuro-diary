// Оверлей екранів синхронізації.
//
// Живе поза доменним станом: провайдер тримає власний `stage`, тож у
// `AppState` не з'являється жодного нового поля, а `types.ts` і `persist.ts`
// лишаються недоторканими.
//
// Кожна відмова має свій нейтральний текст: сервер віддає стабільні ASCII-коди,
// українська копія живе тут (§11), і жоден із текстів не називає ані шляху
// запису, ані згоди.

import { lazy, Suspense, useState } from 'react';
import ConsentDialog from './ConsentDialog';
import ConsentsDialog from './ConsentsDialog';
import PassphraseScreen from './PassphraseScreen';
import { SYNC_COPY } from './copy';
import { useSync } from '../provider';
import type { VaultFailure } from '../vault';

/**
 * Екран нагадувань — за тим самим прапорцем, що й його мережевий шлях.
 *
 * `lazy` тут не про вагу чанка, а про артефакт: у збірці без нагадувань
 * `REMINDERS_ENABLED` статично хибний, тож імпорту немає, модуль лишається без
 * жодного споживача й не потрапляє в бандл. Той самий прийом, що в
 * `SyncMount.tsx` для всього оверлея.
 */
const REMINDERS_ENABLED =
  import.meta.env.VITE_SYNC === 'on' && import.meta.env.VITE_REMINDERS === 'on';

const RemindersDialog = REMINDERS_ENABLED
  ? lazy(() => import('../../reminders/ui/RemindersDialog'))
  : null;

const ERROR_COPY: Record<VaultFailure, string> = {
  offline: SYNC_COPY.errorOffline,
  unauthenticated: SYNC_COPY.errorStepUp,
  no_account: SYNC_COPY.errorNoAccount,
  step_up_required: SYNC_COPY.errorStepUp,
  consent_required: SYNC_COPY.errorConsent,
  gone: SYNC_COPY.errorGone,
  rate_limited: SYNC_COPY.errorRateLimited,
  no_vault_key: SYNC_COPY.errorNoVaultKey,
  bad_passphrase: SYNC_COPY.errorBadPassphrase,
  kdf_params_rejected: SYNC_COPY.errorKdfParams,
  stale_server_copy: SYNC_COPY.errorStale,
  integrity: SYNC_COPY.errorIntegrity,
  conflict: SYNC_COPY.errorConflict,
  locked: SYNC_COPY.errorKeyLocked,
  server: SYNC_COPY.errorServer
};

function PassphrasePair({
  title,
  submitLabel,
  firstLabel,
  secondLabel,
  onSubmit,
  onCancel
}: {
  title: string;
  submitLabel: string;
  firstLabel: string;
  secondLabel: string;
  onSubmit(first: string, second: string): void;
  onCancel(): void;
}) {
  const [first, setFirst] = useState('');
  const [second, setSecond] = useState('');
  return (
    <section aria-label={title} data-screen-label="passphrase-pair">
      <h2 className="dialog-title">{title}</h2>
      <div className="dialog-body">
        <label htmlFor="passphrase-first">{firstLabel}</label>
        <input
          id="passphrase-first"
          className="input"
          autoComplete="off"
          spellCheck={false}
          value={first}
          onChange={(event) => setFirst(event.target.value)}
        />
        <label htmlFor="passphrase-second">{secondLabel}</label>
        <input
          id="passphrase-second"
          className="input"
          autoComplete="off"
          spellCheck={false}
          value={second}
          onChange={(event) => setSecond(event.target.value)}
        />
      </div>
      <div className="dialog-actions">
        <button type="button" className="btn" onClick={onCancel}>
          {SYNC_COPY.consentDecline}
        </button>
        <button
          type="button"
          className="btn"
          data-autofocus
          disabled={first === '' || second === ''}
          onClick={() => onSubmit(first, second)}
        >
          {submitLabel}
        </button>
      </div>
    </section>
  );
}

export default function SyncOverlayHost() {
  const sync = useSync();
  const [rekeyPhrase, setRekeyPhrase] = useState('');

  if (sync.stage === 'local_only' || sync.stage === 'idle') return null;

  // Обгортка не косметична: без неї цей блок був статичним сусідом оболонки
  // висотою 100% усередині .nd-page з overflow:hidden — тобто лягав нижче
  // viewport і обрізався. Увесь потік згоди, парольної фрази й re-key був
  // невидимий, а e2e лишався зеленим, бо Playwright уважає обрізаний елемент
  // із непорожнім боксом видимим.
  return (
    <div className="nd-overlay nd-overlay--sync">
      <div className="dialog" role="dialog" aria-modal="true" data-testid="sync-overlay">
        {sync.stage === 'consent' ? (
          <ConsentDialog
            kind={sync.consentKind}
            onAccept={(grant) => sync.acceptConsent(grant)}
            onDecline={sync.dismiss}
          />
        ) : null}

        {sync.stage === 'reminders' && RemindersDialog !== null ? (
          <Suspense fallback={null}>
            <RemindersDialog />
          </Suspense>
        ) : null}

        {sync.stage === 'consents' ? <ConsentsDialog /> : null}

        {sync.stage === 'passphrase' ? (
          <PassphraseScreen
            purpose={sync.passphrasePurpose}
            onReady={(passphrase) => sync.submitPassphrase(passphrase)}
            onCancel={sync.dismiss}
          />
        ) : null}

        {sync.stage === 'change' ? (
          <PassphrasePair
            title={SYNC_COPY.changeTitle}
            firstLabel={SYNC_COPY.changeCurrent}
            secondLabel={SYNC_COPY.changeNext}
            submitLabel={SYNC_COPY.changeSubmit}
            onSubmit={(current, next) => sync.submitChange(current, next)}
            onCancel={sync.dismiss}
          />
        ) : null}

        {sync.stage === 'rekey' ? (
          <section aria-label={SYNC_COPY.rekeyTitle} data-screen-label="rekey">
            <h2 className="dialog-title">{SYNC_COPY.rekeyTitle}</h2>
            <div className="dialog-body">
              <p>{SYNC_COPY.rekeyExplain}</p>
              <label htmlFor="rekey-passphrase">{SYNC_COPY.changeNext}</label>
              <input
                id="rekey-passphrase"
                className="input"
                autoComplete="off"
                spellCheck={false}
                value={rekeyPhrase}
                onChange={(event) => setRekeyPhrase(event.target.value)}
              />
            </div>
            <div className="dialog-actions">
              <button type="button" className="btn" onClick={sync.dismiss}>
                {SYNC_COPY.consentDecline}
              </button>
              <button
                type="button"
                className="btn"
                data-autofocus
                disabled={rekeyPhrase === ''}
                onClick={() => sync.submitRekey(rekeyPhrase)}
              >
                {SYNC_COPY.rekeySubmit}
              </button>
            </div>
          </section>
        ) : null}

        {sync.stage === 'confirm' ? (
          <section aria-label={SYNC_COPY.confirmPruneTitle} data-screen-label="sync-confirm">
            <h2 className="dialog-title">{SYNC_COPY.confirmPruneTitle}</h2>
            <div className="dialog-body">
              <p data-testid="sync-confirm-body">
                {sync.pendingDeletions > 1
                  ? SYNC_COPY.confirmMassDeleteBody
                  : SYNC_COPY.confirmPruneBody}
              </p>
            </div>
            <div className="dialog-actions">
              <button
                type="button"
                className="btn"
                data-autofocus
                data-testid="sync-confirm-keep"
                onClick={() => sync.confirmPending(false)}
              >
                {SYNC_COPY.confirmKeep}
              </button>
              <button
                type="button"
                className="btn"
                data-testid="sync-confirm-apply"
                onClick={() => sync.confirmPending(true)}
              >
                {SYNC_COPY.confirmApply}
              </button>
            </div>
          </section>
        ) : null}

        {sync.stage === 'working' ? (
          <p role="status" data-testid="sync-working">
            {SYNC_COPY.working}
          </p>
        ) : null}

        {sync.stage === 'error' ? (
          <section aria-label={SYNC_COPY.settingsTitle} data-screen-label="sync-error">
            <p role="alert" data-testid="sync-error">
              {sync.lastError === null ? SYNC_COPY.errorServer : ERROR_COPY[sync.lastError]}
            </p>
            <div className="dialog-actions">
              <button type="button" className="btn" data-autofocus onClick={sync.dismiss}>
                {SYNC_COPY.confirmKeep}
              </button>
            </div>
          </section>
        ) : null}
      </div>
    </div>
  );
}
