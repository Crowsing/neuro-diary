// Екран «Звіт для лікаря» — порт docs/prototype/nd-v2.dc.html:
// шаблон рядки 225–317, view-логіка _vReport рядки 1648–1719.
// Обробники → actions за ACTIONS.md: setR → REPORT_SET (toggle-и обчислює екран),
// pdfCreate → DIALOG_OPEN {type:'pdf'}.

import { useMemo } from 'react';
import { useApp } from '../../../state/store';
import { useNow } from '../../../state/clock';
import { dialogOpen, reportSet } from '../../../state/actions';
import type { Period, ReportState } from '../../../lib/types';
import { isoOff } from '../../../lib/dates';
import {
  UNGROUPED_ID,
  groupBadges,
  reportableSymptomIds,
  visibleGroups
} from '../../../lib/groups';
import { periodLabel } from '../../../lib/report';
import { makeSymDef, toggle } from '../../../lib/utils';
import Chip from '../../ui/Chip';
import Toggle from '../../ui/Toggle';
import PdfPreview from './PdfPreview';

/** Періоди чипів кроку 1 (repPeriodChips, рядок 1690). */
const PERIODS: Period[] = [7, 30, 90];

export default function ReportTab() {
  const { state: st, dispatch } = useApp();
  const now = useNow();
  const r = st.report;
  const per = r.period || 30;

  const symDef = useMemo(() => makeSymDef(st.data.custom), [st.data.custom]);
  const groups = visibleGroups(st.data);
  const available = reportableSymptomIds(st.data, per, now, r.groupIds);
  const active = new Set(st.data.active);
  const currentSyms = available.filter((id) => active.has(id));
  const archivedSyms = available.filter((id) => !active.has(id));

  // setR (рядок 1685).
  const setR = (patch: Partial<ReportState>) => dispatch(reportSet(patch));

  // repTgRows (рядки 1694–1699).
  const repTgRows = [
    { k: 'Контекстні фактори', d: 'Стрес, сон, активність, спека', sel: r.ctx, on: () => setR({ ctx: !r.ctx }) },
    { k: 'Важливі зміни симптомів', d: 'Ваші ручні позначки для обговорення з лікарем', sel: r.flares, on: () => setR({ flares: !r.flares }) },
    { k: 'День циклу', d: 'Вимкнено за замовчуванням', sel: r.cycle, on: () => setR({ cycle: !r.cycle }) },
    { k: 'Вільні нотатки', d: 'Вимкнено за замовчуванням', sel: r.notes, on: () => setR({ notes: !r.notes }) },
    { k: 'Показувати назви груп', d: 'Назви груп потраплять у PDF лише після ввімкнення', sel: r.includeGroupNames, on: () => setR({ includeGroupNames: !r.includeGroupNames }) }
  ];

  const symptomChip = (sid: string) => {
    const symptom = symDef(sid);
    const badges = groupBadges(sid, st.data);
    const groupsLabel = badges.length ? badges.join(', ') : 'Без групи';
    return (
      <Chip
        key={sid}
        label={(symptom ? symptom.name : sid) + ' · ' + groupsLabel}
        selected={r.syms.includes(sid)}
        onClick={() => setR({ syms: toggle(r.syms, sid) })}
        style={{ padding: '8px 14px', fontSize: 13, whiteSpace: 'normal', textAlign: 'left' }}
      />
    );
  };

  return (
    <div data-screen-label="Звіт" style={{ flex: 1, overflowY: 'auto', padding: '8px 20px 20px', display: 'flex', flexDirection: 'column', gap: 13 }}>
      <h2 style={{ fontSize: 24, margin: 0 }}>Звіт для лікаря</h2>
      <div style={{ fontSize: '12.5px' }} className="text-muted">{'Крок ' + r.step + ' із 3 · період → дані → попередній перегляд PDF'}</div>
      <div className="card" style={{ gap: 6, fontSize: 13 }}>
        <span style={{ fontWeight: 700 }}>Групи спостереження — лише спосіб упорядкування</span>
        <span className="text-muted">Групи допомагають упорядкувати записи. Вони не означають, що симптом спричинений певним станом, і не підтверджують діагноз.</span>
        <span className="text-muted">Назви груп є чутливими даними про здоровʼя. У PDF вони приховані за замовчуванням.</span>
      </div>
      {r.step === 1 && (
        <>
          <div className="card" style={{ gap: 10 }}>
            <span className="card-kicker">Крок 1 · Період</span>
            <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap' }}>
              {PERIODS.map((p) => (
                <Chip key={p} label={p + ' днів'} selected={per === p} onClick={() => setR({ period: p })} />
              ))}
            </div>
            <span style={{ fontSize: '12.5px' }} className="text-muted">{periodLabel(per, now)}</span>
          </div>
          <button className="btn btn-primary" style={{ minHeight: 50 }} onClick={() => setR({ step: 2 })}>Далі: вибір даних</button>
        </>
      )}
      {r.step === 2 && (
        <>
          <div className="card" style={{ gap: 10 }}>
            <span className="card-kicker">Крок 2 · Опційний фільтр за групами</span>
            <span style={{ fontSize: 13 }} className="text-muted">Фільтр лише звужує список симптомів. Він не змінює записи й не створює підсумків за станами.</span>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
              <Chip label="Усі групи" selected={r.groupIds.length === 0} onClick={() => setR({ groupIds: [] })} />
              {groups.map((group) => (
                <Chip
                  key={group.id}
                  label={group.name}
                  selected={r.groupIds.includes(group.id)}
                  onClick={() => setR({ groupIds: toggle(r.groupIds, group.id) })}
                  style={{ whiteSpace: 'normal', overflowWrap: 'anywhere' }}
                />
              ))}
              <Chip
                label="Без групи"
                selected={r.groupIds.includes(UNGROUPED_ID)}
                onClick={() => setR({ groupIds: toggle(r.groupIds, UNGROUPED_ID) })}
              />
            </div>
          </div>
          <div className="card" style={{ gap: 10 }}>
            <span className="card-kicker">Симптоми у звіті · виберіть явно</span>
            {currentSyms.length > 0 && (
              <>
                <span style={{ fontSize: 13, fontWeight: 700 }}>Поточний список</span>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>{currentSyms.map(symptomChip)}</div>
              </>
            )}
            {archivedSyms.length > 0 && (
              <>
                <span style={{ fontSize: 13, fontWeight: 700 }}>Архівні з відомими спостереженнями за період</span>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>{archivedSyms.map(symptomChip)}</div>
              </>
            )}
            {available.length === 0 && <span style={{ fontSize: 13 }} className="text-muted">За цим фільтром доступних симптомів немає.</span>}
            <span style={{ fontSize: 12.5 }} className="text-muted">Спільний для кількох груп симптом зберігається й потрапляє у звіт один раз.</span>
          </div>
          <div className="card" style={{ gap: 12 }}>
            <span className="card-kicker">Що ще включити</span>
            {repTgRows.map((t) => (
              <div key={t.k} style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                <Toggle selected={t.sel} onClick={t.on} aria={t.k} />
                <span style={{ display: 'flex', flexDirection: 'column', gap: 1 }}><span style={{ fontSize: 14, fontWeight: 600 }}>{t.k}</span><span style={{ fontSize: 12 }} className="text-muted">{t.d}</span></span>
              </div>
            ))}
            {r.notes && r.groupIds.length > 0 && (
              <span style={{ fontSize: 12.5 }} className="text-muted">Нотатки є даними всього дня. Будуть включені всі нотатки вибраного періоду, а не лише нотатки біля симптомів із вибраних груп.</span>
            )}
          </div>
          <div className="card" style={{ gap: 10 }}>
            <span className="card-kicker">Ідентифікаційні дані · опційно</span>
            <div className="field"><label htmlFor="nd-rep-name">Імʼя</label><input id="nd-rep-name" className="input" value={r.name || ''} onChange={(e) => setR({ name: e.target.value })} placeholder="Не вказувати" style={{ minHeight: 44 }} /></div>
            <div className="field"><label htmlFor="nd-rep-dob">Дата народження</label><input id="nd-rep-dob" type="date" max={isoOff(now, 0)} className="input" value={r.dob || ''} onChange={(e) => setR({ dob: e.target.value })} style={{ minHeight: 44 }} /></div>
            <span style={{ fontSize: 12.5 }} className="text-muted">Імʼя та дата народження є чутливими даними. Вони не зберігаються між сесіями, але, якщо введені, потраплять у поточний PDF і JSON-експорт.</span>
          </div>
          <div style={{ display: 'flex', gap: 10 }}>
            <button className="btn btn-secondary" style={{ minHeight: 50 }} onClick={() => setR({ step: 1 })}>Назад</button>
            <button className="btn btn-primary" disabled={r.syms.length === 0} style={{ flex: 1, minHeight: 50 }} onClick={() => setR({ step: 3 })}>Переглянути PDF</button>
          </div>
          {r.syms.length === 0 && <span style={{ fontSize: 12.5 }} className="text-muted">Виберіть щонайменше один симптом для звіту.</span>}
        </>
      )}
      {r.step === 3 && (
        <>
          <PdfPreview />
          <button className="btn btn-primary" style={{ minHeight: 52, fontSize: 16 }} onClick={() => dispatch(dialogOpen({ type: 'pdf' }))}>Експортувати звіт</button>
          <button className="btn btn-secondary" style={{ minHeight: 48 }} onClick={() => setR({ step: 2 })}>Назад до налаштувань</button>
          <span style={{ fontSize: 12 }} className="text-muted">Звіт читабельний у відтінках сірого. Друк браузера дозволяє зберегти його як PDF; застосунок нічого не надсилає.</span>
        </>
      )}
    </div>
  );
}
