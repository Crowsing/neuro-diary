// Хелпери sync-e2e: підписаний initData і два «профілі браузера».
//
// Ключ навмисно той самий детермінований, що вже живе в
// `apps/api/tests/integration/conftest.py`: насіння `bytes(range(32))`. Він не
// секрет і не комітиться як ключоподібний блоб — це двадцять байтів
// послідовності, які збираються тут з `Uint8Array.from({length: 32}, …)`, тож
// gitleaks не має що знайти, а api валідує підпис справжньою перевіркою, а не
// заглушкою.
//
// Публічний ключ передається серверу змінною `TELEGRAM_ED25519_PUBLIC_KEY`;
// його значення продубльоване в CI-джобі `sync-e2e`.

import { createPrivateKey, sign } from 'node:crypto';
import type { BrowserContext, Page } from '@playwright/test';

/** Той самий фікстурний bot id, що і в `scripts/dev-stand.sh`. */
export const BOT_ID = 1234567890;

/**
 * Свій акаунт на кожен прогін.
 *
 * Фіксований id зробив би тест неідемпотентним: другий прогін проти того самого
 * стенду знаходив би вже налаштований сейф і йшов би зовсім іншою гілкою —
 * «введіть наявну фразу» замість «згенеруйте нову». Обидва пристрої в межах
 * одного прогону беруть це значення з модуля, тож акаунт у них спільний.
 */
export const TELEGRAM_USER_ID = Number(
  process.env.SYNC_E2E_USER_ID ?? 4_200_000_000 + (Date.now() % 90_000_000)
);

/** Публічний ключ насіння нижче — рівно те, що очікує api. */
export const TEST_PUBLIC_KEY_HEX =
  '03a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8';

const SEED = Uint8Array.from({ length: 32 }, (_, index) => index);

/**
 * PKCS#8-обгортка 32-байтового насіння: Node не приймає сире насіння Ed25519,
 * а лише DER. Префікс — фіксований заголовок алгоритму з RFC 8410.
 */
function privateKey() {
  const prefix = Buffer.from('302e020100300506032b657004220420', 'hex');
  return createPrivateKey({
    key: Buffer.concat([prefix, Buffer.from(SEED)]),
    format: 'der',
    type: 'pkcs8'
  });
}

export interface InitDataOptions {
  readonly telegramUserId?: number;
  readonly authDate?: Date;
  /** Робить initData унікальним: anti-replay §8 віддає одну сесію на один. */
  readonly queryId?: string;
}

/**
 * initData, підписаний так само, як його підписав би Telegram.
 *
 * Check-string — `"<bot_id>:WebAppData\n"` плюс поля без `hash` і `signature`,
 * алфавітно, по `key=value` через `\n` (див. `app/infra/telegram/initdata.py`).
 */
export function signedInitData(options: InitDataOptions = {}): string {
  const moment = options.authDate ?? new Date();
  const fields: Record<string, string> = {
    auth_date: String(Math.floor(moment.getTime() / 1000)),
    user: JSON.stringify({ id: options.telegramUserId ?? TELEGRAM_USER_ID })
  };
  if (options.queryId !== undefined) fields.query_id = options.queryId;

  const body = Object.entries(fields)
    .sort((a, b) => (a[0] < b[0] ? -1 : 1))
    .map(([key, value]) => `${key}=${value}`)
    .join('\n');
  const signature = sign(null, Buffer.from(`${BOT_ID}:WebAppData\n${body}`, 'utf-8'), privateKey());
  fields.signature = signature.toString('base64url').replace(/=+$/, '');

  return Object.entries(fields)
    .map(([key, value]) => `${key}=${encodeURIComponent(value)}`)
    .join('&');
}

/** Адреса застосунку з initData у фрагменті — так його відкриває Telegram. */
export function syncAppUrl(initData: string, nowIso = '2026-01-15'): string {
  return `/?now=${nowIso}#tgWebAppData=${encodeURIComponent(initData)}`;
}

/**
 * Один профіль браузера з власним initData.
 *
 * Різні `query_id` обов'язкові: сервер віддає сесію щонайбільше один раз на
 * конкретний initData, тож два профілі з однаковим рядком дали б 401 на
 * другому — і тест «два пристрої» доводив би не те, що потрібно.
 */
export async function openDevice(
  context: BrowserContext,
  options: { queryId: string; seed?: Record<string, unknown> }
): Promise<{ page: Page; initData: string }> {
  const page = await context.newPage();
  const initData = signedInitData({ queryId: options.queryId });
  if (options.seed !== undefined) await seedEmptyDiary(page, options.seed);
  await page.goto(syncAppUrl(initData));
  return { page, initData };
}

/** Кладе стан у localStorage ДО старту застосунку — як `helpers.ts::seedState`. */
export async function seedEmptyDiary(
  page: Page,
  state: Record<string, unknown>
): Promise<void> {
  const payload = JSON.stringify(state);
  await page.addInitScript(
    ([key, json]) => {
      if (!localStorage.getItem('nd_e2e_seeded')) {
        localStorage.setItem(key, json);
        localStorage.setItem('nd_e2e_seeded', '1');
      }
    },
    ['nd_demo_v3', payload] as const
  );
}

/** Порожній щоденник у режимі app: онбординг пропущено, демо-даних немає. */
export function emptyAppState(): Record<string, unknown> {
  return {
    view: 'app',
    tab: 'set',
    data: {
      schemaVersion: 4,
      entries: {},
      cycleStarts: [],
      active: [],
      archived: [],
      custom: [],
      groups: [],
      symptomGroupIds: {},
      cycleOn: false,
      lock: false
    }
  };
}
