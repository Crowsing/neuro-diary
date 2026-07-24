import type { ReactNode } from 'react';

export default function MobileShell({ children }: { children: ReactNode }) {
  return (
    <main className="nd-mobile-shell" aria-label="Неврологічний щоденник">
      {children}
    </main>
  );
}
