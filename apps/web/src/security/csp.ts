// Content Security Policy — §13.17 плану.
//
// Три обмеження, які треба задовольнити одночасно:
//
//  * `telegram-web-app.js` обов'язковий і вантажиться з telegram.org, тож
//    `script-src 'self'` несумісний із Mini App;
//  * Argon2id (WASM) потребує `'wasm-unsafe-eval'`; без нього WebAssembly
//    мовчки не запускається — і всі опиняються на PBKDF2, чого §13.17 і лякає;
//  * `frame-ancestors` у <meta> ігнорується специфікацією, а Mini App у
//    Telegram Web працює саме в iframe — тож ця директива існує лише як
//    HTTP-заголовок на reverse proxy.
//
// Політика живе тут одним джерелом: і плагін збірки, і тести читають її звідси.

export type Directives = Readonly<Record<string, readonly string[]>>;

export function buildDirectives(apiOrigin: string): Directives {
  return {
    'default-src': ["'none'"],
    'script-src': ["'self'", 'https://telegram.org', "'wasm-unsafe-eval'"],
    // Інлайнові стилі — наслідок локальної геометрії в компонентах; вони не
    // виконують коду. `unsafe-inline` для script-src не з'являється ніде.
    'style-src': ["'self'", "'unsafe-inline'"],
    // `data:` тут не послаблення, а необхідність: збірка інлайнить дрібні
    // шрифтові файли в data-URI, і без цього джерела браузер їх блокує —
    // застосунок мовчки втрачає типографіку. Знайдено e2e, який слухає
    // `securitypolicyviolation`, бо жодна інша перевірка цього не бачила.
    'font-src': ["'self'", 'data:'],
    'img-src': ["'self'", 'data:'],
    'connect-src': ["'self'", apiOrigin].filter((value) => value !== ''),
    'base-uri': ["'none'"],
    'form-action': ["'none'"],
    'object-src': ["'none'"],
    'frame-ancestors': ['https://web.telegram.org', 'https://*.telegram.org']
  };
}

/** Директиви, які <meta http-equiv> ігнорує за специфікацією. */
export const META_IGNORED = ['frame-ancestors', 'report-uri', 'sandbox'] as const;

export function metaSafe(directives: Directives): Directives {
  return Object.fromEntries(
    Object.entries(directives).filter(
      ([name]) => !META_IGNORED.includes(name as (typeof META_IGNORED)[number])
    )
  );
}

export function toPolicy(directives: Directives): string {
  return Object.entries(directives)
    .map(([name, values]) => `${name} ${values.join(' ')}`)
    .join('; ');
}
