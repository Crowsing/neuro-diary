// Екран «Графік симптому» — порт docs/prototype/nd-v2.dc.html:
// шаблон рядки 591–664 (sc-if sSym), логіка _vTrends рядки 1594–1646.
// Перемикач Графік/Таблиця, статистика, під-графіки стресу і сну та повна
// клавіатурна таблична альтернатива. SVG-графік — у SymptomChart.

import type { CSSProperties } from 'react';
import { useApp } from '../../../state/store';
import { useNow } from '../../../state/clock';
import { navSub, trendsSet } from '../../../state/actions';
import { model } from '../../../lib/chart';
import type { ChartDay, ChartDeps } from '../../../lib/chart';
import { groupBadges, trendableSymptomIds } from '../../../lib/groups';
import SubScreenHeader from '../../frame/SubScreenHeader';
import { makeSymDef } from '../../../lib/utils';
import { fmtShort } from '../../../lib/dates';
import { INT } from '../../../constants/symptoms';
import type { Entry, Period, SymType } from '../../../lib/types';
import SymptomChart from './SymptomChart';

const PERIODS: Period[] = [7, 30, 90];

// Стиль чипа цього екрана (шаблон рядки 598, 600): padding 8px 14px, font-size 13px.
function chipStyle(sel: boolean): CSSProperties {
  return {
    minHeight: 44,
    padding: '8px 14px',
    borderRadius: 999,
    font: 'inherit',
    fontSize: '13px',
    cursor: 'pointer',
    border: '1.5px solid ' + (sel ? 'var(--color-accent)' : 'var(--color-divider)'),
    background: sel ? 'var(--color-accent)' : 'var(--color-surface)',
    color: sel ? 'var(--color-bg)' : 'var(--color-text)'
  };
}

function contextLabel(entry: Entry | undefined): string {
  if (!entry || entry.status !== 'done') return '';
  const ctx = entry.ctx;
  return [
    ctx.stress == null ? 'стрес не заповнено' : 'стрес ' + ctx.stress + '/5',
    ctx.sleepQ == null ? 'якість сну не заповнено' : 'якість сну ' + ctx.sleepQ + '/5',
    ctx.sleepH == null ? 'тривалість сну не заповнено' : 'тривалість сну ' + ctx.sleepH + ' год',
    ctx.activity == null ? 'активність не заповнено' : (ctx.activity ? 'активність: ' + (ctx.actType || 'так') : 'активність: ні'),
    ctx.heat == null ? 'спека / перегрів не заповнено' : 'спека / перегрів: ' + (ctx.heat ? 'так' : 'ні')
  ].join(' · ');
}

