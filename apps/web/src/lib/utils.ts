// Утиліти — портовано з docs/prototype/nd-v2.dc.html: tog (рядок 1002), symDef (рядок 1001).

import type { SymptomDef } from './types';
import { SYM } from '../constants/symptoms';

/** Перемикає наявність v у масиві (tog у прототипі). */
export function toggle<T>(arr: T[], v: T): T[] {
  return arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v];
}

/**
 * Фабрика пошуку визначення симптому (symDef у прототипі):
 * спершу серед базових SYM, потім серед власних (custom).
 */
export function makeSymDef(
  custom: SymptomDef[] | null | undefined
): (id: string) => SymptomDef | undefined {
  return (id) => SYM.find((s) => s.id === id) || (custom || []).find((s) => s.id === id);
}
