// Журнали видалень — §9.3 плану.
//
// Reducer домену знає лише, що елемента більше немає. Merge потребує знати,
// коли саме він зник, інакше пристрій, який був офлайн, додасть його назад
// просто тому, що в нього він ще є. Тому журнали живуть тут, на межі
// серіалізації, і подорожують УСЕРЕДИНІ шифрованого payload.
//
// Обрізання за T_journal обов'язкове й прив'язане до T_tomb сервера: журнал,
// коротший за tombstone-TTL, воскрешав би записи, які сервер уже скомпактив.

import type { Removal } from './types';

/** T_journal = T_tomb = H + Δ = 180 + 7 днів (§6.4). */
export const JOURNAL_TTL_MS = 187 * 24 * 60 * 60 * 1000;

export function pruneJournal(
  journal: readonly Removal[],
  nowMs: number
): readonly Removal[] {
  const horizon = nowMs - JOURNAL_TTL_MS;
  return journal.filter((entry) => entry.removedAt >= horizon);
}

/** Об'єднання журналів: для одного id перемагає найпізніше видалення. */
export function mergeJournals(
  left: readonly Removal[],
  right: readonly Removal[]
): readonly Removal[] {
  const latest = new Map<string, number>();
  for (const entry of [...left, ...right]) {
    const known = latest.get(entry.id);
    if (known === undefined || entry.removedAt > known) {
      latest.set(entry.id, entry.removedAt);
    }
  }
  return [...latest.entries()]
    .map(([id, removedAt]) => ({ id, removedAt }))
    .sort((a, b) => (a.id < b.id ? -1 : 1));
}

export function removedAt(journal: readonly Removal[], id: string): number | null {
  let latest: number | null = null;
  for (const entry of journal) {
    if (entry.id === id && (latest === null || entry.removedAt > latest)) {
      latest = entry.removedAt;
    }
  }
  return latest;
}

/**
 * Чи живий елемент: додавання мусить бути СТРОГО пізнішим за видалення.
 * Рівність вирішується на користь видалення — інакше два пристрої з однаковим
 * годинником розійшлися б назавжди (§9.3).
 */
export function survives(addedAt: number, removal: number | null): boolean {
  return removal === null || addedAt > removal;
}
