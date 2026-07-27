import { useApp } from '../../../state/store';
import { dialogOpen, navSub } from '../../../state/actions';
import { OB_PRIVACY } from '../../../constants/copy';
import { downloadCsv, downloadJson } from '../../../lib/export';
import SubScreenHeader from '../../frame/SubScreenHeader';

export default function PrivacyScreen() {
  const { state, dispatch } = useApp();
  return (
    <div data-screen-label="Приватність і дані" className="nd-screen nd-screen--tight">
      <SubScreenHeader
        title="Приватність і дані"
        backLabel="Назад"
        onBack={() => dispatch(navSub(null, { tab: 'set' }))}
      />
      {OB_PRIVACY.map((item) => (
        <div key={item.k} style={{ display: 'flex', gap: 12, padding: '12px 14px', borderRadius: 18, background: 'var(--color-surface)' }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--color-accent-2)', flex: 'none', marginTop: 7 }} />
          <div className="nd-row-main">
            <div style={{ fontSize: 14, fontWeight: 600 }}>{item.k}</div>
            <div style={{ fontSize: 13 }} className="text-muted">{item.v}</div>
          </div>
        </div>
      ))}
      <div className="card" style={{ gap: 5 }}>
        <span style={{ fontSize: 14.5, fontWeight: 700 }}>Блокування застосунку</span>
        <span style={{ fontSize: 12.5 }} className="text-muted">Окремий PIN або біометричний lock у вебверсії не реалізовано. Використовуйте блокування пристрою та профілю браузера.</span>
      </div>
      <div className="card" style={{ gap: 6 }}>
        <span className="card-kicker">Локальні toast-підтвердження</span>
        <span style={{ fontSize: 13 }} className="text-muted">Toast може підтвердити щойно виконану дію лише у відкритому інтерфейсі. Він не зберігається, не працює у фоні й не є каналом нагадувань.</span>
      </div>
      <div className="card" style={{ gap: 6 }}>
        <span className="card-kicker">Поточне локальне зберігання</span>
        <span style={{ fontSize: 13 }} className="text-muted">Щоденник зберігається лише в localStorage цього браузера. Синхронізації, фонової передачі та мережевої доставки немає.</span>
      </div>
      <div className="card" style={{ gap: 6 }}>
        <span className="card-kicker">Майбутні Telegram-нагадування</span>
        <span style={{ fontSize: 13 }} className="text-muted">Зовнішні нагадування зараз не надсилаються. Якщо їх буде реалізовано, єдиним каналом стане Telegram, а підключення вимагатиме нової окремої явної згоди. Browser/system push не підтримується.</span>
      </div>
      <div className="card" style={{ gap: 6 }}>
        <span className="card-kicker">Що містять файли</span>
        <span style={{ fontSize: 13 }} className="text-muted">JSON містить повний локальний стан: schemaVersion, записи, явну відсутність, nullable-контекст, групи, привʼязки та налаштування звіту. CSV містить записи дня, явну відсутність і поточну організацію симптомів за групами. PDF містить лише вибраний період і поля; назви груп додаються тільки після явного перемикача.</span>
      </div>
      <div className="card" style={{ gap: 6 }}>
        <span className="card-kicker">Чутливі дані</span>
        <span style={{ fontSize: 13 }} className="text-muted">Назви груп/станів, імʼя та дата народження у звіті є чутливими даними. Імʼя й дата народження не зберігаються між сесіями, але потрапляють у поточний PDF або JSON, якщо ви їх ввели.</span>
      </div>
      <button className="btn btn-secondary" style={{ minHeight: 48 }} onClick={() => downloadCsv(state.data)}>Експортувати CSV записів</button>
      <button className="btn btn-secondary" style={{ minHeight: 48 }} onClick={() => downloadJson(state)}>Експортувати JSON усіх локальних даних</button>
      <button className="btn btn-secondary" style={{ minHeight: 48 }} onClick={() => dispatch(dialogOpen({ type: 'delData' }))}>Видалення даних…</button>
    </div>
  );
}
