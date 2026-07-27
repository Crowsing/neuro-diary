// Картка нагадувань у Налаштуваннях — обидві гілки прапорця в одному місці.
//
// Гілка «вимкнено» рендерить рівно ту картку, що жила тут до Фази 6: той самий
// клас, той самий текст, нуль інтерактивних елементів. Це не збіг і не
// сумісність заради сумісності — §10 вимагає, щоб чесний стан «недоступно»
// лишався в UI, доки весь delivery path не пройде gate, а
// `e2e/production-controls.spec.ts` тримає це дослівно. Тому текст тут
// **не редагується** разом з увімкненою гілкою.
//
// Прапорець статичний, тож у збірці без нагадувань уся гілка `on` — мертвий код,
// і `assert-bundle.mjs` перевіряє це на артефакті.

import { useSync } from '../../sync/provider';
import { REMINDERS_COPY } from '../copy';

export default function RemindersCard() {
  const sync = useSync();

  if (!sync.remindersEnabled) {
    return (
      <div className="card" style={{ gap: 4 }}>
        <span style={{ fontSize: 14.5, fontWeight: 700 }}>
          {REMINDERS_COPY.unavailableTitle}
        </span>
        <span style={{ fontSize: 12.5 }} className="text-muted">
          {REMINDERS_COPY.unavailableBody}
        </span>
      </div>
    );
  }

  // `remindersActive` тримає провайдер, і `null` там означає «ще не питали
  // сервер», а не «вимкнено». Показувати «вимкнені» до першої відповіді
  // означало б стверджувати про стан акаунта те, чого ми не знаємо.
  const status =
    sync.remindersActive === null
      ? REMINDERS_COPY.cardUnknown
      : sync.remindersActive
        ? REMINDERS_COPY.cardOn
        : REMINDERS_COPY.cardOff;

  return (
    <button
      className="card"
      data-testid="reminders-card"
      onClick={sync.openReminders}
      style={{
        border: 'none',
        font: 'inherit',
        cursor: 'pointer',
        textAlign: 'left',
        width: '100%',
        flexDirection: 'row',
        alignItems: 'center',
        gap: 10
      }}
    >
      <span className="nd-row-main">
        <span style={{ fontWeight: 700, fontSize: 14.5 }}>
          {REMINDERS_COPY.cardTitle}
        </span>
        <span style={{ fontSize: 12.5 }} className="text-muted">
          {status}
        </span>
      </span>
      <svg
        width="14"
        height="14"
        viewBox="0 0 14 14"
        aria-hidden="true"
        style={{ flex: 'none' }}
      >
        <path
          d="M5 2 L10 7 L5 12"
          fill="none"
          stroke="var(--color-accent)"
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </button>
  );
}
