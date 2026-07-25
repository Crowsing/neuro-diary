import { describe, expect, it } from 'vitest';
import { assertRecordPath, buildAad, isRecordPath } from './aad';

// Еталон будується незалежним шляхом (TextEncoder над літералом), щоб тест не
// повторював реалізацію: інакше він підтверджував би сам себе.
function reference(path: string, clientTsMs: number, tombstone: boolean): Uint8Array {
  return new TextEncoder().encode(
    `ndv1\x1f${path}\x1f${clientTsMs}\x1f${tombstone ? '1' : '0'}`
  );
}

describe('assertRecordPath — closed path list', () => {
  it('accepts the five singletons', () => {
    for (const path of ['cycle', 'catalog', 'groups', 'settings', 'manifest']) {
      expect(assertRecordPath(path)).toBe(path);
    }
  });

  it('accepts a zero-padded entry date', () => {
    expect(assertRecordPath('entry:2026-01-05')).toBe('entry:2026-01-05');
  });

  it.each([
    'entry:2026-1-5',
    'entry:',
    'entry:2026-01-05 ',
    ' entry:2026-01-05',
    'Cycle',
    'manifest2',
    'settings ',
    'entry:2026-01-05\nmanifest',
    '',
    'cycle:2026-01-05'
  ])('rejects %j', (path) => {
    expect(() => assertRecordPath(path)).toThrow();
    expect(isRecordPath(path)).toBe(false);
  });
});

describe('buildAad — §7 byte layout', () => {
  it('matches the reference vector for an entry', () => {
    const aad = buildAad(assertRecordPath('entry:2026-01-15'), 1768435200000, false);
    expect(aad).toEqual(reference('entry:2026-01-15', 1768435200000, false));
  });

  it('matches the reference vector for a singleton', () => {
    const aad = buildAad(assertRecordPath('cycle'), 1, false);
    expect(aad).toEqual(reference('cycle', 1, false));
  });

  it('mirrors the tombstone column in the trailing flag', () => {
    const path = assertRecordPath('entry:2026-01-15');
    expect(buildAad(path, 1768435200000, true)).toEqual(
      reference('entry:2026-01-15', 1768435200000, true)
    );
    expect(buildAad(path, 1768435200000, true)).not.toEqual(
      buildAad(path, 1768435200000, false)
    );
  });

  it('separates every field with 0x1f and nothing else', () => {
    const aad = buildAad(assertRecordPath('catalog'), 42, false);
    expect([...aad].filter((b) => b === 0x1f)).toHaveLength(3);
  });

  it('encodes client_ts_ms as a decimal integer', () => {
    const aad = buildAad(assertRecordPath('cycle'), 1768435200000, false);
    expect(new TextDecoder().decode(aad)).toContain('\x1f1768435200000\x1f');
  });

  it.each([-1, 0, 1.5, Number.NaN, Number.MAX_SAFE_INTEGER + 2])(
    'rejects %p as client_ts_ms',
    (ts) => {
      expect(() => buildAad(assertRecordPath('cycle'), ts, false)).toThrow();
    }
  );
});
