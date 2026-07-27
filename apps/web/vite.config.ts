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
const CONTRACT_FIXTURES = resolve(HERE, "../../fixtures/contract");
const VIRTUAL_QUIET_HOURS = "virtual:quiet-hours";

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
      interface RegistryEntry {
        kind: string;
        text_version: string;
        locale: string;
        file: string;
        sha256: string;
        frozen: boolean;
      }
      const registry = JSON.parse(
        readFileSync(resolve(CONSENT_COPY_ROOT, "registry.json"), "utf-8"),
      ) as { grants: RegistryEntry[]; revocations: RegistryEntry[] };
      const project = (entries: RegistryEntry[]) =>
        entries.map((entry) => ({
          kind: entry.kind,
          textVersion: entry.text_version,
          locale: entry.locale,
          sha256: entry.sha256,
          frozen: entry.frozen,
          body: readFileSync(resolve(CONSENT_COPY_ROOT, entry.file), "utf-8"),
        }));
      // Тексти відкликання лежали в реєстрі з Фази 2 і не доходили до застосунку
      // взагалі: плагін мапив лише `grants`. UI відкликання без них показував би
      // переказ своїми словами — другий текст, якого ніхто не звіряє з хешем.
      return [
        `export const CONSENT_TEXTS = ${JSON.stringify(project(registry.grants))};`,
        `export const CONSENT_REVOKE_TEXTS = ${JSON.stringify(
          project(registry.revocations),
        )};`,
      ].join("\n");
    },
  };
}

/**
 * Політика quiet hours §10 — той самий файл, який читає apps/api.
 *
 * Число не переїжджає в код web навіть як константа: §10 вимагає, щоб політика
 * експортувалася з API, а не переоголошувалась. Проєкція навмисно вузька —
 * лише `start` і `end`; `boundaries` у фікстурі існують для тестів обох сторін,
 * і бандлу вони не потрібні.
 */
function quietHoursPlugin(): Plugin {
  return {
    name: "nd-quiet-hours",
    resolveId(id) {
      return id === VIRTUAL_QUIET_HOURS ? `\0${VIRTUAL_QUIET_HOURS}` : null;
    },
    load(id) {
      if (id !== `\0${VIRTUAL_QUIET_HOURS}`) return null;
      const policy = JSON.parse(
        readFileSync(resolve(CONTRACT_FIXTURES, "quiet-hours.json"), "utf-8"),
      ) as { start: string; end: string };
      return `export const QUIET_HOURS = ${JSON.stringify({
        start: policy.start,
        end: policy.end,
      })};`;
    },
  };
}

/**
 * Єдина мета-CSP, зібрана з того самого модуля, що й тести (§13.17).
 *
 * Вставляється одразу ПІСЛЯ оголошення кодування — тобто перед усім, що
 * завантажує ресурси. Причина не косметична: `<meta http-equiv=
 * "Content-Security-Policy">` керує лише тим, що йде після неї. Попередня
 * редакція підставляла політику перед `</head>`, тобто останньою — після
 * скрипта Telegram SDK, вхідного чанку Vite і таблиці стилів. Жоден із них їй
 * не підпорядковувався, і політика описувала порожнечу.
 *
 * Саме після charset, а не на самому початку `<head>`: оголошення кодування
 * має лишатися в перших 1024 байтах документа.
 */
const CHARSET_META = '<meta charset="utf-8" />';

function cspPlugin(apiOrigin: string): Plugin {
  return {
    name: "nd-csp",
    transformIndexHtml(html) {
      const policy = toPolicy(metaSafe(buildDirectives(apiOrigin)));
      if (!html.includes(CHARSET_META)) {
        // Мовчазна вставка «кудись» гірша за гучну зупинку: політика, яка
        // стоїть не там, не захищає нічого, але виглядає наявною.
        throw new Error(`nd-csp: у index.html немає ${CHARSET_META}, нема куди вставити політику`);
      }
      return html.replace(
        CHARSET_META,
        `${CHARSET_META}\n    <meta http-equiv="Content-Security-Policy" content="${policy}" />`,
      );
    },
  };
}

export default defineConfig(({ mode }) => {
  // Порожній рядок означає local-only збірку: sync вимкнений, і connect-src
  // не отримує жодного зовнішнього походження.
  const apiOrigin = process.env.VITE_API_ORIGIN ?? "";
  // Куди dev-сервер проксує api. Порожньо — проксі немає (збірка, CI, local-only).
  const apiProxy = process.env.VITE_API_PROXY ?? "";
  return {
    plugins: [react(), consentCopyPlugin(), quietHoursPlugin(), cspPlugin(apiOrigin)],
    define: {
      // Статичний прапорець: гілка з `await import('./engine')` усувається
      // збіркою цілком, тож local-only бандл фізично не містить мережевого коду.
      "import.meta.env.VITE_SYNC": JSON.stringify(process.env.VITE_SYNC ?? "off"),
      // Другий прапорець, і за замовчуванням він `off`.
      //
      // Бекенд нагадувань готовий, але gate `future-telegram-reminders.md` — ні:
      // тексти згод лишаються `0.9` з плейсхолдером замість імені контролера, а
      // privacy/security/clinical review реальної доставки не підписаний. Доки
      // так, §10 вимагає, щоб чесний стан «недоступно» лишався в UI — тож шлях
      // реалізований, але усувається зі збірки так само фізично, як sync.
      "import.meta.env.VITE_REMINDERS": JSON.stringify(
        process.env.VITE_REMINDERS ?? "off",
      ),
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
      // api тим самим тунелем, що й web — лише для стенду.
      //
      // Telegram відкриває Mini App тільки через HTTPS, тобто через тунель, а
      // акаунт ngrok з одним доменом не дає api власної публічної адреси. Без
      // проксі стенд вироджувався в `http://localhost:8000`, до якого телефон
      // не достукається, і шлях Mini App → api → БД неможливо було пройти
      // руками взагалі.
      //
      // Наслідок, який треба назвати: за проксі web і api стають ОДНИМ
      // походженням, тож стенд перестає перевіряти CORS і preflight. Саме там
      // жила відсутність `PUT` у `allow_methods` — дефект, який знайшов лише
      // крос-origin прогін. Тому job `sync-e2e` у CI лишається крос-origin
      // (`localhost:4174` проти `localhost:8000`) і проксі не використовує:
      // fidelity перевірки живе там, зручність — тут.
      proxy:
        apiProxy === ""
          ? undefined
          : {
              "/v1": { target: apiProxy, changeOrigin: true },
              "/health": { target: apiProxy, changeOrigin: true },
            },
    },
    build: {
      outDir: process.env.VITE_OUT_DIR ?? "dist",
      sourcemap: mode === "development",
    },
  };
});
