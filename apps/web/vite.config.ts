import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Конфіг vitest живе окремо у vitest.config.ts: vitest 2.x резолвить власний
// vite@5, і поле `test` тут ламало tsc проти vite@6 цього застосунку.
export default defineConfig({
  plugins: [react()],
  server: {
    // Vite 6 відхиляє запити з незнайомим заголовком Host. Telegram відкриває
    // Mini App лише через HTTPS, тобто в розробці — через тунель, тож його
    // домени треба дозволити явно. Стосується тільки dev-сервера.
    allowedHosts: [".ngrok-free.dev", ".ngrok-free.app", ".ngrok.app", ".trycloudflare.com"],
  },
});
