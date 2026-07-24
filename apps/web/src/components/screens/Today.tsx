// Екран «Сьогодні» — порт docs/prototype/nd-v2.dc.html:
// шаблон рядки 118–158, view-логіка vToday рядки 1133–1162.
// Обробники → actions за ACTIONS.md: todCta/todAddMoreOn → CHECKIN_START,
// todNoSym → TODAY_NO_SYMPTOMS, todMenses → DIALOG_OPEN menses,
// safetyOpen → NAV_SUB safety.

import { useMemo } from 'react';
import { useApp } from '../../state/store';
import { useNow } from '../../state/clock';
import { checkinStart, dialogOpen, navSub, todayNoSymptoms } from '../../state/actions';
import type { Entry } from '../../lib/types';
import { dOf, fmtLong, isoOff } from '../../lib/dates';
import { cycleDay } from '../../lib/cycle';
import { sum } from '../../lib/summary';
import { makeSymDef } from '../../lib/utils';
import { WD_SHORT } from '../../constants/copy';
import GroupBadges from '../ui/GroupBadges';

/** Стовпчик 7-денного міні-барчарта (vToday, рядки 1137–1144). */
interface WeekBar {
  iso: string;
  l: string;
  bg: string;
  status: string;
  mark: string;
}

export default function Today() {
  const { state: st, dispatch } = useApp();
  const now = useNow();

  // vToday, рядки 1134–1136.
  const t = isoOff(now, 0);
  const e: Entry | undefined = st.data.entries[t];
  const doneEntry = e && e.status === 'done' ? e : null;
  const done = !!doneEntry;
  const draft = (!!e && e.status === 'draft') || (!!st.checkin && st.checkin.date === t);
  const cd = st.data.cycleOn ? cycleDay(t, st.data.cycleStarts) : null;

  const symDef = useMemo(() => makeSymDef(st.data.custom), [st.data.custom]);

  // vToday, рядки 1137–1144.
  const week7 = useMemo<WeekBar[]>(() => {
    const bars: WeekBar[] = [];
    for (let o = -6; o <= 0; o++) {
      const iso = isoOff(now, o);
      const en: Entry | undefined = st.data.entries[iso];
      const d = dOf(iso);
      const status = en?.status === 'done' ? 'завершено' : en?.status === 'draft' ? 'чернетка' : 'не заповнено';
      bars.push({
        iso,
        l: WD_SHORT[(d.getDay() + 6) % 7],
        bg: en?.status === 'done' ? 'var(--color-accent-2-500)' : en?.status === 'draft' ? 'var(--color-accent-500)' : 'var(--color-neutral-400)',
        status,
        mark: en?.status === 'done' ? '✓' : en?.status === 'draft' ? '…' : '—'
      });
    }
    return bars;
  }, [now, st.data.entries]);

  // vToday, рядки 1146–1161.
  const todWD = fmtLong(t).replace(/^./, (c) => c.toUpperCase());
  const todStatusT = done ? 'Завершено' : (draft ? 'Чернетка' : 'Не заповнено');
  const todStatusBg = done ? 'var(--color-accent-2-200)' : (draft ? 'var(--color-accent-100)' : 'var(--color-neutral-200)');
  const todStatusFg = done ? 'var(--color-accent-2-800)' : (draft ? 'var(--color-accent-800)' : 'var(--color-neutral-800)');
  const todHasCycle = !!cd;
  const todCycle = cd ? 'Цикл · день ' + cd : '';
  const todCtaL = done ? 'Редагувати запис' : (draft ? 'Продовжити' : 'Заповнити день');
  const todNoSymShow = !done && !draft && st.data.active.length > 0;
  const todAddMore = !!(doneEntry && doneEntry.noSymptoms);
  const todHasSum = done;
  const todSum = done ? sum(doneEntry, t, symDef, st.data.cycleOn, st.data.cycleStarts) : '';

  return (
    <div data-screen-label="Сьогодні" style={{ flex: 1, overflowY: 'auto', padding: '8px 20px 20px', display: 'flex', flexDirection: 'column', gap: '14px' }}>
      <div>
        <div style={{ fontSize: '12px', letterSpacing: '.08em', textTransform: 'uppercase' }} className="text-muted">Сьогодні</div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: '10px', flexWrap: 'wrap' }}>
          <h2 style={{ fontSize: '27px', margin: 0 }}>{todWD}</h2>
        </div>
        <div style={{ display: 'flex', gap: '8px', marginTop: '8px', flexWrap: 'wrap' }}>
          <span className="tag" style={{ background: todStatusBg, color: todStatusFg, fontSize: '12px', padding: '5px 12px' }}>{todStatusT}</span>
          {todHasCycle && <span className="tag tag-neutral" style={{ fontSize: '12px', padding: '5px 12px' }}>{todCycle}</span>}
        </div>
      </div>
      {todHasSum && (
        <div className="card elev-sm" style={{ gap: '8px' }}>
          <span className="card-kicker">Підсумок дня</span>
          <p style={{ margin: 0, fontSize: '14.5px', lineHeight: 1.5 }}>{todSum}</p>
          <span style={{ fontSize: '12px' }} className="text-muted">Підсумок лише переказує введені факти й змінюється тільки через редагування запису.</span>
        </div>
      )}
      {!!doneEntry && Object.keys(doneEntry.sym).length > 0 && (
        <div className="card" style={{ gap: 10 }}>
          <span className="card-kicker">Записані симптоми</span>
          {[...new Set(Object.keys(doneEntry.sym))].map((id) => (
            <div key={id} className="nd-present-row"><span>{symDef(id)?.name || id}</span><GroupBadges symptomId={id} data={st.data} /></div>
          ))}
        </div>
      )}
      <button className="btn btn-primary" style={{ minHeight: '54px', fontSize: '17px' }} onClick={() => dispatch(checkinStart(t))}>{todCtaL}</button>
      {todNoSymShow && (
        <button className="btn btn-secondary" style={{ minHeight: '48px' }} onClick={() => dispatch(todayNoSymptoms())}>Сьогодні не було жодного з відстежуваних симптомів</button>
      )}
      {todAddMore && (
        <button className="btn btn-secondary" style={{ minHeight: '48px' }} onClick={() => dispatch(checkinStart(t))}>Додати самопочуття або контекст</button>
      )}
      {st.data.cycleOn && <button className="btn btn-secondary" style={{ minHeight: '48px' }} onClick={() => dispatch(dialogOpen({ type: 'menses', sel: 'today', custom: t, dup: false }))}>
        <svg width="18" height="18"><path d="M9 2 C9 2 14 8 14 11.5 A5 5 0 0 1 4 11.5 C4 8 9 2 9 2 Z" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round"></path></svg>
        Почалися місячні</button>}
      <div className="card" style={{ gap: '10px' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}><span className="card-kicker">Останні 7 днів · заповнення</span></div>
        <div style={{ display: 'flex', gap: '7px' }}>
          {week7.map((b) => (
            <div key={b.iso} role="img" aria-label={`${b.l}: ${b.status}`} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '5px' }}><div aria-hidden="true" style={{ width: '100%', borderRadius: '8px', background: b.bg, color: 'var(--color-text)', minHeight: 18, display: 'grid', placeItems: 'center', fontSize: 12, fontWeight: 700 }}>{b.mark}</div><span aria-hidden="true" style={{ fontSize: '10px' }} className="text-muted">{b.l}</span></div>
          ))}
        </div>
        <span style={{ fontSize: '12px' }} className="text-muted">Лише стан заповнення: завершено, чернетка або не заповнено. Без оцінки симптомів.</span>
      </div>
      <button className="btn btn-ghost" style={{ minHeight: '44px', justifyContent: 'flex-start', paddingInline: '4px' }} onClick={() => dispatch(navSub('safety', { safetyMore: false }))}>
        <svg width="17" height="17"><path d="M8.5 2 L16 15 L1 15 Z M8.5 7 L8.5 10.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round"></path><circle cx="8.5" cy="12.8" r="0.9" fill="currentColor"></circle></svg>
        Коли потрібна термінова допомога?</button>
    </div>
  );
}
