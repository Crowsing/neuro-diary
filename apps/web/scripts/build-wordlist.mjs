// Збирання кандидатного словника для генератора парольної фрази (§7).
//
// Запуск (мережа потрібна лише тут; застосунок словника не завантажує):
//   node --experimental-strip-types apps/web/scripts/build-wordlist.mjs
//
// Два джерела, обидва відкриті, обидва зафіксовані в
// src/crypto/wordlist/README.md разом із ліцензіями й датою зрізу:
//
//   1) uk_UA.dic (LibreOffice, MPL 1.1) — питомість слова. Частотні списки з
//      субтитрів рясніють російськими словами; перетин зі словником їх відсікає.
//   2) uk_full.txt (FrequencyWords, MIT) — уживаність. Без неї механічно
//      «правильний» список складається з абцугів і авгітів, тобто фрази, якої
//      неможливо ні запам'ятати, ні продиктувати.
//
// Скрипт детермінований: ті самі зрізи джерел дають той самий файл.
//
// Словник НЕ пройшов локалізаційний review. Доки він його не пройшов, UI не
// оголошує ентропію фрази й не показує індикатор сили (блокер 2 промпту Фази 2).

import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import {
  EXACT_COUNT,
  MAX_LENGTH,
  MIN_LENGTH,
  PREFIX_LENGTH,
  devoicedForm,
  hasAllowedLength,
  hasNoMedicalStem,
  isUkrainianLowercase
} from '../src/crypto/wordlist/filters.ts';

const DICTIONARY_URL =
  'https://raw.githubusercontent.com/LibreOffice/dictionaries/master/uk_UA/uk_UA.dic';
const FREQUENCY_URL =
  'https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/uk/uk_full.txt';

async function fetchText(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`source unavailable: ${url} → ${response.status}`);
  return response.text();
}

const [dictionaryRaw, frequencyRaw] = await Promise.all([
  fetchText(DICTIONARY_URL),
  fetchText(FREQUENCY_URL)
]);

/** Формат hunspell .dic: перший рядок — лічильник, далі `слово/ФЛАГИ`. */
const dictionary = new Set();
for (const line of dictionaryRaw.split('\n').slice(1)) {
  const word = line.trim().split('/')[0].trim();
  if (word) dictionary.add(word);
}

/** Формат частотного списку: `слово частота`, у порядку спадання частоти. */
const ranked = [];
for (const line of frequencyRaw.split('\n')) {
  const [word] = line.split(' ');
  if (!word) continue;
  if (!hasAllowedLength(word)) continue;
  if (!isUkrainianLowercase(word)) continue;
  if (!hasNoMedicalStem(word)) continue;
  if (!dictionary.has(word)) continue;
  ranked.push(word);
}

// Жадібний прохід за спаданням частоти. Перший представник кожного
// 4-літерного префікса — щоб автодоповнення не вимагало вводити слово
// повністю; слова, що збігаються на слух після оглушення дзвінких у кінці,
// пропускаються — інакше продиктувати фразу вголос неможливо однозначно.
const prefixes = new Set();
const sounds = new Set();
const chosen = [];
for (const word of ranked) {
  if (chosen.length === EXACT_COUNT) break;
  const prefix = word.slice(0, PREFIX_LENGTH);
  const sound = devoicedForm(word);
  if (prefixes.has(prefix) || sounds.has(sound)) continue;
  prefixes.add(prefix);
  sounds.add(sound);
  chosen.push(word);
}

if (chosen.length !== EXACT_COUNT) {
  throw new Error(`expected ${EXACT_COUNT} words, sources yielded ${chosen.length}`);
}

chosen.sort();

const target = fileURLToPath(new URL('../src/crypto/wordlist/uk-4096.ts', import.meta.url));
const lines = [];
for (let i = 0; i < chosen.length; i += 8) {
  lines.push(`  ${chosen.slice(i, i + 8).map((word) => `'${word}'`).join(', ')}`);
}

writeFileSync(
  target,
  `// Кандидатний словник для генератора парольної фрази (§7).
//
// ЦЕЙ СПИСОК НЕ ПРОЙШОВ ЛОКАЛІЗАЦІЙНИЙ REVIEW. Механічні фільтри застосовані й
// закриті тестами, але редакторська перевірка (омоформи, слова, що плутаються
// на слух поза правилом оглушення, будь-яка медична чи тривожна конотація) —
// попереду. Доки її немає, UI не оголошує ентропію фрази й не показує
// індикатор сили.
//
// Джерела, ліцензії й процедура — README.md у цьому каталозі.
// Файл згенеровано scripts/build-wordlist.mjs; руками не редагується.

/** Механічні фільтри пройдено; редакторську перевірку — ні. */
export const REVIEWED_BY_LOCALIZATION_EDITOR = false;

/** Рівно ${EXACT_COUNT} слів, ${MIN_LENGTH}–${MAX_LENGTH} літер, унікальний префікс перших ${PREFIX_LENGTH} літер. */
export const WORDS: readonly string[] = [
${lines.join(',\n')}
];
`,
  'utf-8'
);

console.log(`wrote ${chosen.length} words to ${target}`);
