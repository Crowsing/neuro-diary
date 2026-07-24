// Нижня навігація — порт рядків 831–838 прототипу docs/prototype/nd-v2.dc.html
// + navs/NAVD/LBL із vCore (рядки 1088–1095, дані — constants/icons.ts).
// Видима лише коли sub === null (умова subNone стоїть в AppShell, як у шаблоні).

import { navs } from '../../constants/icons';
import { useApp } from '../../state/store';
import { navTab } from '../../state/actions';

export default function BottomNav() {
  const { state, dispatch } = useApp();
  return (
    <nav className="nd-bottom-nav" aria-label="Основна навігація" data-navigation="mobile">
      {navs.map((n) => {
        const active = state.tab === n.id;
        return (
          <button
            key={n.id}
            onClick={() => dispatch(navTab(n.id))}
            aria-label={n.label}
            aria-current={active ? 'page' : undefined}
            style={{
              flex: 1,
              minHeight: 52,
              border: 'none',
              background: 'transparent',
              cursor: 'pointer',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: 3,
              font: 'inherit',
              color: active ? 'var(--color-accent)' : 'color-mix(in srgb, var(--color-text) 72%, transparent)',
              padding: '6px 0',
              borderRadius: 14
            }}
          >
            <svg width="23" height="23" viewBox="0 0 24 24" style={{ overflow: 'visible' }} aria-hidden="true">
              <path d={n.d} fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" transform="scale(0.96)" />
            </svg>
            <span style={{ fontSize: '10.5px', fontWeight: active ? '700' : '400' }}>{n.label}</span>
          </button>
        );
      })}
    </nav>
  );
}
