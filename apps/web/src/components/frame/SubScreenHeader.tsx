import type { ReactNode } from 'react';

/**
 * Доступне ім'я кнопки повернення.
 *
 * Юніон, а не рядок: ці значення — контракт e2e, а не копірайтинг. Сім
 * специфікацій шукають кнопку саме за цими іменами (`Назад` при цьому ще й
 * `exact: true`), тож нове формулювання зламало б їх мовчки на етапі
 * виконання. Юніон переносить помилку в `tsc`.
 */
export type BackLabel = 'Назад' | 'Назад до історії' | 'Назад до динаміки' | 'Назад до налаштувань';

export interface SubScreenHeaderProps {
  title: string;
  backLabel: BackLabel;
  onBack: () => void;
  /** Теги в одному рядку із заголовком — «Історичний» і бейджі груп. */
  children?: ReactNode;
}

/**
 * Шапка під-екрана: кнопка повернення плюс заголовок.
 *
 * Замінює сім дослівних копій одного й того самого рядка. Обгортка навколо
 * заголовка не декоративна: на екрані симптому теги мусять переноситися в
 * тому ж flex-рядку, що й `h2`, і без неї той екран регресує.
 */
export default function SubScreenHeader({
  title,
  backLabel,
  onBack,
  children
}: SubScreenHeaderProps) {
  return (
    <header className="nd-subscreen-header">
      <button
        type="button"
        className="btn btn-icon btn-secondary"
        aria-label={backLabel}
        onClick={onBack}
      >
        <svg width="16" height="16" aria-hidden="true">
          <path
            d="M10 2 L4 8 L10 14"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
      <span className="nd-subscreen-title">
        <h2 className="nd-title nd-title--sub">{title}</h2>
        {children}
      </span>
    </header>
  );
}
