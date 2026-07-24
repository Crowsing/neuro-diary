// Екран «Деталі дня» — порт docs/prototype/nd-v2.dc.html:
// шаблон рядки 552–590 (резюме, секції, кнопки Редагувати/Видалити/загострення/місячні),
// логіка _vHist рядки 1471–1510 (dayRows, dayFlareT, обробники).

import { useApp } from '../../../state/store';
import {
  checkinStart,
  dialogOpen,
  mensesRemove,
  navSub
} from '../../../state/actions';
import { fmtLong } from '../../../lib/dates';
import { cycleDay } from '../../../lib/cycle';
import { entryState } from '../../../lib/entry';
import { groupBadges, uniqueIds } from '../../../lib/groups';
import { det, sum } from '../../../lib/summary';
import { makeSymDef } from '../../../lib/utils';
import type { Ctx } from '../../../lib/types';

export default function DayDetails() {
  const { state: st, dispatch } = useApp();
  const starts = st.data.cycleStarts || [];
  const symDef = makeSymDef(st.data.custom);

  // Вибраний день (рядок 1471).
  const sel = st.selDay;
  const se = sel ? st.data.entries[sel] : null;
  const sd = se && se.status === 'done' ? se : null;

  // Рядки резюме дня (рядки 1472–1484).
  const dayRows: Array<{ id: string; k: string; v: string; badges?: string[] }> = [];
  if (sd && sel) {
    // Current tracking controls visibility only. The value still comes solely from
    // this historical entry, so a symptom added later is shown honestly as unknown.
    const symptomIds = uniqueIds([...Object.keys(sd.sym || {}), ...(sd.absent || []), ...st.data.active]);
    symptomIds.forEach((id) => {
      const s = symDef(id);
      if (!s) return;
      const state = entryState(sd, id);
      dayRows.push({
        id: 'sym-' + id,
        k: s.name,
        v: state === 'present'
          ? (s.type === 'scale' && sd.sym[id]?.int == null ? 'симптом був; інтенсивність не заповнено' : det(sd.sym[id], s))
          : state === 'absent'
            ? 'не було (підтверджено)'
            : 'симптом не заповнено цього дня',
        badges: groupBadges(id, st.data)
      });
    });
    if (sd.legacyNoSymptoms) {
      dayRows.push({ id: 'legacy-no-symptoms', k: 'Симптоми', v: 'не було за старим записом; точний перелік відстежуваних симптомів не збережено' });
    } else if (sd.noSymptoms && symptomIds.length === 0) {
      dayRows.push({ id: 'no-symptoms', k: 'Симптоми', v: 'не було (підтверджено)' });
    }
    dayRows.push({ id: 'wellbeing', k: 'Самопочуття', v: sd.wb == null ? 'не заповнено' : sd.wb + '/10' });
    const cx: Partial<Ctx> = sd.ctx || {};
    dayRows.push({ id: 'stress', k: 'Стрес', v: cx.stress == null ? 'не заповнено' : cx.stress + '/5' });
    dayRows.push({
      id: 'sleep',
      k: 'Сон',
      v: (cx.sleepQ == null ? 'якість не заповнено' : 'якість ' + cx.sleepQ + '/5')
        + ' · '
        + (cx.sleepH == null ? 'тривалість не заповнено' : 'тривалість ' + cx.sleepH + ' год')
    });
    dayRows.push({ id: 'activity', k: 'Активність', v: cx.activity == null ? 'не заповнено' : (cx.activity ? (cx.actType || 'так') : 'ні') });
    dayRows.push({ id: 'heat', k: 'Спека / перегрів', v: cx.heat == null ? 'не заповнено' : (cx.heat ? 'так' : 'ні') });
    const cdd = st.data.cycleOn ? cycleDay(sel, starts) : null;
    if (cdd) dayRows.push({ id: 'cycle', k: 'Цикл', v: 'день ' + cdd });
  }

  // Value-обʼєкт (рядки 1492–1510).
  const dayTitle = sel ? fmtLong(sel).replace(/^./, (ch) => ch.toUpperCase()) : '';
  const dayStatusT = se ? (se.status === 'done' ? 'Завершено' : 'Чернетка') : 'Не заповнено';
  const dayStatusBg = se ? (se.status === 'done' ? 'var(--color-accent-2-200)' : 'var(--color-accent-100)') : 'var(--color-neutral-200)';
  const dayStatusFg = se ? (se.status === 'done' ? 'var(--color-accent-2-800)' : 'var(--color-accent-800)') : 'var(--color-neutral-800)';
  const dayHasEntry = !!se;
  const dayNoEntry = !se;
  const dayHasSum = !!sd;
  const daySum = sd && sel ? sum(sd, sel, symDef, st.data.cycleOn, starts) : '';
  const dayHasNote = !!(sd && sd.note);
  const dayNote = (sd && sd.note) || '';
  const dayHasFlare = !!(sd && sd.flare);
  const dayFlareGroups = sd?.flare?.groupIds
    ?.map((id) => st.data.groups.find((group) => group.id === id && !group.archived)?.name)
    .filter((name): name is string => !!name) || [];
  const dayFlareT = sd && sd.flare
    ? [
        sd.flare.isNew ? 'новий або значно сильніший' : '',
        sd.flare.dur24 ? 'триває понад 24 год' : '',
        sd.flare.temp ? 'була температура/перегрів' : '',
        sd.flare.note
      ].filter(Boolean).join('; ') || 'позначено'
    : '';
  const dayFilledLater = !!(sd && sd.filledLater);
  const dayHasMenses = !!(sel && starts.includes(sel));
  const dayEditL = se ? (se.status === 'done' ? 'Редагувати запис' : 'Продовжити чернетку') : 'Заповнити цей день';
  const dayCanFlare = !!(sd && !sd.flare);

  const dayBack = () => dispatch(navSub(null, { selDay: null, tab: 'history' }));
  const dayEdit = () => { if (sel) dispatch(checkinStart(sel, 'day')); };
  const dayFlareBtn = () => {
    if (!sel) return;
    const f = (sd && sd.flare) || { isNew: false, dur24: false, temp: false, note: '' };
    dispatch(dialogOpen({ type: 'flare', iso: sel, f: { ...f }, had: !!(sd && sd.flare) }));
  };
  const dayDelete = () => { if (sel) dispatch(dialogOpen({ type: 'delEntry', iso: sel })); };
  const dayDelMenses = () => { if (sel) dispatch(mensesRemove(sel)); };

  return (
    <div data-screen-label="Деталі дня" style={{ flex: 1, overflowY: 'auto', padding: '6px 20px 20px', display: 'flex', flexDirection: 'column', gap: 13 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <button className="btn btn-icon btn-secondary" aria-label="Назад до історії" onClick={dayBack} style={{ width: 44, height: 44 }}><svg width="16" height="16"><path d="M10 2 L4 8 L10 14" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"></path></svg></button>
        <h2 style={{ fontSize: 20, margin: 0, flex: 1 }}>{dayTitle}</h2>
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <span className="tag" style={{ background: dayStatusBg, color: dayStatusFg, fontSize: 12, padding: '5px 12px' }}>{dayStatusT}</span>
        {dayFilledLater && (<span className="tag tag-neutral" style={{ fontSize: 12, padding: '5px 12px' }}>Заповнено пізніше</span>)}
        {dayHasMenses && (<span className="tag tag-accent" style={{ fontSize: 12, padding: '5px 12px' }}>Початок менструації</span>)}
      </div>
      {dayHasEntry && (
        <>
          {dayHasSum && (
            <div className="card" style={{ gap: 6 }}><span className="card-kicker">Підсумок</span><p style={{ margin: 0, fontSize: 14, lineHeight: 1.5 }}>{daySum}</p></div>
          )}
          <div className="card" style={{ gap: 6 }}>
            {dayRows.map((r) => (
              <div key={r.id} style={{ display: 'flex', gap: 12, justifyContent: 'space-between', borderBottom: '1px solid color-mix(in srgb, var(--color-text) 8%, transparent)', padding: '6px 0' }}>
                <span style={{ fontSize: 13, flex: 'none' }} className="text-muted">{r.k}</span>
                <span style={{ fontSize: '13.5px', textAlign: 'right', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
                  <span>{r.v}</span>
                  {!!r.badges?.length && (
                    <span style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'flex-end', gap: 4 }}>
                      {r.badges.map((badge) => <span key={badge} className="tag tag-neutral" style={{ fontSize: 10, overflowWrap: 'anywhere' }}>{badge}</span>)}
                    </span>
                  )}
                </span>
              </div>
            ))}
            <span style={{ fontSize: 11.5 }} className="text-muted">Групування відповідає поточним налаштуванням.</span>
          </div>
          {dayHasNote && (
            <div className="card" style={{ gap: 6 }}><span className="card-kicker">Нотатка</span><p style={{ margin: 0, fontSize: 14 }}>{dayNote}</p></div>
          )}
          {dayHasFlare && (
            <div className="card" style={{ gap: 6, background: 'var(--color-accent-100)' }}>
              <span className="card-kicker">Важлива зміна симптомів для обговорення з лікарем</span>
              <p style={{ margin: 0, fontSize: '13.5px', color: 'var(--color-accent-800)' }}>{dayFlareT}</p>
              {!!dayFlareGroups.length && <span style={{ fontSize: 12, color: 'var(--color-accent-800)' }}>Організаційні групи: {dayFlareGroups.join(', ')}</span>}
              <span style={{ fontSize: 12, color: 'var(--color-accent-800)' }}>Особиста позначка для розмови з лікарем, не діагноз. Її можна змінити або прибрати.</span>
            </div>
          )}
          <button className="btn btn-primary" style={{ minHeight: 50 }} onClick={dayEdit}>{dayEditL}</button>
          {dayCanFlare && (<button className="btn btn-secondary" style={{ minHeight: 48 }} onClick={dayFlareBtn}>Позначити важливу зміну симптомів</button>)}
          {dayHasFlare && (<button className="btn btn-secondary" style={{ minHeight: 48 }} onClick={dayFlareBtn}>Редагувати позначку важливої зміни</button>)}
          {dayHasMenses && (<button className="btn btn-ghost" style={{ minHeight: 44 }} onClick={dayDelMenses}>Прибрати позначку початку менструації</button>)}
          <button className="btn btn-ghost" style={{ minHeight: 44, color: '#a33d2f' }} onClick={dayDelete}>Видалити запис</button>
        </>
      )}
      {dayNoEntry && (
        <>
          <div className="card" style={{ gap: 8 }}><p style={{ margin: 0, fontSize: 14 }} className="text-muted">Цей день не заповнено. Пропущений день не означає, що симптомів не було — на графіках він показується як прогалина.</p></div>
          <button className="btn btn-primary" style={{ minHeight: 50 }} onClick={dayEdit}>Заповнити цей день</button>
          {dayHasMenses && (<button className="btn btn-ghost" style={{ minHeight: 44 }} onClick={dayDelMenses}>Прибрати позначку початку менструації</button>)}
        </>
      )}
    </div>
  );
}
