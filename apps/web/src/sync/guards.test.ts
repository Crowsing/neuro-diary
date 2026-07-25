import { describe, expect, it } from 'vitest';
import {
  MASS_DELETE_MINIMUM,
  MASS_DELETE_SHARE,
  type LocalRecordState,
  needsMassDeleteConfirmation,
  presenceAuthority
} from './guards';

const acked = (path: string): LocalRecordState => ({ path, acked: true, dirty: false });
const dirty = (path: string): LocalRecordState => ({ path, acked: true, dirty: true });
const fresh = (path: string): LocalRecordState => ({ path, acked: false, dirty: true });

const consents = (kinds: string[], at = 10) => ({
  activeKinds: kinds,
  fetchedAtRevision: at
});

describe('presenceAuthority — the post-410 rule of §9.4', () => {
  it('prunes a clean acked record the server no longer has', () => {
    const verdict = presenceAuthority({
      local: [acked('entry:2026-01-15')],
      serverPaths: new Set(),
      consents: consents(['health_sync']),
      pullRevision: 10
    });
    expect(verdict.prune).toEqual(['entry:2026-01-15']);
    expect(verdict.needsConfirmation).toEqual([]);
  });

  it('keeps a record the server still has', () => {
    const verdict = presenceAuthority({
      local: [acked('entry:2026-01-15')],
      serverPaths: new Set(['entry:2026-01-15']),
      consents: consents(['health_sync']),
      pullRevision: 10
    });
    expect(verdict.keep).toEqual(['entry:2026-01-15']);
    expect(verdict.prune).toEqual([]);
  });

  it('keeps a record that was never synchronized', () => {
    const verdict = presenceAuthority({
      local: [fresh('entry:2026-01-15')],
      serverPaths: new Set(),
      consents: consents(['health_sync']),
      pullRevision: 10
    });
    expect(verdict.keep).toEqual(['entry:2026-01-15']);
  });

  it('asks before dropping a record that was acked and then edited', () => {
    const verdict = presenceAuthority({
      local: [dirty('entry:2026-01-15')],
      serverPaths: new Set(),
      consents: consents(['health_sync']),
      pullRevision: 10
    });
    expect(verdict.needsConfirmation).toEqual(['entry:2026-01-15']);
    expect(verdict.prune).toEqual([]);
  });

  it('prunes nothing without a consent snapshot at all', () => {
    const verdict = presenceAuthority({
      local: [acked('entry:2026-01-15'), acked('cycle')],
      serverPaths: new Set(),
      consents: null,
      pullRevision: 10
    });
    expect(verdict.prune).toEqual([]);
    expect(verdict.keep).toHaveLength(2);
  });

  it('prunes nothing when the consent snapshot is older than this pull', () => {
    const verdict = presenceAuthority({
      local: [acked('entry:2026-01-15')],
      serverPaths: new Set(),
      consents: consents(['health_sync'], 9),
      pullRevision: 10
    });
    expect(verdict.prune).toEqual([]);
  });

  it('never prunes the cycle record while cycle_sync is inactive', () => {
    // Це найтяжчий дефект, який правило закриває: сервер видаляє записи циклу
    // при відкликанні згоди, і без винятку пристрій стер би локальні дати.
    const verdict = presenceAuthority({
      local: [acked('cycle')],
      serverPaths: new Set(),
      consents: consents(['health_sync']),
      pullRevision: 10
    });
    expect(verdict.prune).toEqual([]);
    expect(verdict.keep).toEqual(['cycle']);
  });

  it('prunes the cycle record once the consent is active again and it is gone', () => {
    const verdict = presenceAuthority({
      local: [acked('cycle')],
      serverPaths: new Set(),
      consents: consents(['health_sync', 'cycle_sync']),
      pullRevision: 10
    });
    expect(verdict.prune).toEqual(['cycle']);
  });
});

describe('needsMassDeleteConfirmation — §9.4', () => {
  it('pins the thresholds of the plan', () => {
    expect(MASS_DELETE_SHARE).toBe(0.2);
    expect(MASS_DELETE_MINIMUM).toBe(10);
  });

  it('asks above twenty percent and at least ten records', () => {
    expect(needsMassDeleteConfirmation(40, 10)).toBe(true);
  });

  it('stays silent at exactly twenty percent', () => {
    expect(needsMassDeleteConfirmation(50, 10)).toBe(false);
  });

  it('stays silent below ten records however large the share', () => {
    expect(needsMassDeleteConfirmation(10, 9)).toBe(false);
    expect(needsMassDeleteConfirmation(9, 9)).toBe(false);
  });

  it('stays silent on an ordinary tidy-up of a large diary', () => {
    expect(needsMassDeleteConfirmation(1000, 15)).toBe(false);
  });

  it('asks when almost everything disappears at once', () => {
    expect(needsMassDeleteConfirmation(100, 100)).toBe(true);
  });
});
