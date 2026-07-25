// Банер тестового стенду — блокер 1 промпту Фази 2.
//
// Джерелом є відповідь api (`GET /health` → `app_env`), а не прапорець збірки:
// так стенд не може «забути» показати банер через невірний білд. Факт, що
// згоди створюються з незамороженим текстом, має бути видимим, а не
// замовчуваним.

import { useEffect, useState } from 'react';
import { SYNC_COPY } from './copy';

const API_ORIGIN = import.meta.env.VITE_API_ORIGIN;

export default function DevelopmentBanner() {
  const [isDevelopment, setDevelopment] = useState(false);

  useEffect(() => {
    if (import.meta.env.VITE_SYNC !== 'on' || API_ORIGIN === '') return;
    let cancelled = false;
    void fetch(`${API_ORIGIN}/health`)
      .then((response) => (response.ok ? response.json() : null))
      .then((body: { app_env?: string } | null) => {
        if (!cancelled && body?.app_env === 'development') setDevelopment(true);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  if (!isDevelopment) return null;

  return (
    <div role="status" className="nd-inline-error" data-testid="development-banner">
      {SYNC_COPY.developmentBanner}
    </div>
  );
}
