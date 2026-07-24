import { describe, it, expect } from 'vitest';
import { cycleDay } from './cycle';

describe('cycleDay', () => {
  it('день 1 у день старту', () => {
    expect(cycleDay('2026-07-21', ['2026-07-21'])).toBe(1);
  });

  it('рахує від старту: наступний день — день 2', () => {
    expect(cycleDay('2026-07-22', ['2026-07-21'])).toBe(2);
  });

  it('бере останній старт, що не пізніший за iso (несортований вхід)', () => {
    expect(cycleDay('2026-07-21', ['2026-08-01', '2026-06-01', '2026-07-09'])).toBe(13);
  });

  it('старт лише в майбутньому → null', () => {
    expect(cycleDay('2026-07-21', ['2026-08-01'])).toBeNull();
  });

  it('порожній список → null', () => {
    expect(cycleDay('2026-07-21', [])).toBeNull();
  });

  it('рівно 60 днів → 60, 61 → null', () => {
    expect(cycleDay('2026-07-21', ['2026-05-23'])).toBe(60);
    expect(cycleDay('2026-07-21', ['2026-05-22'])).toBeNull();
  });
});
