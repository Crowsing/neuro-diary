// Оверлей екранів синхронізації.
//
// Живе поза доменним станом: провайдер тримає власний `stage`, тож у
// `AppState` не з'являється жодного нового поля, а `types.ts` і `persist.ts`
// лишаються недоторканими.

import { useState } from 'react';
import ConsentDialog from './ConsentDialog';
import PassphraseScreen from './PassphraseScreen';
import { useSync } from '../provider';

export default function SyncOverlayHost() {
  const sync = useSync();
  const [accepted, setAccepted] = useState<string | null>(null);

  if (sync.stage === 'local_only' || sync.stage === 'idle') return null;

  return (
    <div className="dialog" role="dialog" aria-modal="true">
      {sync.stage === 'consent' ? (
        <ConsentDialog
          kind="health_sync"
          onAccept={(grant) => {
            setAccepted(grant.sha256);
            sync.openPassphrase();
          }}
          onDecline={sync.dismiss}
        />
      ) : null}

      {sync.stage === 'passphrase' ? (
        <PassphraseScreen
          onReady={() => sync.dismiss()}
          onCancel={sync.dismiss}
        />
      ) : null}

      {sync.stage === 'error' ? (
        <p role="alert" data-testid="sync-error">
          {sync.lastError}
        </p>
      ) : null}

      {accepted === null ? null : (
        <span hidden data-testid="accepted-consent-digest">
          {accepted}
        </span>
      )}
    </div>
  );
}
