// Екран «Історія» — порт docs/prototype/nd-v2.dc.html:
// шаблон рядки 160–207 (місячний календар 7×N, легенда, фільтри, список записів),
// логіка _vHist рядки 1429–1491 (calendar cells, FL, histList, histEmpty).

import { useApp } from '../../../state/store';
import { useNow } from '../../../state/clock';
import {
  histCalPrev,
  histCalNext,
  histFilter as setHistFilter,
  historyGroupSet,
  navSub
} from '../../../state/actions';
import { MON, MONN, fmtLong, isoOff, p2 } from '../../../lib/dates';
import { filterByGroup, UNGROUPED_ID, uniqueIds, visibleGroups } from '../../../lib/groups';
import { sum } from '../../../lib/summary';
import { makeSymDef } from '../../../lib/utils';
import { HIST_FILTER_LABELS } from '../../../constants/copy';
import type { HistFilter } from '../../../lib/types';
import Chip from '../../ui/Chip';
import ScreenHeader from '../../frame/ScreenHeader';

/** Клітинка календаря (_vHist, рядки 1436–1452). */
interface Cell {
  txt: string;
  bg: string;
  bd: string;
  fg: string;
  on?: () => void;
  interactive: boolean;
  cur: string;
  aria: string;
  mNote: boolean;
  mMenses: boolean;
  mFlare: boolean;
}

/** Порожня клітинка до/після днів місяця (рядки 1436, 1452). */
function emptyCell(): Cell {
  return {
    txt: '', bg: 'transparent', bd: '1.5px solid transparent', fg: 'inherit',
    interactive: false, cur: 'default', aria: '', mNote: false, mMenses: false, mFlare: false
  };
}

/** Порядок фільтрів = порядок ключів FL (рядок 1454); підписи — з copy.ts. */
const FL = Object.keys(HIST_FILTER_LABELS) as HistFilter[];

