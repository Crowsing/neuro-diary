import type { ReactNode } from 'react';

export interface ScreenHeaderProps {
  title: string;
  /** Надрядок над заголовком — «Сьогодні» на екрані дня. */
  kicker?: string;
  /** `lead` — дата на «Сьогодні»; `tab` — решта вкладок. */
  size?: 'lead' | 'tab';
  /** Теги і статуси під заголовком. */
  children?: ReactNode;
}

/**
 * Шапка вкладки верхнього рівня.
 *
 * Інсетів не застосовує свідомо: шапка живе всередині скрол-контейнера, тож
 * відступ під виріз тут просто відскролився б. Безпечні зони належать
 * оболонці — див. `.nd-mobile-shell` в app.css.
 */
export default function ScreenHeader({ title, kicker, size = 'tab', children }: ScreenHeaderProps) {
  return (
    <header className="nd-screen-header">
      {kicker !== undefined && <div className="nd-screen-kicker text-muted">{kicker}</div>}
      <h2 className={`nd-title nd-title--${size}`}>{title}</h2>
      {children}
    </header>
  );
}
