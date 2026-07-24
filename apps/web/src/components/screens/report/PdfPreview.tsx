import { useMemo } from 'react';
import { useApp } from '../../../state/store';
import { useNow } from '../../../state/clock';
import type { ChartDeps } from '../../../lib/chart';
import {
  groupBadges,
  normalizeReportSelection,
  reportableSymptomIds
} from '../../../lib/groups';
import { makeSymDef } from '../../../lib/utils';
import {
  TOP_NO_DATA_ROW,
  TOP_NO_HIGH_ROW,
  buildRows,
  buildSums,
  buildTop,
  ctxSummary,
  cycleLine,
  cycleStartsInPeriod,
  filledEntries,
  flaresInPeriod,
  generatedLabel,
  notesInPeriod,
  periodLabel
} from '../../../lib/report';

const GROUP_DISCLAIMER = 'Групи допомагають упорядкувати записи. Вони не означають, що симптом спричинений певним станом, і не підтверджують діагноз.';

export default function PdfPreview() {
  const { state: st } = useApp();
  const now = useNow();
  const r = st.report;
  const per = r.period || 30;
  const symDef = useMemo(() => makeSymDef(st.data.custom), [st.data.custom]);
  const available = useMemo(
    () => reportableSymptomIds(st.data, per, now, r.groupIds),
    [st.data, per, now, r.groupIds]
  );
  const syms = useMemo(() => normalizeReportSelection(r.syms, available), [r.syms, available]);
  const deps = useMemo<ChartDeps>(
    () => ({ now, entries: st.data.entries, cycleStarts: st.data.cycleStarts, symDef }),
    [now, st.data.entries, st.data.cycleStarts, symDef]
  );
  const rows = useMemo(
    () => buildRows(per, syms, st.data.entries, symDef, now),
    [per, syms, st.data.entries, symDef, now]
  );
  const sums = useMemo(() => buildSums(per, syms, deps), [per, syms, deps]);
  const top = useMemo(
    () => buildTop(per, syms, st.data.entries, symDef, now),
    [per, syms, st.data.entries, symDef, now]
  );
  const filledE = useMemo(() => filledEntries(per, st.data.entries, now), [per, st.data.entries, now]);
  const flares = useMemo(() => flaresInPeriod(per, st.data.entries, now), [per, st.data.entries, now]);
  const notes = useMemo(() => notesInPeriod(per, st.data.entries, now), [per, st.data.entries, now]);
  const cStarts = useMemo(
    () => cycleStartsInPeriod(per, st.data.cycleStarts || [], now),
    [per, st.data.cycleStarts, now]
  );
  const groupLabels = (symptomId: string) => {
    const names = groupBadges(symptomId, st.data);
    return names.length ? names : ['Без групи'];
  };
  const flareGroupLabels = (ids: string[]) => st.data.groups
    .filter((group) => !group.archived && ids.includes(group.id))
    .map((group) => group.name);
  const pdfHasName = !!(r.name || r.dob);
  const pdfNameT = [r.name, r.dob ? 'нар. ' + r.dob : ''].filter(Boolean).join(' · ');
  const pdfTop = top.rows.length ? top.rows : [top.hasIntensityData ? TOP_NO_HIGH_ROW : TOP_NO_DATA_ROW];

  return (
    <div
      className="nd-print-report"
      style={{
        background: '#ffffff', color: '#1c1c1c', borderRadius: 14, padding: '18px 16px',
        display: 'flex', flexDirection: 'column', gap: 14, boxShadow: 'var(--shadow-md)',
        fontSize: 14, lineHeight: 1.5, minWidth: 0, overflowWrap: 'anywhere'
      }}
    >
      <header className="nd-report-section nd-report-header">
        <div style={{ fontSize: 18, fontWeight: 700 }}>Неврологічний щоденник — звіт за період</div>
        <div style={{ color: '#555' }}>{periodLabel(per, now)}<br />{generatedLabel(now)} · {'завершено ' + filledE.length + ' із ' + per + ' днів'}</div>
        {pdfHasName && <div style={{ color: '#333', marginTop: 4 }}>{pdfNameT}</div>}
      </header>

      <div className="nd-report-section" style={{ border: '1px solid #bbb', borderRadius: 8, padding: '10px 12px', color: '#333' }}>
        Інтенсивність: 1–5. «Підтверджено не було» — явна відсутність конкретного симптому. «Не заповнено у завершені дні» — запис дня є, але значення цього симптому невідоме. Пропущений день рахується окремо.
      </div>

      <section className="nd-report-section">
        <div style={{ fontWeight: 700, marginBottom: 5 }}>Короткий підсумок</div>
        {sums.length > 0
          ? sums.map((summary) => (
              <div key={summary.id} style={{ color: '#333', marginBottom: 4 }}>
                · {summary.t}
                {r.includeGroupNames && <span style={{ color: '#555' }}> · групи: {groupLabels(summary.id).join(', ')}</span>}
              </div>
            ))
          : <div style={{ color: '#555' }}>Жодного доступного симптому не вибрано.</div>}
      </section>

      <section className="nd-report-section">
        <div style={{ fontWeight: 700, marginBottom: 5 }}>Дати з найбільшою інтенсивністю</div>
        {pdfTop.map((item, index) => <div key={index} style={{ color: '#333' }}>· {item.d} — {item.t}</div>)}
      </section>

      <section className="nd-report-section nd-report-records">
        <div style={{ fontWeight: 700, marginBottom: 7 }}>Записи симптомів</div>
        {rows.length === 0 && <div style={{ color: '#555' }}>Записів про наявні вибрані симптоми немає. Явно підтверджену відсутність показано у підсумку вище.</div>}

        <div className="nd-report-screen-cards" style={{ display: 'grid', gap: 8 }}>
          {rows.map((row) => (
            <article
              key={row.iso + ':' + row.sid}
              className="nd-report-row-card"
              style={{ border: '1px solid #ccc', borderRadius: 8, padding: '10px 11px', fontSize: 14, breakInside: 'avoid' }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'baseline', flexWrap: 'wrap' }}>
                <strong>{row.s}</strong><span style={{ color: '#555', whiteSpace: 'nowrap' }}>{row.d}</span>
              </div>
              {r.includeGroupNames && <div style={{ color: '#555', marginTop: 3 }}>Групи: {groupLabels(row.sid).join(', ')}</div>}
              <div style={{ marginTop: 5 }}><strong>Інтенсивність:</strong> {row.i}</div>
              <div><strong>Локалізація:</strong> {row.l}</div>
              <div><strong>Коментар:</strong> {row.c}</div>
            </article>
          ))}
        </div>

        <table
          className="nd-report-print-table"
          style={{ display: 'none', width: '100%', borderCollapse: 'collapse', fontSize: 11 }}
        >
          <thead>
            <tr>
              {['Дата', 'Симптом', 'Інт.', 'Локалізація', 'Коментар', ...(r.includeGroupNames ? ['Групи'] : [])].map((heading) => (
                <th key={heading} style={{ textAlign: 'left', borderBottom: '1px solid #888', padding: '4px' }}>{heading}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.iso + ':' + row.sid} className="nd-report-print-row">
                <td style={{ borderBottom: '1px solid #ddd', padding: '4px', whiteSpace: 'nowrap' }}>{row.d}</td>
                <td style={{ borderBottom: '1px solid #ddd', padding: '4px' }}>{row.s}</td>
                <td style={{ borderBottom: '1px solid #ddd', padding: '4px' }}>{row.i}</td>
                <td style={{ borderBottom: '1px solid #ddd', padding: '4px' }}>{row.l}</td>
                <td style={{ borderBottom: '1px solid #ddd', padding: '4px' }}>{row.c}</td>
                {r.includeGroupNames && <td style={{ borderBottom: '1px solid #ddd', padding: '4px' }}>{groupLabels(row.sid).join(', ')}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="nd-report-section">
        <div style={{ fontWeight: 700, marginBottom: 5 }}>Контекст дня</div>
        <div style={{ color: '#333' }}>{r.ctx ? ctxSummary(filledE) : 'Не включено у звіт'}</div>
      </section>

      {r.flares && flares.length > 0 && (
        <section className="nd-report-section">
          <div style={{ fontWeight: 700, marginBottom: 5 }}>Важливі зміни симптомів для обговорення з лікарем</div>
          {flares.map((flare, index) => {
            const labels = flareGroupLabels(flare.groupIds);
            return (
              <div key={index} style={{ color: '#333', marginBottom: 3 }}>
                · {flare.d} — {flare.t}
                {r.includeGroupNames && labels.length > 0 && <span style={{ color: '#555' }}> · групи: {labels.join(', ')}</span>}
              </div>
            );
          })}
          <div style={{ color: '#666', fontSize: 12.5 }}>Позначки зроблені користувачкою вручну й не є медичним висновком.</div>
        </section>
      )}

      {r.cycle && (
        <section className="nd-report-section">
          <div style={{ fontWeight: 700, marginBottom: 5 }}>Цикл · дані всього дня</div>
          <div style={{ color: '#333' }}>{cycleLine(cStarts)}</div>
        </section>
      )}

      {r.notes && notes.length > 0 && (
        <section className="nd-report-section">
          <div style={{ fontWeight: 700, marginBottom: 5 }}>Нотатки · дані всього дня</div>
          {r.groupIds.length > 0 && <div style={{ color: '#666', marginBottom: 4 }}>Включено всі нотатки вибраного періоду; фільтр груп звужує лише список симптомів.</div>}
          {notes.map((note, index) => <div key={index} style={{ color: '#333' }}>· {note.d} — {note.t}</div>)}
        </section>
      )}

      {r.includeGroupNames && (
        <div className="nd-report-section" style={{ borderTop: '1px solid #bbb', paddingTop: 8, color: '#555' }}>{GROUP_DISCLAIMER}</div>
      )}
      <footer className="nd-report-section" style={{ borderTop: '1px solid #bbb', paddingTop: 8, color: '#555' }}>
        Звіт відображає самоспостереження користувачки та не є медичним висновком. Контекст, цикл і нотатки залишаються даними всього дня.
        <div style={{ color: '#666', marginTop: 4 }}>Конфіденційні медичні дані. Після експорту файл більше не контролюється застосунком.</div>
      </footer>
    </div>
  );
}