export default function HistoryTab() {
  const { state: st, dispatch } = useApp();
  const now = useNow();
  const E = st.data.entries;
  const starts = st.data.cycleStarts || [];
  const symDef = makeSymDef(st.data.custom);
  const groups = visibleGroups(st.data);

  // Місяць календаря (рядки 1431–1434).
  const base = new Date(now);
  base.setDate(1);
  base.setMonth(base.getMonth() + st.histShift);
  const y = base.getFullYear(), m = base.getMonth();
  const first = (new Date(y, m, 1).getDay() + 6) % 7;
  const dim = new Date(y, m + 1, 0).getDate();
  const today = isoOff(now, 0);

  // Клітинки (рядки 1435–1452).
  const cells: Cell[] = [];
  for (let i = 0; i < first; i++) cells.push(emptyCell());
  for (let dd = 1; dd <= dim; dd++) {
    const iso = y + '-' + p2(m + 1) + '-' + p2(dd);
    const e = E[iso]; const fut = iso > today;
    const done = !!e && e.status === 'done', draft = !!e && e.status === 'draft';
    cells.push({
      txt: String(dd),
      bg: done ? 'var(--color-accent-2-200)' : (draft ? 'var(--color-accent-100)' : 'transparent'),
      bd: iso === today ? '2px solid var(--color-accent)' : (draft ? '1.5px dashed var(--color-accent-600)' : '1.5px solid transparent'),
      fg: fut ? 'color-mix(in srgb, var(--color-text) 30%, transparent)' : (done ? 'var(--color-accent-2-800)' : 'inherit'),
      on: fut ? undefined : () => dispatch(navSub('day', { selDay: iso })),
      interactive: !fut,
      cur: fut ? 'default' : 'pointer',
      aria: dd + ' ' + MON[m] + (done ? ', завершено' : (draft ? ', чернетка' : ', не заповнено')),
      mNote: !!(e && ((e.status === 'done' && e.note) || (e.status === 'draft' && e.d && e.d.note))),
      mMenses: starts.includes(iso),
      mFlare: !!(e && e.status === 'done' && e.flare)
    });
  }
  while (cells.length % 7) cells.push(emptyCell());
  const rows: Array<{ cells: Cell[] }> = [];
  for (let i = 0; i < cells.length; i += 7) rows.push({ cells: cells.slice(i, i + 7) });

  // Список останніх записів (рядки 1455–1470).
  const list = Object.keys(E).sort().reverse().filter((iso) => iso <= today).filter((iso) => {
    const e = E[iso], f = st.histFilter;
    const symptomIds = e.status === 'done'
      ? uniqueIds([...Object.keys(e.sym || {}), ...(e.absent || [])])
      : uniqueIds([...(e.d.sel || []), ...(e.d.absent || [])]);
    if (st.historyGroupId && !filterByGroup(symptomIds, st.historyGroupId, st.data).length) return false;
    if (f === 'flare') return e.status === 'done' && !!e.flare;
    if (f === 'menses') return starts.includes(iso);
    if (f === 'notes') return e.status === 'done' && !!e.note;
    if (f === 'draft') return e.status === 'draft';
    return true;
  }).slice(0, 40).map((iso) => {
    const e = E[iso], done = e.status === 'done';
    return {
      iso,
      dateL: fmtLong(iso).replace(/^./, (ch) => ch.toUpperCase()),
      statusT: done ? 'Завершено' : 'Чернетка',
      sBg: done ? 'var(--color-accent-2-200)' : 'var(--color-accent-100)',
      sFg: done ? 'var(--color-accent-2-800)' : 'var(--color-accent-800)',
      sum: done ? sum(e, iso, symDef, st.data.cycleOn, starts) : 'Незавершений запис — можна продовжити з того самого місця.',
      on: () => dispatch(navSub('day', { selDay: iso }))
    };
  });
  const histEmpty = !list.length;

  return (
    <div data-screen-label="Історія" className="nd-screen">
      <ScreenHeader title="Історія" />
      <div className="card" style={{ gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <button className="btn btn-icon btn-secondary" aria-label="Попередній місяць" onClick={() => dispatch(histCalPrev())} style={{ width: 44, height: 44 }}><svg width="14" height="14"><path d="M9 2 L4 7 L9 12" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"></path></svg></button>
          <span style={{ fontFamily: 'var(--font-heading)', fontSize: 17 }}>{MONN[m] + ' ' + y}</span>
          <button className="btn btn-icon btn-secondary" aria-label="Наступний місяць" disabled={st.histShift === 0} onClick={() => dispatch(histCalNext())} style={{ width: 44, height: 44, cursor: st.histShift === 0 ? 'default' : 'pointer' }}><svg width="14" height="14"><path d="M5 2 L10 7 L5 12" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"></path></svg></button>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7,1fr)', gap: 3, fontSize: '10.5px', textAlign: 'center' }} className="text-muted"><span>Пн</span><span>Вт</span><span>Ср</span><span>Чт</span><span>Пт</span><span>Сб</span><span>Нд</span></div>
        {rows.map((w, wi) => (
          <div key={wi} style={{ display: 'grid', gridTemplateColumns: 'repeat(7,1fr)', gap: 3 }}>
            {w.cells.map((c, ci) => (
              c.interactive ? (
                <button key={ci} onClick={c.on} aria-label={c.aria} style={{ minHeight: 46, borderRadius: 13, cursor: c.cur, font: 'inherit', fontSize: '13.5px', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 2, border: c.bd, background: c.bg, color: c.fg, padding: '2px 0' }}>
                  <span style={{ fontWeight: 600 }}>{c.txt}</span>
                  <span aria-hidden="true" style={{ display: 'flex', gap: '2.5px', alignItems: 'center', height: 7 }}>
                    {c.mNote && (<span style={{ width: 5, height: 5, borderRadius: '1.5px', background: 'var(--color-neutral-600)' }}></span>)}
                    {c.mMenses && (<span style={{ width: 6, height: 6, borderRadius: '50%', border: '1.6px solid var(--color-accent-700)' }}></span>)}
                    {c.mFlare && (<span style={{ width: 0, height: 0, borderLeft: '3.5px solid transparent', borderRight: '3.5px solid transparent', borderBottom: '6px solid var(--color-accent-600)' }}></span>)}
                  </span>
                </button>
              ) : (
                <span key={ci} aria-hidden="true" style={{ minHeight: 46, borderRadius: 13, cursor: c.cur, fontSize: '13.5px', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 2, border: c.bd, background: c.bg, color: c.fg, padding: '2px 0' }}>
                  <span style={{ fontWeight: 600 }}>{c.txt}</span>
                  <span style={{ height: 7 }}></span>
                </span>
              )
            ))}
          </div>
        ))}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, fontSize: 11 }} className="text-muted">
          <span style={{ display: 'flex', gap: 5, alignItems: 'center' }}><span style={{ width: 11, height: 11, borderRadius: 4, background: 'var(--color-accent-2-200)' }}></span>завершено</span>
          <span style={{ display: 'flex', gap: 5, alignItems: 'center' }}><span style={{ width: 11, height: 11, borderRadius: 4, background: 'var(--color-accent-100)', border: '1.5px dashed var(--color-accent-600)' }}></span>чернетка</span>
          <span style={{ display: 'flex', gap: 5, alignItems: 'center' }}><span style={{ width: 5, height: 5, borderRadius: '1.5px', background: 'var(--color-neutral-600)' }}></span>нотатка</span>
          <span style={{ display: 'flex', gap: 5, alignItems: 'center' }}><span style={{ width: 7, height: 7, borderRadius: '50%', border: '1.6px solid var(--color-accent-700)' }}></span>менструація</span>
          <span style={{ display: 'flex', gap: 5, alignItems: 'center' }}><span style={{ width: 0, height: 0, borderLeft: '4px solid transparent', borderRight: '4px solid transparent', borderBottom: '7px solid var(--color-accent-600)' }}></span>важлива зміна симптомів</span>
        </div>
      </div>
      <div role="group" aria-label="Фільтр історії за групою спостереження" style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
        <span style={{ fontSize: 12, fontWeight: 700 }}>Група спостереження</span>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
          <Chip label="Усі групи" selected={st.historyGroupId === null} onClick={() => dispatch(historyGroupSet(null))} style={{ padding: '8px 14px', fontSize: 13 }} />
          {groups.map((group) => (
            <Chip key={group.id} label={group.name} selected={st.historyGroupId === group.id} onClick={() => dispatch(historyGroupSet(group.id))} style={{ padding: '8px 14px', fontSize: 13, maxWidth: '100%', overflowWrap: 'anywhere' }} />
          ))}
          <Chip label="Без групи" selected={st.historyGroupId === UNGROUPED_ID} onClick={() => dispatch(historyGroupSet(UNGROUPED_ID))} style={{ padding: '8px 14px', fontSize: 13 }} />
        </div>
        <span style={{ fontSize: 12 }} className="text-muted">Фільтр застосовується лише до списку й включає день, якщо симптом із групи був вибраний або його відсутність явно підтверджено; записи лише про самопочуття чи невідомі значення не включаються. Використовується поточне групування, а календар і самі записи не змінюються.</span>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
        {FL.map((k) => (
          <Chip
            key={k}
            label={k === 'flare' ? 'Важлива зміна' : HIST_FILTER_LABELS[k]}
            selected={st.histFilter === k}
            onClick={() => dispatch(setHistFilter(k))}
            style={{ padding: '8px 14px', fontSize: 13 }}
          />
        ))}
      </div>
      {histEmpty && (
        <div className="card"><p style={{ margin: 0, fontSize: 14 }} className="text-muted">За цим фільтром записів немає.</p></div>
      )}
      {list.map((h) => (
        <button key={h.iso} onClick={h.on} className="card" style={{ textAlign: 'left', cursor: 'pointer', border: 'none', font: 'inherit', gap: 5, width: '100%' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'baseline' }}><span style={{ fontWeight: 700, fontSize: 14 }}>{h.dateL}</span><span className="tag" style={{ background: h.sBg, color: h.sFg, fontSize: 11, padding: '3px 10px', flex: 'none' }}>{h.statusT}</span></div>
          <span style={{ fontSize: 13, lineHeight: 1.45 }} className="text-muted">{h.sum}</span>
        </button>
      ))}
    </div>
  );
}
