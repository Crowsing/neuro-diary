import type { ReactNode } from 'react';
import { navs } from '../../../constants/icons';
import { navTab } from '../../../state/actions';
import { useApp } from '../../../state/store';

export default function DesktopShell({ children }: { children: ReactNode }) {
  const { state, dispatch } = useApp();

  return (
    <div className="nd-desktop-shell">
      <aside className="nd-desktop-sidebar">
        <div className="nd-desktop-brand">Неврологічний щоденник</div>
        {state.view === 'app' && state.sub === null && (
          <nav className="nd-desktop-nav" aria-label="Основна навігація" data-navigation="desktop">
            {navs.map((item) => {
              const active = state.tab === item.id;
              return (
                <button
                  key={item.id}
                  type="button"
                  aria-current={active ? 'page' : undefined}
                  onClick={() => dispatch(navTab(item.id))}
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d={item.d} fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                  <span>{item.label}</span>
                </button>
              );
            })}
          </nav>
        )}
        <p className="nd-desktop-privacy">
          Дані зберігаються локально в цьому браузері. Це не захищене медичне сховище.
        </p>
      </aside>
      <main className="nd-desktop-workspace" aria-label="Вміст застосунку">
        <div className="nd-desktop-stage">{children}</div>
      </main>
    </div>
  );
}
