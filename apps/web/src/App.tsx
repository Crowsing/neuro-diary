import { NowProvider } from './state/clock';
import { AppProvider } from './state/store';
import AppShell from './components/frame/AppShell';

export default function App() {
  return (
    <NowProvider>
      <AppProvider>
        <AppShell />
      </AppProvider>
    </NowProvider>
  );
}
