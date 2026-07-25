// Захисти клієнта — §9.4 плану.
//
// Два правила, обидва про те саме: серверна відповідь ніколи не має тихо
// стирати локальні дані. Локальна копія первинна; сервер — це резервна копія,
// а не джерело істини.

/** Частка й кількість, за яких видалення трактується як подія, а не як синк. */
export const MASS_DELETE_SHARE = 0.2;
export const MASS_DELETE_MINIMUM = 10;

export interface LocalRecordState {
  readonly path: string;
  /** Сервер колись підтвердив цей запис. */
  readonly acked: boolean;
  /** Є локальні зміни, ще не надіслані. */
  readonly dirty: boolean;
}

export interface ConsentSnapshot {
  readonly activeKinds: readonly string[];
  /** Ревізія pull, під час якої знімок отримано (§9.4). */
  readonly fetchedAtRevision: number;
}

export interface PresenceInput {
  readonly local: readonly LocalRecordState[];
  /** Повний серверний набір шляхів після 410-ресинку. */
  readonly serverPaths: ReadonlySet<string>;
  readonly consents: ConsentSnapshot | null;
  readonly pullRevision: number;
}

export interface PresenceVerdict {
  /** Можна видалити локально — після підтвердження на пристрої. */
  readonly prune: readonly string[];
  /** Лишається локально й піде в push. */
  readonly keep: readonly string[];
  /** Потребує явного рішення користувачки. */
  readonly needsConfirmation: readonly string[];
}

/**
 * Домени, чий запис сервер має право вилучити за названим ключем (§9.7).
 *
 * Єдине джерело для трьох предикатів, які інакше розповзаються: чи входить шлях
 * у manifest (§7), чи виключений він із правила авторитетності присутності
 * (§9.4) і чи дозволено йому покидати пристрій без своєї згоди. Ключі цієї мапи
 * мусять збігатися з `MANIFEST_EXCLUDED_PATHS` — це під тестом, бо саме
 * розходження між ними повертає конфлікт §7 ↔ §9.7: кожен повний ресинк після
 * штатного відкликання згоди знову ставав би помилкою цілісності.
 */
export const NAMED_KEY_CONSENTS: Readonly<Record<string, string>> = {
  cycle: 'cycle_sync'
};

/** Назва згоди, без якої цей шлях не існує на сервері; `null` — якщо такої немає. */
export function consentFor(path: string): string | null {
  return NAMED_KEY_CONSENTS[path] ?? null;
}

/**
 * Правило авторитетності присутності після 410 (§9.4).
 *
 * Виняток для неактивних згод не є деталлю: без нього відкликання `cycle_sync`
 * призводило б до найтяжчого можливого дефекту — сервер видаляє записи циклу,
 * пристрій із активною `health_sync` рано чи пізно отримує 410, не знаходить їх
 * у серверному наборі й стирає локальні `cycleStarts`. Відкликання згоди на
 * синхронізацію ніколи не має видаляти локальні дані.
 */
export function presenceAuthority(input: PresenceInput): PresenceVerdict {
  const prune: string[] = [];
  const keep: string[] = [];
  const needsConfirmation: string[] = [];

  // Без свіжої відповіді `GET /v1/consents`, не старшої за цей самий pull,
  // pruning не виконується взагалі.
  const consentsFresh =
    input.consents !== null &&
    input.consents.fetchedAtRevision >= input.pullRevision;

  for (const record of input.local) {
    if (input.serverPaths.has(record.path)) {
      keep.push(record.path);
      continue;
    }
    if (!consentsFresh) {
      keep.push(record.path);
      continue;
    }
    const required = consentFor(record.path);
    if (required !== null && !input.consents!.activeKinds.includes(required)) {
      keep.push(record.path);
      continue;
    }
    if (!record.acked) {
      keep.push(record.path);
      continue;
    }
    if (record.dirty) {
      needsConfirmation.push(record.path);
      continue;
    }
    prune.push(record.path);
  }

  return { prune, keep, needsConfirmation };
}

/**
 * Захист від масового видалення вкраденою сесією (§9.4).
 *
 * Обидві умови обов'язкові: частка без абсолютного мінімуму спрацьовувала б на
 * щоденнику з трьох записів, а мінімум без частки — на кожному великому
 * прибиранні.
 */
export function needsMassDeleteConfirmation(
  localCount: number,
  tombstoned: number
): boolean {
  return (
    tombstoned >= MASS_DELETE_MINIMUM && tombstoned > localCount * MASS_DELETE_SHARE
  );
}
