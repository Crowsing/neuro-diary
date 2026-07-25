import { useSyncExternalStore } from 'react';
import { useApp } from '../../state/store';
import PhoneFrame from './PhoneFrame';
import BottomNav from '../nav/BottomNav';
import Toast from '../ui/Toast';
import DialogHost from '../dialogs/DialogHost';
import Onboarding from '../screens/Onboarding';
import Today from '../screens/Today';
import CheckinScreen from '../screens/checkin/CheckinScreen';
import HistoryTab from '../screens/history/HistoryTab';
import DayDetails from '../screens/history/DayDetails';
import TrendsTab from '../screens/trends/TrendsTab';
import SymptomDetail from '../screens/trends/SymptomDetail';
import ReportTab from '../screens/report/ReportTab';
import SettingsTab from '../screens/settings/SettingsTab';
import CatalogScreen from '../screens/settings/CatalogScreen';
import GroupSettingsScreen from '../screens/settings/GroupSettingsScreen';
import PrivacyScreen from '../screens/settings/PrivacyScreen';
import SafetyScreen from '../screens/settings/SafetyScreen';
import CrisisScreen from '../screens/settings/CrisisScreen';
import DesktopDashboard from '../screens/desktop/DesktopDashboard';
import DevelopmentBanner from '../../sync/ui/DevelopmentBanner';
import SyncMount from '../../sync/ui/SyncMount';
import { downloadJson } from '../../lib/export';

const DESKTOP_QUERY = '(min-width: 900px)';

function subscribeToDesktopLayout(onChange: () => void) {
  if (typeof window === 'undefined') return () => {};
  const query = window.matchMedia(DESKTOP_QUERY);
  query.addEventListener('change', onChange);
  return () => query.removeEventListener('change', onChange);
}

function desktopSnapshot() {
  return typeof window !== 'undefined' && window.matchMedia(DESKTOP_QUERY).matches;
}

export function useDesktopLayout() {
  return useSyncExternalStore(subscribeToDesktopLayout, desktopSnapshot, () => false);
}

function ScreenOutlet() {
  const { state } = useApp();

  if (state.view === 'ob') return <Onboarding />;

  switch (state.sub) {
    case 'checkin': return <CheckinScreen />;
    case 'day': return <DayDetails />;
    case 'sym': return <SymptomDetail />;
    case 'catalog': return <CatalogScreen />;
    case 'groups': return <GroupSettingsScreen />;
    case 'privacy': return <PrivacyScreen />;
    case 'safety': return <SafetyScreen />;
    case 'crisis': return <CrisisScreen />;
  }

  switch (state.tab) {
    case 'history': return <HistoryTab />;
    case 'trends': return <TrendsTab />;
    case 'report': return <ReportTab />;
    case 'set': return <SettingsTab />;
    default: return <Today />;
  }
}

export interface AppShellProps {
  theme?: 'light' | 'dark' | 'contrast';
  reduceMotion?: boolean;
}

export default function AppShell({ theme = 'light', reduceMotion = false }: AppShellProps) {
  const { state, persistenceError, retrySave } = useApp();
  const isDesktop = useDesktopLayout();
  const isApp = state.view === 'app';
  const screen = <ScreenOutlet />;

  return (
    <div
      className={reduceMotion ? 'nd-page nd-reduce' : 'nd-page'}
      data-layout={isDesktop ? 'desktop' : 'mobile'}
      data-theme={theme}
    >
      {isDesktop ? (
        <DesktopDashboard>{screen}</DesktopDashboard>
      ) : (
        <PhoneFrame>
          {screen}
          {isApp && state.sub === null && <BottomNav />}
        </PhoneFrame>
      )}
      {persistenceError && (
        <div className="nd-storage-error" role="alert" aria-live="assertive">
          <div style={{ fontSize: 14, fontWeight: 700 }}>Не вдалося зберегти зміни в браузері. Дані ще доступні в цій вкладці.</div>
          <div className="nd-storage-error-actions">
            <button className="btn btn-secondary" style={{ minHeight: 44 }} onClick={retrySave}>Повторити збереження</button>
            <button className="btn btn-secondary" style={{ minHeight: 44, whiteSpace: 'normal' }} onClick={() => downloadJson(state)}>Експортувати JSON для відновлення</button>
          </div>
        </div>
      )}
      {isApp && <DialogHost />}
      {isApp && <Toast />}
      {/* Банер стенду й екрани sync живуть тут, а не всередині вкладок: вони
          мусять бути досяжними з будь-якого екрана, і саме через їхню
          відсутність у дереві половина гарантій §7 не діяла в застосунку. */}
      <DevelopmentBanner />
      <SyncMount />
    </div>
  );
}
