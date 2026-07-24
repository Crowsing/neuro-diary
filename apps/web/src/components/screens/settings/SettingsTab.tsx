import { useApp } from '../../../state/store';
import { dataPatch, dialogOpen, navSub } from '../../../state/actions';
import Toggle from '../../ui/Toggle';

export default function SettingsTab() {
  const { state, dispatch } = useApp();
  const data = state.data;
  const arrow = (
    <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true" style={{ flex: 'none' }}>
      <path d="M5 2 L10 7 L5 12" fill="none" stroke="var(--color-accent)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );

  return (
    <div data-screen-label="Налаштування" style={{ flex: 1, overflowY: 'auto', padding: '8px 20px 20px', display: 'flex', flexDirection: 'column', gap: 13 }}>
      <h2 style={{ fontSize: 24, margin: 0 }}>Налаштування</h2>
      <button
        className="card"
        onClick={() => dispatch(navSub('groups', { tab: 'set' }))}
        style={{ border: 'none', font: 'inherit', cursor: 'pointer', textAlign: 'left', width: '100%', flexDirection: 'row', alignItems: 'center', gap: 10 }}
      >
        <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
          <span style={{ fontWeight: 700, fontSize: 14.5 }}>Групи спостереження</span>
          <span style={{ fontSize: 12.5 }} className="text-muted">
            {data.groups.filter((group) => !group.archived).length} активних · {data.groups.filter((group) => group.archived).length} в архіві
          </span>
        </span>
        {arrow}
      </button>
      <button
        className="card"
        onClick={() => dispatch(navSub('catalog', { catFrom: 'set' }))}
        style={{ border: 'none', font: 'inherit', cursor: 'pointer', textAlign: 'left', width: '100%', flexDirection: 'row', alignItems: 'center', gap: 10 }}
      >
        <span style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 2 }}>
          <span style={{ fontWeight: 700, fontSize: 14.5 }}>Мої симптоми</span>
          <span style={{ fontSize: 12.5 }} className="text-muted">Активний список, каталог, архів, власні симптоми</span>
        </span>
        {arrow}
      </button>

      <div className="card" style={{ gap: 12 }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
          <Toggle selected={data.cycleOn} onClick={() => dispatch(dataPatch({ cycleOn: !data.cycleOn }))} aria="Відстеження циклу" />
          <span style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
            <span style={{ fontSize: 14.5, fontWeight: 700 }}>Відстеження циклу</span>
            <span style={{ fontSize: 12 }} className="text-muted">Зберігається локально, вимкнено у звіті за замовчуванням</span>
          </span>
        </div>
      </div>

      <div className="card" style={{ gap: 4 }}>
        <span style={{ fontSize: 14.5, fontWeight: 700 }}>Нагадування недоступні</span>
        <span style={{ fontSize: 12.5 }} className="text-muted">Застосунок зараз не надсилає фонових повідомлень. Майбутні зовнішні нагадування можливі лише через Telegram і вимагатимуть нової явної згоди.</span>
      </div>

      <button
        className="card"
        onClick={() => dispatch(navSub('privacy'))}
        style={{ border: 'none', font: 'inherit', cursor: 'pointer', textAlign: 'left', width: '100%', flexDirection: 'row', alignItems: 'center', gap: 10 }}
      >
        <span style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 2 }}>
          <span style={{ fontWeight: 700, fontSize: 14.5 }}>Приватність і дані</span>
          <span style={{ fontSize: 12.5 }} className="text-muted">Локальне зберігання, експорт і повне видалення</span>
        </span>
        {arrow}
      </button>

      <button
        className="card"
        onClick={() => dispatch(navSub('safety', { safetyMore: false }))}
        style={{ border: 'none', font: 'inherit', cursor: 'pointer', textAlign: 'left', width: '100%', flexDirection: 'row', alignItems: 'center', gap: 10 }}
      >
        <span style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 2 }}>
          <span style={{ fontWeight: 700, fontSize: 14.5 }}>Коли потрібна термінова допомога?</span>
          <span style={{ fontSize: 12.5 }} className="text-muted">Ключові ознаки і як діяти</span>
        </span>
        {arrow}
      </button>

      <button className="btn btn-secondary" style={{ minHeight: 48 }} onClick={() => dispatch(dialogOpen({ type: 'delData' }))}>Видалення даних</button>
      <span style={{ fontSize: 12 }} className="text-muted">Дані зберігаються лише у вашому браузері (localStorage). Це не захищене сховище — не вводьте реальні медичні дані.</span>
    </div>
  );
}
