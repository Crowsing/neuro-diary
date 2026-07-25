import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

/**
 * Реєстр читається тут напряму з файлів, а не через віртуальний модуль: тест
 * має доводити, що хеш у реєстрі дорівнює SHA-256 БАЙТІВ файлу — того самого
 * значення, яке apps/api звіряє при grant. Якби тест брав хеш із того самого
 * місця, що й застосунок, він доводив би лише самому собі.
 */
const ROOT = fileURLToPath(new URL('../../../../consent-copy/', import.meta.url));

interface RegistryEntry {
  kind: string;
  text_version: string;
  locale: string;
  file: string;
  sha256: string;
  frozen: boolean;
}

const registry = JSON.parse(readFileSync(`${ROOT}registry.json`, 'utf-8')) as {
  schema: number;
  grants: RegistryEntry[];
  revocations?: RegistryEntry[];
};

const entries = [...registry.grants, ...(registry.revocations ?? [])];

describe('consent copy registry', () => {
  it('lists a text for each of the three consents', () => {
    expect(registry.grants.map((entry) => entry.kind).sort()).toEqual([
      'cycle_sync',
      'health_sync',
      'telegram_reminders'
    ]);
  });

  it.each(entries.map((entry) => [entry.file, entry]))(
    'the digest of %s equals the sha256 of the file bytes',
    (_file, entry) => {
      const bytes = readFileSync(`${ROOT}${(entry as RegistryEntry).file}`);
      expect(createHash('sha256').update(bytes).digest('hex')).toBe(
        (entry as RegistryEntry).sha256
      );
    }
  );

  it('keeps the texts at 0.9 and unfrozen, as phase 2 requires', () => {
    // Заморожування до 1.0 потребує імені контролера — блокер Фази 2, не її
    // завдання. Fail-closed guard сервера лишається чинним.
    for (const entry of registry.grants) {
      expect(entry.text_version.endsWith('@0.9')).toBe(true);
      expect(entry.frozen).toBe(false);
    }
  });
});
