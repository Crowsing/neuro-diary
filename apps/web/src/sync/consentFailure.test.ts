import { describe, expect, it } from 'vitest';
import { SyncError } from './client';
import { consentFailureOf, meansNoAccount } from './consentFailure';
import { VaultError } from './vault';

describe('причина відмови на екрані згод', () => {
  it('читає сирий SyncError, у якого немає failure', () => {
    // Саме на цьому спіткнулася перша редакція: `ensureSession` кидає
    // `SyncError`, і читання лише `.failure` давало 'server' — тобто «не
    // вдалося» замість «акаунта немає».
    expect(consentFailureOf(new SyncError('no_account'))).toBe('no_account');
    expect(meansNoAccount(consentFailureOf(new SyncError('no_account')))).toBe(true);
  });

  it('читає VaultError, у якого немає code', () => {
    expect(consentFailureOf(new VaultError('offline'))).toBe('offline');
  });

  it('віддає перевагу серверному коду над класом статусу', () => {
    // 409 приходить і як `confirm_required`, і як `consent_precondition`; клас
    // 'conflict' їх не розрізняє, а екран мусить.
    const refusal = new SyncError('conflict', null, [], 'confirm_required');
    expect(consentFailureOf(refusal)).toBe('confirm_required');
  });

  it('порожній серверний код не перемагає класу', () => {
    expect(consentFailureOf(new SyncError('rate_limited', null, [], ''))).toBe(
      'rate_limited'
    );
  });

  it('невідома форма деградує до server, а не кидає', () => {
    expect(consentFailureOf(new Error('boom'))).toBe('server');
    expect(consentFailureOf(null)).toBe('server');
    expect(consentFailureOf(undefined)).toBe('server');
    expect(consentFailureOf('рядок')).toBe('server');
  });

  it('лише no_account означає «згод немає»', () => {
    // Інакше справжній збій тихо показувався б як порожній перелік — тобто
    // користувачка думала б, що згод немає, коли їх просто не прочитали.
    for (const other of ['offline', 'server', 'unauthenticated', 'rate_limited']) {
      expect(meansNoAccount(other)).toBe(false);
    }
  });
});
