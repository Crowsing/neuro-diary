// Екран «Динаміка» — порт docs/prototype/nd-v2.dc.html:
// шаблон рядки 208–223 (sc-if tTrends), логіка _vTrends рядки 1578–1593.
// Період-чипи 7/30/90, coverage-рядок, список активних симптомів зі зведеннями.

import { useApp } from '../../../state/store';
import { useNow } from '../../../state/clock';
import { navSub, trendsSet } from '../../../state/actions';
import { model } from '../../../lib/chart';
import type { ChartDeps } from '../../../lib/chart';
import { isoOff } from '../../../lib/dates';
import { filterByGroup, groupBadges, trendableSymptomIds, UNGROUPED_ID, visibleGroups } from '../../../lib/groups';
import { makeSymDef } from '../../../lib/utils';
import type { Period } from '../../../lib/types';
import Chip from '../../ui/Chip';
import ScreenHeader from '../../frame/ScreenHeader';

const PERIODS: Period[] = [7, 30, 90];

export default function TrendsTab() {
  const { state: st, dispatch } = useApp();
  const now = useNow();
  const tr = st.trends;
  const per = tr.period || 30;
  const symDef = makeSymDef(st.data.custom);
  const deps: ChartDeps = {
    now,
    entries: st.data.entries,
    cycleStarts: st.data.cycleStarts,
    symDef
  };
  let filled = 0;
  for (let offset = 0; offset > -per; offset--) {
    if (st.data.entries[isoOff(now, offset)]?.status === 'done') filled++;
  }
  const allTrendable = trendableSymptomIds(st.data);
  const filteredIds = filterByGroup(allTrendable, tr.groupId, st.data);
  const groups = visibleGroups(st.data);
  const trList = filteredIds
    .map((id) => {
      const s = symDef(id);
      if (!s) return null;
      const m = model(id, per, 100, 100, deps);
      const present = m.days.filter((day): day is typeof day & { v: number } => day.v !== null && day.v >= 1);
      const parts = [
        'було: ' + m.present + ' із ' + m.known + ' відомих',
        'підтверджено не було: ' + m.absent,
        'симптом не заповнено у завершені дні: ' + m.unknownCompleted
      ];
      if (s.type === 'scale' && present.length) {
        const avg = present.reduce((total, day) => total + day.v, 0) / present.length;
        parts.push('середня інтенсивність у дні зі значенням: ' + avg.toFixed(1) + '/5');
      }
      return {
        id,
        name: s.name,
        line: parts.join(' · '),
        badges: groupBadges(id, st.data),
        historical: !st.data.active.includes(id)
      };
    })
    .filter((x): x is NonNullable<typeof x> => x !== null);
  return (
    <div data-screen-label="Динаміка" className="nd-screen">
      <ScreenHeader title="Динаміка" />
      <div role="group" aria-label="Період динаміки" style={{ display: 'flex', gap: 7, flexWrap: 'wrap' }}>
        {PERIODS.map((p) => <Chip key={p} label={p + ' дн.'} selected={per === p} onClick={() => dispatch(trendsSet({ period: p }))} />)}
      </div>
      <span style={{ fontSize: '12.5px' }} className="text-muted">{'Заповнено ' + filled + ' із ' + per + ' днів · пропущені дні показуються як прогалини, без домальовування'}</span>
      <div role="group" aria-label="Фільтр симптомів за групою спостереження" style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
        <span style={{ fontSize: 12, fontWeight: 700 }}>Група спостереження</span>
        <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap' }}>
          <Chip label="Усі групи" selected={tr.groupId === null} onClick={() => dispatch(trendsSet({ groupId: null }))} />
          {groups.map((group) => (
            <Chip key={group.id} label={group.name} selected={tr.groupId === group.id} onClick={() => dispatch(trendsSet({ groupId: group.id }))} style={{ maxWidth: '100%', overflowWrap: 'anywhere' }} />
          ))}
          <Chip label="Без групи" selected={tr.groupId === UNGROUPED_ID} onClick={() => dispatch(trendsSet({ groupId: UNGROUPED_ID }))} />
        </div>
        <span style={{ fontSize: 12 }} className="text-muted">Фільтр використовує поточне групування й лише звужує список симптомів. Він не створює оцінку групи та не змінює числові ряди.</span>
      </div>
      {!trList.length && <div className="card"><p style={{ margin: 0, fontSize: 14 }} className="text-muted">За цим фільтром немає активних або історичних симптомів із записами.</p></div>}
      {trList.map((s) => (
        <button
          key={s.id}
          onClick={() => dispatch(navSub('sym', { trendsSym: s.id }))}
          className="card"
          style={{ textAlign: 'left', cursor: 'pointer', border: 'none', font: 'inherit', gap: 4, width: '100%', flexDirection: 'row', alignItems: 'center' }}
        >
          <span style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 3 }}>
            <span style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 5 }}>
              <span style={{ fontWeight: 700, fontSize: 14 }}>{s.name}</span>
              {s.historical && <span className="tag tag-neutral" style={{ fontSize: 10 }}>Історичний</span>}
              {s.badges.map((badge) => <span key={badge} className="tag tag-neutral" style={{ fontSize: 10, overflowWrap: 'anywhere' }}>{badge}</span>)}
            </span>
            <span style={{ fontSize: '12.5px' }} className="text-muted">{s.line}</span>
          </span>
          <svg width="14" height="14" style={{ flex: 'none' }}><path d="M5 2 L10 7 L5 12" fill="none" stroke="var(--color-accent)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </button>
      ))}
      <span style={{ fontSize: 12 }} className="text-muted">Відомі спостереження для конкретного симптому — це дні, коли його позначено як наявний або явно підтверджено його відсутність. Незавершені та невідомі значення не входять у знаменник.</span>
      <span style={{ fontSize: 12 }} className="text-muted">Порівняння — це спостереження, а не доказ причинно-наслідкового звʼязку.</span>
    </div>
  );
}
