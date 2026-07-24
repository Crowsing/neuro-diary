import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Конфіг vitest живе окремо у vitest.config.ts: vitest 2.x резолвить власний
// vite@5, і поле `test` тут ламало tsc проти vite@6 цього застосунку.
export default defineConfig({
  plugins: [react()],
});
