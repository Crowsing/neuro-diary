import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

// §7 фіксує три механічні правила реалізації, і два з них — властивості
// вихідного тексту, а не поведінки: публічний API не має параметра `nonce`, а
// сам nonce народжується всередині виклику. Типи цього не ловлять — `nonce`
// можна додати з дефолтним значенням, і всі поведінкові тести лишаться зеленими.
const ROOT = fileURLToPath(new URL('.', import.meta.url));

/** Файли, яким випадковість потрібна за призначенням (nonce, R, device_id). */
const RANDOMNESS_ALLOWED = ['envelope.ts', 'keys.ts', 'passphrase/generate.ts'];

function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((item) => {
    const path = join(dir, item.name);
    if (item.isDirectory()) return sources(path);
    if (!item.name.endsWith('.ts') || item.name.endsWith('.test.ts')) return [];
    return [path];
  });
}

const files = sources(ROOT);
const relative = (file: string): string => file.slice(ROOT.length).replace(/\\/g, '/');

describe('crypto module — public API discipline (§7)', () => {
  it('covers every source file in crypto/', () => {
    expect(files.length).toBeGreaterThan(0);
  });

  it.each(files.map(relative))('%s exports no parameter named nonce or iv', (name) => {
    const text = readFileSync(join(ROOT, name), 'utf-8');
    const signatures = text.match(/export\s+(?:async\s+)?function[^)]*\)/g) ?? [];
    for (const signature of signatures) {
      const params = signature.slice(signature.indexOf('(') + 1);
      expect(params.toLowerCase()).not.toMatch(/\b(nonce|iv)\b/);
    }
  });

  // Довжина nonce — не nonce: `NONCE_BYTES` описує формат payload і потрібна
  // читачам конверта. Забороняється експортувати сам nonce або будь-що, що ним
  // керує ззовні.
  const SIZE_CONSTANTS = ['NONCE_BYTES'];

  it.each(files.map(relative))('%s exports no nonce-shaped value', (name) => {
    const text = readFileSync(join(ROOT, name), 'utf-8');
    const exported = text.match(/export\s+(?:const|function|async function)\s+(\w+)/g) ?? [];
    for (const declaration of exported) {
      const symbol = declaration.split(/\s+/).at(-1) ?? '';
      if (SIZE_CONSTANTS.includes(symbol)) continue;
      expect(symbol.toLowerCase()).not.toContain('nonce');
    }
  });

  it('keeps randomness inside the files that own it', () => {
    const withRandom = files
      .filter((file) => /getRandomValues/.test(readFileSync(file, 'utf-8')))
      .map(relative);
    expect(withRandom).toContain('envelope.ts');
    for (const name of withRandom) {
      expect(RANDOMNESS_ALLOWED).toContain(name);
    }
  });

  it('derives the nonce from randomness only, never from a key, a counter or a clock', () => {
    const text = readFileSync(join(ROOT, 'envelope.ts'), 'utf-8');
    const assignments = text.match(/const\s+nonce\s*=\s*[^;]+/g) ?? [];
    expect(assignments).toHaveLength(1);
    expect(assignments[0]).toContain('crypto.getRandomValues');
  });
});
