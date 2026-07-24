// Чип вибору — порт хелпера ch (рядок 1003 прототипу) + типового чип-баттона
// шаблону (напр., рядки 337, 386, 847): min-height 44, radius 999, палітра
// accent/surface. Екрани передають точні розміри/шрифт через style за потреби.

import type { CSSProperties } from 'react';

export interface ChipProps {
  label: string;
  selected: boolean;
  onClick: () => void;
  aria?: string;
  style?: CSSProperties;
}

export default function Chip({ label, selected, onClick, aria, style }: ChipProps) {
  return (
    <button
      onClick={onClick}
      aria-label={aria}
      aria-pressed={selected}
      style={{
        minHeight: 44,
        padding: '8px 16px',
        borderRadius: 999,
        font: 'inherit',
        fontSize: '13.5px',
        cursor: 'pointer',
        border: `1.5px solid ${selected ? 'var(--color-accent)' : 'var(--color-divider)'}`,
        background: selected ? 'var(--color-accent)' : 'var(--color-surface)',
        color: selected ? 'var(--color-bg)' : 'var(--color-text)',
        ...style
      }}
    >
      {label}
    </button>
  );
}
