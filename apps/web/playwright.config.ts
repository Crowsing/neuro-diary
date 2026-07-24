// Playwright-конфіг e2e для acceptance-критеріїв прототипу (AC1–AC8).
// Сервер: production build + vite preview на 4173 — детермінованіше за dev
// (жодних on-demand трансформів). Дата фіксується URL-параметром ?now=2026-01-15.

import { defineConfig, devices } from '@playwright/test';

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
      use: { ...devices['Pixel 5'], viewport: { width: 390, height: 844 } }
    },
    {
      name: 'desktop',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } }
    }
  ],
  webServer: {
    command: 'pnpm build && pnpm exec vite preview --port 4173 --strictPort',
    url: 'http://localhost:4173',
    // false: зайнятий порт = гучна помилка, а не мовчазний прогін проти
    // застарілого білда чужого сервера.
    reuseExistingServer: false,
    timeout: 180_000
  }
});
