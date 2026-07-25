import { describe, expect, it } from 'vitest';
import { META_IGNORED, buildDirectives, metaSafe, toPolicy } from './csp';

const directives = buildDirectives('http://localhost:8000');

describe('CSP — §13.17', () => {
  it('allows the Telegram SDK, which script-src self would block', () => {
    expect(directives['script-src']).toContain('https://telegram.org');
    expect(directives['script-src']).toContain("'self'");
  });

  it('allows WebAssembly without allowing eval', () => {
    // Без 'wasm-unsafe-eval' Argon2id мовчки не запуститься, і всі опиняться
    // на PBKDF2 — рівно те, чим лякає §13.17.
    expect(directives['script-src']).toContain("'wasm-unsafe-eval'");
    expect(directives['script-src']).not.toContain("'unsafe-eval'");
    expect(directives['script-src']).not.toContain("'unsafe-inline'");
  });

  it('denies everything not named', () => {
    expect(directives['default-src']).toEqual(["'none'"]);
    expect(directives['object-src']).toEqual(["'none'"]);
    expect(directives['base-uri']).toEqual(["'none'"]);
    expect(directives['form-action']).toEqual(["'none'"]);
  });

  it('lets the app reach its own api and nothing else', () => {
    expect(directives['connect-src']).toEqual(["'self'", 'http://localhost:8000']);
  });

  it('drops the api origin from connect-src when there is none', () => {
    expect(buildDirectives('')['connect-src']).toEqual(["'self'"]);
  });

  it('keeps frame-ancestors out of the meta tag, where it is ignored', () => {
    const safe = metaSafe(directives);
    expect(directives['frame-ancestors']).toContain('https://web.telegram.org');
    expect(safe['frame-ancestors']).toBeUndefined();
    for (const name of META_IGNORED) expect(safe[name]).toBeUndefined();
  });

  it('serializes to a policy string', () => {
    const policy = toPolicy(metaSafe(directives));
    expect(policy).toContain("default-src 'none'");
    expect(policy).toContain("script-src 'self' https://telegram.org 'wasm-unsafe-eval'");
    expect(policy).not.toContain('frame-ancestors');
    expect(policy.split('; ').length).toBeGreaterThan(5);
  });
});
