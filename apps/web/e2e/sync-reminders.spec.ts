// Шлях нагадувань проти живого api з реальною PostgreSQL.
//
// Це єдина збірка, у якій прапорець `VITE_REMINDERS` увімкнений
// (`playwright.config.ts`), і єдине місце, де екран перевіряється взагалі —
// у продакшеновій формі його немає фізично. «Вимкнено» не означає
// «неперевірено», і саме цей файл робить різницю.
//
// **Один тест на весь шлях, а не чотири.** Це не економія рядків: §11 дає
// десять спроб автентифікації на хвилину з одного IP, а кожне ввімкнення
// нагадувань коштує дві (гілка 2 §8: спершу без grant, потім із ним). Чотири
// незалежні тести вичерпували вікно, і сюїта червоніла 429 — причому у
// браузері це виглядало як «Немає зв'язку», бо відмова лімітера йде повз
// CORSMiddleware і не має заголовка `Access-Control-Allow-Origin`. Ліміт не
// послаблюється: жодна людина не автентифікується десять разів на хвилину, тож
// це тести були нереалістичні, а не ліміт завузький.
//
// Дві речі перевіряються НЕ так, як напрошується, і обидві коштували часу
// раніше в цьому репозиторії:
//
//  * геометрія — координатами, а не `toBeVisible()`. Playwright уважає видимим
//    елемент із непорожнім боксом, навіть повністю обрізаний предком; саме так
//    увесь sync-оверлей роками рендерився нижче viewport при зеленій сюїті
//    (див. `shell-geometry.spec.ts`);
//  * мережа — повним збігом префікса призначеного api й пінованим SDK, а не
//    дозволом класу. `page.route` не ховає запит від `page.on('request')`.

import { expect, test, type Locator, type Page } from '@playwright/test';
import { stubTelegramSdk, TELEGRAM_SDK_URL } from './helpers';
import {
  emptyAppState,
  freshTelegramUserId,
  seedEmptyDiary,
  signedInitData,
  syncAppUrl
} from './sync-helpers';

test.describe.configure({ mode: 'serial' });

const API_ORIGIN = process.env.SYNC_API_ORIGIN ?? 'http://localhost:8000';

/** Той самий контроль, що в `shell-geometry.spec.ts`: бокс усередині кадру. */
async function expectInsideViewport(
  page: Page,
  locator: Locator,
  what: string
): Promise<void> {
  const box = await locator.boundingBox();
  expect(box, `${what}: елемент не має боксу — його немає в макеті`).not.toBeNull();
  const viewport = page.viewportSize();
  expect(viewport, 'viewport не заданий').not.toBeNull();
  expect(box!.y, `${what}: верхній край вище кадру`).toBeGreaterThanOrEqual(0);
  expect(box!.y + box!.height, `${what}: нижній край нижче кадру`).toBeLessThanOrEqual(
    viewport!.height
  );
  expect(box!.x, `${what}: лівий край поза кадром`).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width, `${what}: правий край поза кадром`).toBeLessThanOrEqual(
    viewport!.width
  );
}

