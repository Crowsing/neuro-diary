// Точка монтування екранів синхронізації в оболонці застосунку.
//
// Обгортка існує заради однієї властивості: у local-only збірці оверлея немає
// ФІЗИЧНО. Прапорець статичний, тож тернарник згортається в `null`, а разом із
// ним зникає і `import()`, і все, що за ним, — зокрема словник на 4096 слів,
// який інакше опинився б у бандлі, де фраза не вводиться ніколи.

import { Suspense, lazy } from 'react';

const SYNC_ENABLED = import.meta.env.VITE_SYNC === 'on';

const Overlay = SYNC_ENABLED ? lazy(() => import('./SyncOverlayHost')) : null;

export default function SyncMount() {
  if (Overlay === null) return null;
  return (
    <Suspense fallback={null}>
      <Overlay />
    </Suspense>
  );
}
