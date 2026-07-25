import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

import { buildDirectives, metaSafe, toPolicy } from "./src/security/csp";

// Конфіг vitest живе окремо у vitest.config.ts: vitest 2.x резолвить власний
// vite@5, і поле `test` тут ламало tsc проти vite@6 цього застосунку.

const HERE = dirname(fileURLToPath(import.meta.url));
const CONSENT_COPY_ROOT = resolve(HERE, "../../consent-copy");
const VIRTUAL_CONSENT_COPY = "virtual:consent-copy";

/**
 * Реєстр текстів згод — той самий каталог у корені, який читає apps/api.
 *
 * Хеш НЕ рахується в браузері: `?raw` дає рядок, і байтова еквівалентність
 * через TextEncoder була б припущенням. Береться значення з реєстру, а рівність
 * «реєстр = SHA-256 байтів файлу» доводиться окремим тестом.
 */
function consentCopyPlugin(): Plugin {
  return {
    name: "nd-consent-copy",
    resolveId(id) {
      return id === VIRTUAL_CONSENT_COPY ? `\0${VIRTUAL_CONSENT_COPY}` : null;
    },
    load(id) {
      if (id !== `\0${VIRTUAL_CONSENT_COPY}`) return null;
      const registry = JSON.parse(
        readFileSync(resolve(CONSENT_COPY_ROOT, "registry.json"), "utf-8"),
      ) as {
        grants: {
          kind: string;
          text_version: string;
          locale: string;
          file: string;
          sha256: string;
          frozen: boolean;
        }[];
      };
      const texts = registry.grants.map((entry) => ({
        kind: entry.kind,
        textVersion: entry.text_version,
        locale: entry.locale,
        sha256: entry.sha256,
        frozen: entry.frozen,
        body: readFileSync(resolve(CONSENT_COPY_ROOT, entry.file), "utf-8"),
      }));
      return `export const CONSENT_TEXTS = ${JSON.stringify(texts)};`;
    },
  };
}

/** Єдина мета-CSP, зібрана з того самого модуля, що й тести (§13.17). */
function cspPlugin(apiOrigin: string): Plugin {
  return {
    name: "nd-csp",
    transformIndexHtml(html) {
      const policy = toPolicy(metaSafe(buildDirectives(apiOrigin)));
      return html.replace(
        "</head>",
        `  <meta http-equiv="Content-Security-Policy" content="${policy}" />\n  </head>`,
      );
    },
  };
}

export default defineConfig(({ mode }) => {
  // Порожній рядок означає local-only збірку: sync вимкнений, і connect-src
  // не отримує жодного зовнішнього походження.
  const apiOrigin = process.env.VITE_API_ORIGIN ?? "";
  return {
    plugins: [react(), consentCopyPlugin(), cspPlugin(apiOrigin)],
    define: {
      // Статичний прапорець: гілка з `await import('./engine')` усувається
      // збіркою цілком, тож local-only бандл фізично не містить мережевого коду.
      "import.meta.env.VITE_SYNC": JSON.stringify(process.env.VITE_SYNC ?? "off"),
      "import.meta.env.VITE_API_ORIGIN": JSON.stringify(apiOrigin),
    },
    server: {
      // Vite 6 відхиляє запити з незнайомим заголовком Host. Telegram відкриває
      // Mini App лише через HTTPS, тобто в розробці — через тунель, тож його
      // домени треба дозволити явно. Стосується тільки dev-сервера.
      allowedHosts: [
        ".ngrok-free.dev",
        ".ngrok-free.app",
        ".ngrok.app",
        ".trycloudflare.com",
      ],
    },
    build: {
      outDir: process.env.VITE_OUT_DIR ?? "dist",
      sourcemap: mode === "development",
    },
  };
});
