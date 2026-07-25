// Канонічний рядок значення — єдина форма, за якою два пристрої можуть
// детерміновано порівняти вміст, не домовляючись заздалегідь.
//
// Ключі об'єктів упорядковані навмисно: `JSON.stringify` зберігає порядок
// вставки, тож те саме значення, зібране в іншому порядку, дало б інший рядок —
// і «однаковий вміст» перестав би бути однаковим.

export function canonicalBody(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'null';
  if (Array.isArray(value)) return `[${value.map(canonicalBody).join(',')}]`;
  const entries = Object.entries(value as Record<string, unknown>)
    .filter(([, item]) => item !== undefined)
    .sort((a, b) => (a[0] < b[0] ? -1 : 1));
  return `{${entries
    .map(([key, item]) => `${JSON.stringify(key)}:${canonicalBody(item)}`)
    .join(',')}}`;
}

/**
 * Детермінований вибір між двома значеннями з РІВНИМИ мітками часу.
 *
 * Без нього поелементний merge §9.3 залежав би від порядку аргументів: два
 * пристрої з однаковим годинником і різним вмістом обрали б різних переможців
 * і розійшлися б назавжди. Тут виграє лексикографічно більший канонічний
 * рядок — правило довільне, але однакове на кожному пристрої, а це єдине, що
 * від tiebreaker'а й потрібно.
 */
export function laterOrGreater<T>(left: T, right: T, at: (value: T) => number): T {
  const leftAt = at(left);
  const rightAt = at(right);
  if (leftAt !== rightAt) return leftAt > rightAt ? left : right;
  return canonicalBody(left) >= canonicalBody(right) ? left : right;
}
