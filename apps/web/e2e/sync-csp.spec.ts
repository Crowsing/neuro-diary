// CSP на зібраному застосунку — §13.17.
//
// Дві протилежні небезпеки: надто слабка політика нічого не захищає, надто
// строга тихо зіштовхує всіх на PBKDF2, бо WebAssembly не запускається без
// `'wasm-unsafe-eval'`. Обидві перевіряються тут на артефакті, а не на намірі.

import { expect, test } from '@playwright/test';
import { emptyAppState, seedEmptyDiary, signedInitData, syncAppUrl } from './sync-helpers';

test('повний обхід застосунку не дає жодного порушення політики', async ({ page }) => {
  const violations: string[] = [];
  await page.addInitScript(() => {
    (window as unknown as { __csp: string[] }).__csp = [];
    document.addEventListener('securitypolicyviolation', (event) => {
      (window as unknown as { __csp: string[] }).__csp.push(
        `${event.violatedDirective}:${event.blockedURI}`
      );
    });
  });

  await seedEmptyDiary(page, emptyAppState());
  await page.goto(syncAppUrl(signedInitData({ queryId: 'csp-1' })));
  await page.getByRole('heading', { name: 'Налаштування' }).waitFor();

  for (const label of ['Сьогодні', 'Історія', 'Динаміка', 'Звіт', 'Налаштування']) {
    const tab = page.getByRole('button', { name: label });
    if (await tab.count()) await tab.first().click();
  }

  violations.push(...(await page.evaluate(() => (window as unknown as { __csp: string[] }).__csp)));
  expect(violations).toEqual([]);
});

test('WebAssembly.instantiate працює під чинною політикою', async ({ page }) => {
  await seedEmptyDiary(page, emptyAppState());
  await page.goto(syncAppUrl(signedInitData({ queryId: 'csp-2' })));
  await page.getByRole('heading', { name: 'Налаштування' }).waitFor();

  // Найменший валідний модуль WASM: заголовок і версія. Якщо політика забороняє
  // WASM, це впаде саме тут — і саме це означало б тихий відкат на PBKDF2 без
  // жодного червоного тесту.
  const ok = await page.evaluate(async () => {
    const bytes = new Uint8Array([0x00, 0x61, 0x73, 0x6d, 0x01, 0x00, 0x00, 0x00]);
    try {
      await WebAssembly.instantiate(bytes);
      return true;
    } catch {
      return false;
    }
  });
  expect(ok).toBe(true);
});

test('політика не дозволяє ані eval, ані inline-скрипт', async ({ page }) => {
  await seedEmptyDiary(page, emptyAppState());
  await page.goto(syncAppUrl(signedInitData({ queryId: 'csp-3' })));
  const policy = await page.evaluate(
    () =>
      document
        .querySelector('meta[http-equiv="Content-Security-Policy"]')
        ?.getAttribute('content') ?? ''
  );
  const scriptSrc =
    policy
      .split(';')
      .map((item) => item.trim())
      .find((item) => item.startsWith('script-src '))
      ?.split(/\s+/)
      .slice(1) ?? [];

  expect(scriptSrc.length).toBeGreaterThan(0);
  expect(scriptSrc).not.toContain("'unsafe-eval'");
  expect(scriptSrc).not.toContain("'unsafe-inline'");
  expect(scriptSrc).toContain("'wasm-unsafe-eval'");
});
