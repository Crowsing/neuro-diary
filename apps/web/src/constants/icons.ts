// Навігація — портовано з docs/prototype/nd-v2.dc.html, vCore:
// NAVD (svg-паси): рядки 1088–1092; LBL і navs: 1093–1095.

import type { Tab } from '../lib/types';

export const NAVD: Record<Tab, string> = {
  today:'M12 3v2 M12 19v2 M3 12h2 M19 12h2 M5.6 5.6l1.4 1.4 M17 17l1.4 1.4 M5.6 18.4l1.4-1.4 M17 7l1.4-1.4 M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8',
  history:'M4 5h16v16H4z M4 10h16 M9 3v4 M15 3v4',
  trends:'M21 7l-7.5 7.5-4-4L3 17 M15 7h6v6',
  report:'M14 3H7a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V8z M14 3v5h5 M9 13h6 M9 17h6',
  set:'M5 21v-6 M5 11V3 M12 21v-9 M12 8V3 M19 21v-4 M19 13V3 M3 15h4 M10 8h4 M17 17h4'
};

export const NAV_LABELS: Record<Tab, string> = {
  today:'Сьогодні',
  history:'Історія',
  trends:'Динаміка',
  report:'Звіт',
  set:'Налаштування'
};

export interface NavItem {
  id: Tab;
  label: string;
  d: string;
}

export const navs: NavItem[] = (['today','history','trends','report','set'] as Tab[])
  .map((id) => ({ id, label: NAV_LABELS[id], d: NAVD[id] }));