export default function SymptomDetail() {
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
  const id = tr.sym || trendableSymptomIds(st.data)[0] || '';
  const def: { name: string; type: SymType } = symDef(id) || { name: 'Симптом недоступний', type: 'scale' };
  const m = model(id, per, 308, 150, deps);
  const marks = m.max === 10 ? [2, 4, 6, 8, 10] : (m.max === 1 ? [1] : [1, 2, 3, 4, 5]);
  const present = m.days.filter((d): d is ChartDay & { v: number } => d.v !== null && d.v >= 1);
  const stats: { k: string; v: string }[] = [
    { k: 'Було', v: m.present + '/' + m.known + ' відомих' },
    { k: 'Підтверджено не було', v: m.absent + '/' + m.known },
    { k: 'Не заповнено у завершені дні', v: String(m.unknownCompleted) }
  ];
  if (def.type === 'scale') {
    stats.push({ k: 'Середня у дні зі значенням', v: present.length ? (present.reduce((a, d) => a + d.v, 0) / present.length).toFixed(1) + '/5' : '—' });
    stats.push({ k: 'Максимум', v: present.length ? Math.max(...present.map((d) => d.v)) + '/5' : '—' });
  }
  const syIsChart = tr.mode !== 'table';
  const syIsTable = tr.mode === 'table';
  const syEmpty = m.known < 7;
  const stSegs = model('stress', per, 308, 60, deps).segs;
  const slSegs = model('sleepQ', per, 308, 60, deps).segs;
  const syTable = m.days.slice().reverse().map((d) => ({
    iso: d.iso,
    d: fmtShort(d.iso),
    v: !d.e
      ? 'день не заповнено'
      : d.e.status === 'draft'
        ? 'день має чернетку'
        : d.observation === 'unknown'
          ? 'симптом не заповнено цього дня'
          : d.observation === 'absent'
            ? 'підтверджено: не було'
            : d.v === null
              ? 'симптом був; інтенсивність не заповнено'
              : (def.type === 'scale' ? d.v + '/5 — ' + INT[d.v] : 'був'),
    c: contextLabel(d.e)
  }));
  const badges = groupBadges(id, st.data);
  return (
    <div data-screen-label="Графік симптому" className="nd-screen nd-screen--tight">
      <SubScreenHeader
        title={def.name}
        backLabel="Назад до динаміки"
        onBack={() => dispatch(navSub(null, { tab: 'trends' }))}
      >
        {!!id && !st.data.active.includes(id) && <span className="tag tag-neutral" style={{ fontSize: 10 }}>Історичний</span>}
        {badges.map((badge) => <span key={badge} className="tag tag-neutral" style={{ fontSize: 10, overflowWrap: 'anywhere' }}>{badge}</span>)}
      </SubScreenHeader>
      <div role="group" aria-label="Період і режим перегляду динаміки" style={{ display: 'flex', gap: 7, flexWrap: 'wrap' }}>
        {PERIODS.map((p) => (
          <button key={p} aria-pressed={per === p} onClick={() => dispatch(trendsSet({ period: p }))} style={chipStyle(per === p)}>{p + ' дн.'}</button>
        ))}
        <span style={{ flex: 1 }}></span>
        <button aria-pressed={tr.mode !== 'table'} onClick={() => dispatch(trendsSet({ mode: 'chart' }))} style={chipStyle(tr.mode !== 'table')}>Графік</button>
        <button aria-pressed={tr.mode === 'table'} onClick={() => dispatch(trendsSet({ mode: 'table' }))} style={chipStyle(tr.mode === 'table')}>Таблиця</button>
      </div>
      <span style={{ fontSize: 11.5 }} className="text-muted">Назви груп відповідають поточним налаштуванням і не змінюють цей числовий ряд.</span>
      {syEmpty && (
        <div className="card"><p style={{ margin: 0, fontSize: 14 }} className="text-muted">Менше 7 відомих спостережень для цього симптому. Дані нижче доступні, але їх може бути недостатньо для стійкої закономірності.</p></div>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(125px,1fr))', gap: 9 }}>
        {stats.map((s) => (
          <div key={s.k} className="card" style={{ gap: 2, padding: 12 }}><span style={{ fontSize: 11 }} className="text-muted">{s.k}</span><span style={{ fontSize: 17, fontWeight: 700, fontFamily: 'var(--font-heading)' }}>{s.v}</span></div>
        ))}
      </div>
      <span style={{ fontSize: 11.5 }} className="text-muted">Знаменник — лише відомі спостереження: симптом був або його відсутність явно підтверджено. Невідомі значення рахуються окремо.</span>
      {syIsChart && (
        <>
          <div className="card" style={{ gap: 6 }}>
            <span style={{ fontSize: 12 }} className="text-muted">{m.cover}</span>
            <SymptomChart m={m} marks={marks} />
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '10.5px' }} className="text-muted"><span>{fmtShort(m.days[0].iso)}</span><span>{fmtShort(m.days[m.days.length - 1].iso)}</span></div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, fontSize: 11 }} className="text-muted">
              <span style={{ display: 'flex', gap: 5, alignItems: 'center' }}><span style={{ width: 9, height: 9, borderRadius: '50%', background: 'var(--color-accent)' }}></span>значення</span>
              <span style={{ display: 'flex', gap: 5, alignItems: 'center' }}><span style={{ width: 9, height: 9, borderRadius: '50%', border: '2px solid var(--color-accent)', background: 'var(--color-bg)' }}></span>підтверджено: не було (0)</span>
              <span style={{ display: 'flex', gap: 5, alignItems: 'center' }}><span style={{ width: 9, height: 9, borderRadius: '50%', background: 'var(--color-accent-400)' }}></span>спека</span>
              <span style={{ display: 'flex', gap: 5, alignItems: 'center' }}><span style={{ width: 9, height: 9, borderRadius: '50%', border: '2px solid var(--color-accent-700)' }}></span>менструація</span>
              <span style={{ display: 'flex', gap: 5, alignItems: 'center' }}><span style={{ width: 0, height: 0, borderLeft: '5px solid transparent', borderRight: '5px solid transparent', borderBottom: '9px solid var(--color-accent-600)' }}></span>важлива зміна симптомів</span>
            </div>
            <span style={{ fontSize: 11 }} className="text-muted">Графік є візуальним оглядом. Прогалина означає, що числового значення для графіка немає: день пропущено, симптом не заповнено або симптом був, але інтенсивність не заповнено. Щоб відкрити день із клавіатури, перейдіть у режим «Таблиця».</span>
          </div>
          <div className="card" style={{ gap: 6 }}>
            <span className="card-kicker">Порівняння · спільна вісь часу</span>
            <span style={{ fontSize: 12 }} className="text-muted">Стрес (1–5)</span>
            <svg aria-hidden="true" focusable="false" viewBox="0 0 308 60" style={{ display: 'block', width: '100%', height: 'auto' }}>{stSegs.map((s, i) => <path key={i} d={s.d} fill="none" stroke="var(--color-accent-2-600)" strokeWidth="2" strokeLinecap="round" />)}</svg>
            <span style={{ fontSize: 12 }} className="text-muted">Якість сну (1–5)</span>
            <svg aria-hidden="true" focusable="false" viewBox="0 0 308 60" style={{ display: 'block', width: '100%', height: 'auto' }}>{slSegs.map((s, i) => <path key={i} d={s.d} fill="none" stroke="var(--color-neutral-600)" strokeWidth="2" strokeLinecap="round" />)}</svg>
          </div>
          <span style={{ fontSize: '11.5px' }} className="text-muted">Паралельні графіки — це спостереження, а не доказ причинно-наслідкового звʼязку.</span>
        </>
      )}
      {syIsTable && (
        <div className="card" style={{ gap: 2 }}>
          <span style={{ fontSize: 12, marginBottom: 6 }} className="text-muted">{m.cover + ' · усі ' + per + ' днів · основний доступний спосіб відкрити запис дня'}</span>
          {syTable.map((r) => (
            <button
              key={r.iso}
              aria-label={r.d + ': ' + r.v + '. Відкрити запис дня'}
              onClick={() => dispatch(navSub('day', { selDay: r.iso, tab: 'history' }))}
              style={{ display: 'flex', flexWrap: 'wrap', gap: 10, border: 'none', background: 'transparent', cursor: 'pointer', textAlign: 'left', font: 'inherit', width: '100%', borderBottom: '1px solid color-mix(in srgb, var(--color-text) 8%, transparent)', padding: '8px 0', fontSize: 13, alignItems: 'baseline', minHeight: 44 }}
            >
              <span style={{ flex: 'none', width: 52, fontWeight: 600 }}>{r.d}</span>
              <span style={{ flex: '1 1 150px' }}>{r.v}</span>
              <span className="text-muted" style={{ flex: '2 1 180px', fontSize: 12 }}>{r.c || 'контекст дня не заповнено'}</span>
              <svg aria-hidden="true" focusable="false" width="12" height="12" style={{ flex: 'none' }}><path d="M4 2 L9 6 L4 10" fill="none" stroke="var(--color-accent)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
