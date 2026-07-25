// Картка синхронізації в Налаштуваннях.
//
// У local-only збірці не рендериться взагалі: `enabled` там фіксовано `false`,
// і жодна майбутня функція не з'являється в UI як доступна (§12, наскрізний
// gate). Про стійкість фрази тут не сказано нічого — словник ще не пройшов
// локалізаційного review, тож ані «72 бітів», ані індикатора сили (§7).

import Toggle from '../../components/ui/Toggle';
import { useSync } from '../provider';
import { SYNC_COPY } from './copy';

export default function SyncSettings() {
  const sync = useSync();
  if (!sync.enabled) return null;

  return (
    <div className="card" style={{ gap: 12 }} data-testid="sync-settings">
      <span style={{ fontSize: 14.5, fontWeight: 700 }}>{SYNC_COPY.settingsTitle}</span>
      <span style={{ fontSize: 12.5 }} className="text-muted" data-testid="sync-status">
        {sync.active ? SYNC_COPY.settingsOn : SYNC_COPY.settingsOff}
      </span>

      {sync.active ? (
        <>
          <button
            className="btn btn-secondary"
            style={{ minHeight: 44 }}
            data-testid="sync-now"
            onClick={sync.syncNow}
          >
            {SYNC_COPY.settingsTitle}
          </button>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
            <Toggle
              selected={sync.cycleSync}
              onClick={() => sync.openConsent('cycle_sync')}
              aria={SYNC_COPY.cycleTitle}
            />
            <span style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
              <span style={{ fontSize: 14.5, fontWeight: 700 }}>
                {SYNC_COPY.cycleTitle}
              </span>
            </span>
          </div>
          <button
            className="btn btn-secondary"
            style={{ minHeight: 44 }}
            data-testid="sync-change-passphrase"
            onClick={sync.openChangePassphrase}
          >
            {SYNC_COPY.changeTitle}
          </button>
          <button
            className="btn btn-secondary"
            style={{ minHeight: 44 }}
            data-testid="sync-rekey"
            onClick={sync.openRekey}
          >
            {SYNC_COPY.rekeyTitle}
          </button>
        </>
      ) : (
        <>
          <button
            className="btn btn-secondary"
            style={{ minHeight: 44 }}
            data-testid="sync-enable"
            onClick={() => sync.openConsent('health_sync')}
          >
            {SYNC_COPY.enable}
          </button>
          {/* §4.3: cycle_sync надається лише за активної health_sync — тож
              перемикач недоступний і причина названа, а не замовчана. */}
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, opacity: 0.55 }}>
            <Toggle selected={false} onClick={() => undefined} aria={SYNC_COPY.cycleTitle} />
            <span style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
              <span style={{ fontSize: 14.5, fontWeight: 700 }}>
                {SYNC_COPY.cycleTitle}
              </span>
              <span style={{ fontSize: 12 }} className="text-muted">
                {SYNC_COPY.cycleNeedsHealth}
              </span>
            </span>
          </div>
        </>
      )}
    </div>
  );
}
