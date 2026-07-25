// Екран парольної фрази — §7 плану.
//
// Основний шлях — згенерована фраза з підтвердженням повторним введенням.
// Власна фраза дозволена як другий варіант із жорстким допуском.
//
// Чого тут немає і не буде: індикатора сили (він створює хибне заспокоєння
// замість твердого правила допуску), підказки, згадки кількості бітів і
// будь-якого файлу відновлення.

import { useMemo, useState } from 'react';
import { generatePassphrase } from '../../crypto/passphrase/generate';
import { validateOwnPassphrase } from '../../crypto/passphrase/policy';
import { WORDS } from '../../crypto/wordlist/uk-4096';
import { SYNC_COPY } from './copy';

export interface PassphraseScreenProps {
  onReady(passphrase: string): void;
  onCancel(): void;
  /**
   * `unlock` — сейф уже існує, і фраза до нього теж.
   *
   * Показати тут генератор означало б запропонувати нову фразу до сейфа, який
   * нею не відкривається, тобто найпряміший шлях втратити серверну копію.
   */
  purpose?: 'create' | 'unlock';
}

export default function PassphraseScreen({
  onReady,
  onCancel,
  purpose = 'create'
}: PassphraseScreenProps) {
  const [mode, setMode] = useState<'generated' | 'own'>(
    purpose === 'unlock' ? 'own' : 'generated'
  );
  const generated = useMemo(() => generatePassphrase(WORDS), []);
  const [confirmation, setConfirmation] = useState('');
  const [own, setOwn] = useState('');
  const [touched, setTouched] = useState(false);

  const ownVerdict = validateOwnPassphrase(own);
  const confirmed = confirmation.trim() === generated;

  const ownError =
    !touched || ownVerdict.ok
      ? null
      : ownVerdict.reason === 'too_few_words'
        ? SYNC_COPY.passphraseOwnTooFewWords
        : SYNC_COPY.passphraseOwnTooShort;

  return (
    <section
      aria-label={purpose === 'unlock' ? SYNC_COPY.unlockTitle : SYNC_COPY.passphraseTitle}
      data-screen-label="passphrase"
    >
      <h2 className="dialog-title">
        {purpose === 'unlock' ? SYNC_COPY.unlockTitle : SYNC_COPY.passphraseTitle}
      </h2>

      {mode === 'generated' ? (
        <div className="dialog-body">
          <p>{SYNC_COPY.passphraseGenerated}</p>
          <p data-testid="generated-passphrase" style={{ fontWeight: 600 }}>
            {generated}
          </p>
          <p>{SYNC_COPY.passphraseWriteDown}</p>
          <label htmlFor="passphrase-confirm">
            {SYNC_COPY.passphraseConfirmPrompt}
          </label>
          <input
            id="passphrase-confirm"
            className="input"
            autoComplete="off"
            spellCheck={false}
            value={confirmation}
            onChange={(event) => setConfirmation(event.target.value)}
          />
          {confirmation !== '' && !confirmed ? (
            <p role="alert">{SYNC_COPY.passphraseMismatch}</p>
          ) : null}
          <button type="button" className="btn" onClick={() => setMode('own')}>
            {SYNC_COPY.passphraseOwn}
          </button>
        </div>
      ) : (
        <div className="dialog-body">
          <label htmlFor="passphrase-own">
            {purpose === 'unlock' ? SYNC_COPY.unlockPrompt : SYNC_COPY.passphraseOwn}
          </label>
          <input
            id="passphrase-own"
            data-testid="passphrase-input"
            className="input"
            autoComplete="off"
            spellCheck={false}
            value={own}
            onChange={(event) => {
              setOwn(event.target.value);
              setTouched(true);
            }}
          />
          {ownError === null ? null : <p role="alert">{ownError}</p>}
        </div>
      )}

      <div className="dialog-actions">
        <button type="button" className="btn" onClick={onCancel}>
          {SYNC_COPY.consentDecline}
        </button>
        <button
          type="button"
          className="btn"
          data-autofocus
          disabled={mode === 'generated' ? !confirmed : !ownVerdict.ok}
          onClick={() => onReady(mode === 'generated' ? generated : own.trim())}
        >
          {SYNC_COPY.passphraseContinue}
        </button>
      </div>
    </section>
  );
}