test('нагадування: згода, канонічний ресурс, quiet hours, зона і пауза', async ({
  context
}) => {
  // Пристрій відкривається вручну, а не через `openDevice`, рівно з однієї
  // причини: слухач запитів мусить стояти ДО навігації. Перша редакція вішала
  // його після, і твердження «SDK завантажився рівно один раз» було порожнім —
  // запит уже минув. Порожня перевірка гірша за відсутню, бо виглядає наявною.
  const page = await context.newPage();
  await stubTelegramSdk(page);
  await seedEmptyDiary(page, emptyAppState());

  const foreign: string[] = [];
  const sdk: string[] = [];
  page.on('request', (request) => {
    const url = request.url();
    if (url.startsWith('http://localhost:4174/')) return;
    if (url.startsWith(`${API_ORIGIN}/v1/`)) return;
    // Банер стенду читає `app_env` із живого api, і це єдиний запит поза `/v1/`.
    // Дозволяється повним збігом, а не префіксом: `${origin}/health…` уже був би
    // чимось іншим.
    if (url === `${API_ORIGIN}/health`) return;
    if (url === TELEGRAM_SDK_URL) sdk.push(url);
    else foreign.push(url);
  });

  await page.goto(
    syncAppUrl(
      signedInitData({
        queryId: `reminders-${Date.now()}`,
        telegramUserId: freshTelegramUserId()
      })
    )
  );

  // --- увімкнення -----------------------------------------------------------
  await page.getByTestId('reminders-card').click();
  await expect(page.getByTestId('reminders-enable')).toBeVisible({ timeout: 30_000 });
  await page.locator('#reminders-time').fill('20:00');
  await page.locator('#reminders-timezone').fill('Europe/Kyiv');
  await page.getByTestId('reminders-enable').click();

  // Текст згоди показується ДО будь-якого мережевого виклику (§8).
  await expect(page.getByTestId('consent-text')).toContainText('Нагадування в Telegram');
  await page.getByRole('button', { name: 'Погоджуюсь' }).click();
  await expect(page.getByTestId('reminders-save')).toBeVisible({ timeout: 60_000 });

  // Сервер повернув канонічний ресурс, і саме він на екрані.
  await expect(page.locator('#reminders-time')).toHaveValue('20:00');
  await expect(page.locator('#reminders-timezone')).toHaveValue('Europe/Kyiv');

  // --- геометрія ------------------------------------------------------------
  // Оверлей нагадувань новий, і перевіряти його «видимість» тим способом, який
  // колись пропустив цілий екран, немає сенсу.
  await expectInsideViewport(page, page.getByTestId('reminders-save'), 'кнопка «Зберегти»');
  await expectInsideViewport(page, page.locator('#reminders-time'), 'поле часу');
  await expectInsideViewport(page, page.getByTestId('reminders-pause'), 'кнопка «Вимкнути»');

  // --- повторне відкриття читає з сервера, а не з памʼяті вкладки ------------
  await page.getByRole('button', { name: 'Закрити' }).click();
  await page.getByTestId('reminders-card').click();
  await expect(page.locator('#reminders-time')).toHaveValue('20:00', { timeout: 30_000 });

  // --- quiet hours вирішує сервер -------------------------------------------
  await page.locator('#reminders-time').fill('23:00');
  await page.getByTestId('reminders-save').click();
  await expect(page.getByTestId('reminders-error')).toContainText('нічних годинах');

  // Відмова нічого не записала.
  await page.getByRole('button', { name: 'Закрити' }).click();
  await page.getByTestId('reminders-card').click();
  await expect(page.locator('#reminders-time')).toHaveValue('20:00', { timeout: 30_000 });

  // --- зона: авторитет у запіненої tzdata на сервері -------------------------
  await page.locator('#reminders-timezone').fill('Mars/Olympus');
  await page.getByTestId('reminders-save').click();
  await expect(page.getByTestId('reminders-error')).toContainText('часового поясу');

  // --- пауза — не відкликання -----------------------------------------------
  await page.locator('#reminders-timezone').fill('Europe/Kyiv');
  await page.getByTestId('reminders-pause').click();
  await expect(page.getByTestId('reminders-paused')).toBeVisible({ timeout: 30_000 });

  // Згода лишається чинною, час і зона збережені — це головне твердження паузи.
  await page.getByTestId('reminders-consents').click();
  await expect(page.getByTestId('consent-row-telegram_reminders')).toBeVisible({
    timeout: 30_000
  });
  await page.getByRole('button', { name: 'Закрити' }).click();
  await page.getByTestId('reminders-card').click();
  await expect(page.locator('#reminders-time')).toHaveValue('20:00', { timeout: 30_000 });
  await expect(page.getByRole('button', { name: 'Увімкнути знову' })).toBeVisible();

  expect(foreign).toEqual([]);
  expect(sdk).toEqual([TELEGRAM_SDK_URL]);
});
