// Чому не вдалося прочитати чи відкликати згоду — одним рядком-кодом.
//
// Модуль існує тому, що екран згод отримує помилки з двох різних джерел і
// перша редакція це проґавила: `VaultSession.ensureSession` кидає сирий
// `SyncError` (у нього є `code` і `serverCode`, але немає `failure`), а
// `VaultError` — навпаки. Читання лише `failure` перетворювало «акаунта немає»
// на «не вдалося», тобто новий користувач бачив помилку замість чесного
// «чинних згод немає». Шлях без акаунта не покривався жодним e2e, і тут він
// покривається юнітом — без витрати спроби автентифікації з десяти, які §11
// дає на хвилину.
//
// `environment: node`, жодного React: функція чиста над формою помилки.

/** Стабільний ASCII-код або причина сейфа; `server` — коли нічого не впізнали. */
export function consentFailureOf(error: unknown): string {
  if (error === null || typeof error !== 'object') return 'server';
  const value = error as {
    serverCode?: unknown;
    code?: unknown;
    failure?: unknown;
  };
  // Порядок важливий: серверний код найточніший (`confirm_required` і
  // `consent_precondition` існують лише в ньому), `code` — грубий клас зі
  // статусу, `failure` — переклад сейфа.
  if (typeof value.serverCode === 'string' && value.serverCode !== '') {
    return value.serverCode;
  }
  if (typeof value.code === 'string' && value.code !== '') return value.code;
  if (typeof value.failure === 'string' && value.failure !== '') {
    return value.failure;
  }
  return 'server';
}

/**
 * Чи означає ця відмова «акаунта немає», а не збій.
 *
 * `403 no_account` на переліку згод — штатний стан: користувачка ще нічого не
 * вмикала. Показати їй слово «помилка» означало б назвати збоєм її власний
 * вибір нічого не вмикати.
 */
export function meansNoAccount(failure: string): boolean {
  return failure === 'no_account';
}
