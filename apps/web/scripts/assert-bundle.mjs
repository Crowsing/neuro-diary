// Перевірка зібраного бандла — §9.6 і §13.3.
//
//   node apps/web/scripts/assert-bundle.mjs dist          # local-only
//   node apps/web/scripts/assert-bundle.mjs dist-sync --sync
//
// Два ризики, які вона закриває:
//
//  * демо-дані. `genDemo` викликається лише з тестів і e2e, але прапорець
//    збірки може зламатися мовчки — тому перевіряється сам артефакт;
//  * мережевий код у local-only збірці. Гілка `import.meta.env.VITE_SYNC` має
//    усуватися збіркою цілком, тож у local-only бандлі не має бути ані
//    `fetch(`, ані шляхів /v1/sync.

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

const [, , directory, ...flags] = process.argv;
if (!directory) {
  console.error('usage: assert-bundle.mjs <dist-dir> [--sync]');
  process.exit(2);
}
const syncBuild = flags.includes('--sync');

function files(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((item) => {
    const path = join(dir, item.name);
    if (item.isDirectory()) return files(path);
    return item.name.endsWith('.js') || item.name.endsWith('.html') ? [path] : [];
  });
}

const bundle = files(directory)
  .map((path) => ({ path, text: readFileSync(path, 'utf-8') }))
  .filter((entry) => entry.text.length > 0);

const failures = [];

function refuse(condition, message) {
  if (condition) failures.push(message);
}

const all = bundle.map((entry) => entry.text).join('\n');

// 1. Демо-дані ніколи не потрапляють у зібраний застосунок.
//
// Імена функцій для цього не годяться: esbuild перейменовує їх при мінімізації,
// тож `genDemo` не знайшовся б навіть у бандлі, який його містить. Шукаються
// рядкові літерали, які мінімізатор не чіпає, — маркер посіву e2e і насіння
// детермінованого генератора демо-даних.
refuse(all.includes('nd_e2e_seeded'), 'bundle contains the e2e seeding marker');
refuse(all.includes('987654321'), 'bundle contains the demo data seed');

// 2. Мережа до api — лише у sync-збірці.
//
// Перевіряються сліди саме нашого клієнта: шляхи /v1/ і заголовок Bearer.
// Голий `fetch(` для цього не годиться — Vite вбудовує власний шім
// modulepreload, який дістає лише чанки того самого походження. Те, що
// застосунок не робить жодного зовнішнього запиту в рантаймі, доводить
// наявний e2e ac8-no-network.
if (syncBuild) {
  refuse(!all.includes('/v1/sync/push'), 'sync bundle has no sync client');
} else {
  refuse(all.includes('/v1/sync/'), 'local-only bundle references sync endpoints');
  refuse(all.includes('/v1/auth/telegram'), 'local-only bundle references auth');
  refuse(all.includes('Bearer '), 'local-only bundle carries an Authorization header');
}

// 3. CSP присутня в index.html і дозволяє WASM, не дозволяючи eval.
const html = bundle.find((entry) => entry.path.endsWith('index.html'));
refuse(html === undefined, 'no index.html in the bundle');
if (html !== undefined) {
  refuse(
    !html.text.includes('Content-Security-Policy'),
    'index.html carries no Content-Security-Policy'
  );
  refuse(
    !html.text.includes("'wasm-unsafe-eval'"),
    "CSP does not allow 'wasm-unsafe-eval'; Argon2id would silently fall back"
  );
  // Токен, а не підрядок: 'wasm-unsafe-eval' містить 'unsafe-eval' усередині,
  // тож наївна перевірка підрядком мовчала б і на справжньому 'unsafe-eval'.
  const policy = /content="([^"]*)"/.exec(html.text)?.[1] ?? '';
  const scriptSrc =
    policy
      .split(';')
      .map((directive) => directive.trim())
      .find((directive) => directive.startsWith('script-src'))
      ?.split(/\s+/)
      .slice(1) ?? [];
  refuse(scriptSrc.includes("'unsafe-eval'"), "CSP allows 'unsafe-eval'");
  refuse(scriptSrc.includes("'unsafe-inline'"), "CSP allows inline script");
  refuse(
    html.text.includes('frame-ancestors'),
    'frame-ancestors belongs to the HTTP header; a meta tag ignores it'
  );
}

// 4. Argon2 живе в окремому чанку, а не у вхідному графі.
const entryChunk = bundle
  .filter((entry) => entry.path.endsWith('.js'))
  .sort((a, b) => statSync(b.path).size - statSync(a.path).size)[0];
if (entryChunk !== undefined) {
  refuse(
    entryChunk.text.includes('argon2id') && entryChunk.text.includes('memorySize'),
    'the entry chunk embeds argon2; it must stay behind a dynamic import'
  );
}

if (failures.length > 0) {
  for (const failure of failures) console.error(`✗ ${failure}`);
  process.exit(1);
}
console.log(
  `✓ ${directory}: ${bundle.length} files, ${syncBuild ? 'sync' : 'local-only'} build`
);
