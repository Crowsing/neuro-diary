// Діалог згоди — §9.2 плану.
//
// Текст береться з кореневого реєстру consent-copy/ і показується дослівно;
// у grant іде хеш саме з реєстру (він же доведений як SHA-256 байтів файлу).
// Діалог локальний і передує будь-якому мережевому виклику: скасування →
// жодного запиту → жодного акаунта (§8).

import { CONSENT_TEXTS } from 'virtual:consent-copy';
import type { ConsentKind } from '../provider';
import { SYNC_COPY } from './copy';

export interface ConsentDialogProps {
  /**
   * Третій вид з'явився у Фазі 6 і нічого тут не змінив, крім типу.
   *
   * Текст `telegram_reminders@0.9` лежав у реєстрі з Фази 2 і вже інлайнився
   * у віртуальний модуль — бракувало саме споживача. Другого діалогу згоди
   * писати не треба було: він показує текст дослівно й не знає, що це за вид.
   */
  kind: ConsentKind;
  onAccept(grant: { kind: string; textVersion: string; sha256: string }): void;
  onDecline(): void;
}

export default function ConsentDialog({
  kind,
  onAccept,
  onDecline
}: ConsentDialogProps) {
  const entry = CONSENT_TEXTS.find((item) => item.kind === kind);
  if (entry === undefined) return null;

  return (
    <section aria-label={SYNC_COPY.consentTitle} data-screen-label="consent">
      <h2 className="dialog-title">{SYNC_COPY.consentTitle}</h2>
      <div className="dialog-body" data-testid="consent-text">
        {entry.body.split('\n').map((line, index) =>
          line.trim() === '' ? null : <p key={index}>{line}</p>
        )}
      </div>
      <div className="dialog-actions">
        <button type="button" className="btn" onClick={onDecline}>
          {SYNC_COPY.consentDecline}
        </button>
        <button
          type="button"
          className="btn"
          data-autofocus
          onClick={() =>
            onAccept({
              kind: entry.kind,
              textVersion: entry.textVersion,
              sha256: entry.sha256
            })
          }
        >
          {SYNC_COPY.consentAccept}
        </button>
      </div>
    </section>
  );
}
