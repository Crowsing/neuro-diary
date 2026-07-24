import { defineConfig } from "vitest/config";

// Окремо від vite.config.ts: vitest 2.x тягне власний vite@5, і спільний конфіг
// із плагінами vite@6 не проходить перевірку типів. Тести — чисті модулі
// (environment: node), плагін react їм не потрібен.
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
