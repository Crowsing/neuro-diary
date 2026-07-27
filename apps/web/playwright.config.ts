// Playwright-конфіг e2e для acceptance-критеріїв прототипу (AC1–AC8).
// Сервер: production build + vite preview на 4173 — детермінованіше за dev
// (жодних on-demand трансформів). Дата фіксується URL-параметром ?now=2026-01-15.
//
// Проєкт `sync` живе окремо і вмикається лише змінною SYNC_E2E=1. Причина не
// стилістична: він вимагає живого локального api з PostgreSQL, тож якби він
// потрапляв у звичайний `pnpm e2e`, наскрізний gate падав би на кожній машині
// без стенду. CI запускає його окремим блокуючим job-ом `sync-e2e`.

import { defineConfig, devices } from '@playwright/test';

const SYNC = process.env.SYNC_E2E === '1';
const API_ORIGIN = process.env.SYNC_API_ORIGIN ?? 'http://localhost:8000';
const SYNC_PORT = 4174;

/** AC8 стверджує нуль зовнішніх запитів — sync-сценарії до нього не входять. */
const LOCAL_ONLY = { testIgnore: /sync-/ } as const;

export default defineConfig({
  testDir: 'e2e',
  fullyParallel: true,
  retries: 0,
  reporter: [['list']],
  timeout: 60_000,
  use: {
    baseURL: 'http://localhost:4173',
    locale: 'uk-UA',
    timezoneId: 'Europe/Kyiv',
    trace: 'retain-on-failure'
  },
  projects: [
    {
      name: 'mobile',
      ...LOCAL_ONLY,
      use: { ...devices['Pixel 5'], viewport: { width: 390, height: 844 } }
    },
    {
      name: 'desktop',
      ...LOCAL_ONLY,
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } }
    },
    ...(SYNC
      ? [
          {
            name: 'sync',
            // Саме `.spec.ts`: ширший шаблон робив би `sync-helpers.ts`
            // «тестовим файлом», і Playwright забороняв би його імпорт.
            testMatch: /sync-.*\.spec\.ts$/,
            // Кожне відкриття сейфа — мільйон ітерацій PBKDF2 у браузері, а
            // кожен синк — AES-GCM на весь щоденник. Це не повільний тест, це
            // ціна криптографії, яку §7 і вимагає.
            timeout: 240_000,
            use: {
              ...devices['Desktop Chrome'],
              viewport: { width: 1440, height: 900 },
              baseURL: `http://localhost:${SYNC_PORT}`
            }
          }
        ]
      : [])
  ],
  webServer: [
    {
      command: 'pnpm build && pnpm exec vite preview --port 4173 --strictPort',
      url: 'http://localhost:4173',
      // false: зайнятий порт = гучна помилка, а не мовчазний прогін проти
      // застарілого білда чужого сервера.
      reuseExistingServer: false,
      timeout: 180_000
    },
    ...(SYNC
      ? [
          {
            // `VITE_REMINDERS=on` саме тут, і ніде більше в репозиторії.
            //
            // Шлях нагадувань лишається за прапорцем, доки gate
            // `docs/plans/future-telegram-reminders.md` відкритий, — але
            // «вимкнено» не означає «неперевірено». Це єдина збірка, у якій
            // він увімкнений, і вона ж єдина, що ходить до живого api з
            // реальною PostgreSQL. `assert-bundle.mjs --reminders` одразу
            // після збірки червоніє, якщо прапорець не дійшов до артефакту.
            command: `VITE_SYNC=on VITE_REMINDERS=on VITE_API_ORIGIN=${API_ORIGIN} VITE_OUT_DIR=dist-sync pnpm build && node scripts/assert-bundle.mjs dist-sync --sync --reminders --api=${API_ORIGIN} && pnpm exec vite preview --outDir dist-sync --port ${SYNC_PORT} --strictPort`,
            url: `http://localhost:${SYNC_PORT}`,
            reuseExistingServer: false,
            timeout: 180_000
          }
        ]
      : [])
  ]
});
