import { NowProvider } from './state/clock';
import { AppProvider } from './state/store';
import { SyncProvider } from './sync/provider';
import AppShell from './components/frame/AppShell';

export default function App() {
  return (
    <NowProvider>
      <AppProvider>
        <SyncProvider>
          <AppShell />
        </SyncProvider>
      </AppProvider>
    </NowProvider>
  );
}
