// Тост — порт рядків 891–893 прототипу: absolute-оверлей унизу рамки,
// role="status", анімація ndToast (keyframes у styles/app.css).
// sc-if toastF → unmount, коли state.toast === null; авто-приховання
// через 2600 мс робить таймер у state/store.tsx.

import { useApp } from '../../state/store';

export default function Toast() {
  const { state, persistenceError } = useApp();
  if (state.toast === null || persistenceError) return null;
  return (
    <div
      className="nd-toast"
      role="status"
      style={{
        position: 'absolute',
        bottom: 96,
        left: '50%',
        transform: 'translateX(-50%)',
        background: 'var(--color-neutral-800)',
        color: 'var(--color-neutral-100)',
        padding: '11px 20px',
        borderRadius: 999,
        fontSize: '13.5px',
        boxShadow: 'var(--shadow-md)',
        animation: 'ndToast .18s ease-out',
        whiteSpace: 'nowrap',
        maxWidth: 340,
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        zIndex: 60
      }}
    >
      {state.toast}
    </div>
  );
}
