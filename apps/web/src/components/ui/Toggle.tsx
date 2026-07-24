// Тумблер (role="switch") — порт хелпера tg (рядок 1004 прототипу) + розмітки
// шаблону (рядки 328, 332, 470, 479, 495): 50×29, повзунок 23px, left 3px/24px.
// Текстовий підпис у прототипі стоїть ПОРУЧ з тумблером — його рендерить екран;
// label тут використовується лише як запасний aria-label.

import type { CSSProperties } from 'react';

export interface ToggleProps {
  label?: string;
  selected: boolean;
  onClick: () => void;
  aria?: string;
  style?: CSSProperties;
}

export default function Toggle({ label, selected, onClick, aria, style }: ToggleProps) {
  return (
    <button
      role="switch"
      aria-checked={selected}
      aria-label={aria ?? label}
      onClick={onClick}
      style={{
        width: 50,
        height: 44,
        borderRadius: 999,
        border: 'none',
        background: 'transparent',
        position: 'relative',
        cursor: 'pointer',
        flex: 'none',
        padding: 0,
        ...style
      }}
    >
      <span
        aria-hidden="true"
        style={{
          position: 'absolute',
          inset: '7.5px 0',
          borderRadius: 999,
          background: selected ? 'var(--color-accent)' : 'var(--color-divider)'
        }}
      >
        <span
          style={{
            position: 'absolute',
            top: 3,
            left: selected ? 24 : 3,
            width: 23,
            height: 23,
            borderRadius: '50%',
            background: 'var(--color-bg)',
            boxShadow: 'var(--shadow-sm)',
            transition: 'left .15s'
          }}
        />
      </span>
    </button>
  );
}
