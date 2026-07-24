// Сегмент-кнопка перемикача — порт seg-хелпера vCore (рядки 1086–1087 прототипу)
// і кнопок Mobile/Desktop (рядки 28–29): фон accent/transparent усередині
// спільної рамки-«пігулки» (рамку рендерить батьківський контейнер).

import type { CSSProperties } from 'react';

export interface SegButtonProps {
  label: string;
  selected: boolean;
  onClick: () => void;
  aria?: string;
  style?: CSSProperties;
}

export default function SegButton({ label, selected, onClick, aria, style }: SegButtonProps) {
  return (
    <button
      onClick={onClick}
      aria-label={aria}
      aria-pressed={selected}
      style={{
        border: 'none',
        cursor: 'pointer',
        font: 'inherit',
        fontSize: 13,
        padding: '9px 18px',
        minHeight: 44,
        background: selected ? 'var(--color-accent)' : 'transparent',
        color: selected ? 'var(--color-bg)' : 'var(--color-text)',
        ...style
      }}
    >
      {label}
    </button>
  );
}
