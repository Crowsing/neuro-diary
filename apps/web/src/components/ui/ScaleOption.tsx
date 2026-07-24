// Опція сегментованої шкали — порт хелпера so (рядок 1310 прототипу) + баттона
// шкали шаблону (рядки 399, 449, 455): flex:1, min-height 50, radius 14.
// aria у прототипі: «N із M» або «N із M, лейбл» — формує екран і передає пропом.
// Варіант самопочуття 1–10 (рядок 376: min-height 52, radius 16, font-size 18)
// екран задає через style.

import type { CSSProperties } from 'react';

export interface ScaleOptionProps {
  label: string | number;
  selected: boolean;
  onClick: () => void;
  aria?: string;
  style?: CSSProperties;
}

export default function ScaleOption({ label, selected, onClick, aria, style }: ScaleOptionProps) {
  return (
    <button
      onClick={onClick}
      aria-label={aria ?? String(label)}
      aria-pressed={selected}
      style={{
        flex: 1,
        minHeight: 50,
        borderRadius: 14,
        cursor: 'pointer',
        font: 'inherit',
        fontSize: 17,
        fontWeight: 600,
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
