// Механічні фільтри кандидатного словника — блокер 2 промпту Фази 2.
//
// Кожен фільтр експортований окремо, щоб тест ганяв саме цю функцію по
// фактичному файлу словника, а не повторював її логіку. Фільтри механічні за
// визначенням: вони не замінюють локалізаційний review, вони лише роблять
// список придатним для нього.

export const EXACT_COUNT = 4096;
export const MIN_LENGTH = 4;
export const MAX_LENGTH = 9;
export const PREFIX_LENGTH = 4;

export const UKRAINIAN_LOWERCASE = 'абвгґдеєжзиіїйклмнопрстуфхцчшщьюя';

/**
 * Стеми, що вносять медичну або неврологічну семантику. Перелік із промпту
 * Фази 2, розширений сусідніми полями тієї самої семантики: фраза для
 * щоденника симптомів не повинна складатися зі слів про хворобу.
 * Латинські відповідники окремо не потрібні — алфавітний фільтр їх і так
 * відкидає, — але лишаються тут як явна документація наміру.
 */
export const STOP_STEMS: readonly string[] = [
  // Перелік промпту Фази 2.
  'невро',
  'симптом',
  'мігрен',
  'епілеп',
  'склероз',
  'здоров',
  'діагн',
  'біль',
  'напад',
  'лік',
  // Сусідні поля тієї самої семантики: фраза для щоденника симптомів не має
  // складатися зі слів про хворобу, тіло й смерть. Кандидатів вистачає з
  // запасом, тож ціна розширення нульова.
  'нерв',
  'боліт',
  'медиц',
  'хворо',
  'недуг',
  'травм',
  'судом',
  'припад',
  'приступ',
  'терап',
  'пухлин',
  'інсульт',
  'інфаркт',
  'кров',
  'смерт',
  'труп',
  'мертв',
  'помер',
  'вмер',
  'умер',
  'могил',
  'похорон',
  'суїцид',
  'самогуб',
  'аборт',
  'вагітн',
  'полог',
  'операц',
  'пацієнт',
  'аптек',
  'клінік',
  'таблет',
  'шприц',
  'отрут',
  'наркот',
  'рана',
  'опік',
  'цикл',
  'менстр',
  // Латинські відповідники алфавітний фільтр і так відкидає; вони лишаються
  // тут як явна документація наміру.
  'neuro',
  'symptom',
  'migrain',
  'epilep',
  'sclero',
  'diagn'
];

/** Пари приголосних, які в кінці слова та перед глухими звучать однаково. */
const DEVOICING: ReadonlyArray<readonly [string, string]> = [
  ['дж', 'ч'],
  ['дз', 'ц'],
  ['б', 'п'],
  ['д', 'т'],
  ['ж', 'ш'],
  ['з', 'с'],
  ['г', 'х'],
  ['ґ', 'к']
];

export function hasAllowedLength(word: string): boolean {
  return word.length >= MIN_LENGTH && word.length <= MAX_LENGTH;
}

export function isUkrainianLowercase(word: string): boolean {
  for (const letter of word) {
    if (!UKRAINIAN_LOWERCASE.includes(letter)) return false;
  }
  return word.length > 0;
}

export function hasNoMedicalStem(word: string): boolean {
  return !STOP_STEMS.some((stem) => word.includes(stem));
}

/**
 * Форма слова після оглушення дзвінких у кінці. Два слова з однаковою формою
 * не розрізняються на слух, тобто фразу неможливо надійно продиктувати —
 * обидва вилучаються зі списку.
 */
export function devoicedForm(word: string): string {
  for (const [voiced, voiceless] of DEVOICING) {
    if (word.endsWith(voiced)) {
      return word.slice(0, word.length - voiced.length) + voiceless;
    }
  }
  return word;
}

export function hasUniquePrefixes(words: readonly string[]): boolean {
  return new Set(words.map((word) => word.slice(0, PREFIX_LENGTH))).size === words.length;
}

export function hasNoHomophones(words: readonly string[]): boolean {
  return new Set(words.map(devoicedForm)).size === words.length;
}
