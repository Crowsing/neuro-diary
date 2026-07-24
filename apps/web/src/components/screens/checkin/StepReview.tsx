import { useApp } from '../../../state/store';
import { useNow } from '../../../state/clock';
import { checkinPatch, navSub } from '../../../state/actions';
import { buildSeq, missingIntensityIds } from '../../../lib/checkin';
import type { SeqStep } from '../../../lib/checkin';
import { cycleDay } from '../../../lib/cycle';
import { filterByGroup, groupedSections, uniqueIds, UNGROUPED_ID } from '../../../lib/groups';
import { det } from '../../../lib/summary';
import { isoOff } from '../../../lib/dates';
import { makeSymDef } from '../../../lib/utils';
import GroupBadges from '../../ui/GroupBadges';

interface ReviewRow { key: string; label: string; value: string; }

function Rows({ rows }: { rows: ReviewRow[] }) {
  return <>{rows.map((row) => (
    <div key={row.key} className="nd-review-row">
      <span className="text-muted">{row.label}</span><span>{row.value}</span>
    </div>
  ))}</>;
}

export default function StepReview() {
  const { state: st, dispatch } = useApp();
  const now = useNow();
  const c = st.checkin;
  if (!c) return null;
  const d = c.d;
  const seq = buildSeq(d.sel);
  const symDef = makeSymDef(st.data.custom);
  const goTo = (q: SeqStep) => dispatch(checkinPatch(q.s === 3 ? { step: 3, symIdx: q.i } : { step: q.s }));
  const dayWord = c.date === isoOff(now, 0) ? 'сьогодні' : 'цього дня';
  const missing = missingIntensityIds(d, symDef);
  const selectedSections = groupedSections(d.sel, st.data);
  const scope = filterByGroup(st.data.active, d.groupId, st.data);
  const absenceCandidates = scope.filter((id) => !d.sel.includes(id));
  const absenceChecked = absenceCandidates.length > 0 && absenceCandidates.every((id) => d.absent.includes(id));
  const scopeName = d.groupId === null
    ? 'усі активні симптоми'
    : d.groupId === UNGROUPED_ID
      ? 'секція «Без групи»'
      : `група «${st.data.groups.find((group) => group.id === d.groupId)?.name || ''}»`;
  const absentNames = absenceCandidates.map((id) => symDef(id)?.name || id);
  const toggleAbsence = () => dispatch(checkinPatch({
    absent: absenceChecked
      ? d.absent.filter((id) => !absenceCandidates.includes(id))
      : uniqueIds([...d.absent, ...absenceCandidates]),
    confirmed: false,
    noSymptoms: false
  }));

  const cx = d.ctx;
  const contextRows: ReviewRow[] = [
    { key: 'stress', label: 'Стрес', value: cx.stress == null ? 'не заповнено' : cx.stress + '/5' },
    {
      key: 'sleep', label: 'Сон', value: [cx.sleepQ == null ? 'якість не заповнено' : cx.sleepQ + '/5', cx.sleepH == null ? 'години не заповнено' : cx.sleepH + ' год'].join(' · ')
    },
    { key: 'activity', label: 'Активність', value: cx.activity == null ? 'не заповнено' : (cx.activity ? cx.actType || 'так' : 'ні') },
    { key: 'heat', label: 'Спека / перегрів', value: cx.heat == null ? 'не заповнено' : (cx.heat ? 'так' : 'ні') }
  ];
  if (cx.extras.length) contextRows.push({ key: 'extras', label: 'Додатково', value: cx.extras.join(', ') });
  const cycle = st.data.cycleOn ? cycleDay(c.date, st.data.cycleStarts) : null;
  if (cycle) contextRows.push({ key: 'cycle', label: 'Цикл', value: 'день ' + cycle });

  const moodCrisis = d.sel.includes('mood') && d.sym.mood?.int === 5;

  return (
    <>
      {missing.length > 0 && (
        <div role="alert" className="nd-inline-error">Не вибрано обовʼязкову інтенсивність: {missing.map((id) => symDef(id)?.name || id).join(', ')}.</div>
      )}
      <div className="card">
        <div className="nd-review-heading"><span className="card-kicker">Самопочуття</span><button className="btn btn-ghost" onClick={() => goTo(seq[0])}>Змінити самопочуття</button></div>
        <Rows rows={[{ key: 'wellbeing', label: 'Оцінка', value: d.wbSkip || d.wb == null ? 'не заповнено' : d.wb + '/10' }]} />
      </div>

      <div className="card">
        <div className="nd-review-heading"><span className="card-kicker">Симптоми</span><button className="btn btn-ghost" onClick={() => goTo(seq[1])}>Змінити симптоми</button></div>
        {!d.sel.length && <span className="text-muted" style={{ fontSize: 13 }}>Симптоми не вибрані. Це лишається «не заповнено», якщо нижче не підтвердити відсутність.</span>}
        {selectedSections.map((section) => (
          <section key={section.id} className="nd-review-group" aria-label={section.title}>
            <h3>{section.title}</h3>
            {section.symptomIds.map((id) => {
              const symptom = symDef(id);
              if (!symptom) return null;
              return (
                <div key={id} className="nd-review-symptom">
                  <div><span>{symptom.name}</span><GroupBadges symptomId={id} data={st.data} /></div>
                  <span className={missing.includes(id) ? 'nd-inline-error' : 'text-muted'}>{missing.includes(id) ? 'потрібно вибрати інтенсивність' : det(d.sym[id] || {}, symptom)}</span>
                </div>
              );
            })}
          </section>
        ))}
      </div>

      <div className="card">
        <div className="nd-review-heading"><span className="card-kicker">Контекст дня · опційно</span><button className="btn btn-ghost" onClick={() => dispatch(checkinPatch({ step: 4 }))}>Додати контекст</button></div>
        <Rows rows={contextRows} />
      </div>

      <div className="card">
        <div className="nd-review-heading"><span className="card-kicker">Нотатка й позначки · опційно</span><button className="btn btn-ghost" onClick={() => dispatch(checkinPatch({ step: 5 }))}>{d.note || d.flare ? 'Змінити нотатку й позначки' : 'Додати нотатку й позначки'}</button></div>
        <Rows rows={[
          { key: 'note', label: 'Нотатка', value: d.note ? (d.note.length > 80 ? d.note.slice(0, 80) + '…' : d.note) : 'не додано' },
          { key: 'change', label: 'Важлива зміна симптомів', value: d.flare ? 'позначено вручну' : 'не позначено' }
        ]} />
      </div>

      {moodCrisis && (
        <div className="card" style={{ background: 'var(--color-accent-2-100)' }}>
          <span style={{ fontWeight: 600 }}>Схоже, {dayWord} дуже важко.</span>
          <span style={{ fontSize: 13 }}>Якщо хочете, тут є шлях до підтримки. Застосунок не виконує жодних автоматичних дій.</span>
          <button className="btn btn-secondary" onClick={() => dispatch(navSub('crisis', { crisisAns: '' }))}>Потрібна підтримка зараз</button>
        </div>
      )}

      {absenceCandidates.length > 0 && (
        <>
          <button className="nd-check-row" onClick={toggleAbsence} role="checkbox" aria-checked={absenceChecked}>
            <span aria-hidden="true" className={absenceChecked ? 'nd-check-box is-checked' : 'nd-check-box'}>{absenceChecked ? '✓' : ''}</span>
            <span><strong>Підтвердити відсутність у показаній секції</strong><small>Обсяг: {scopeName}. Симптоми поза наведеним переліком не зміняться; спільний симптом має одне значення в усіх групах.</small></span>
          </button>
          <div className="card" style={{ fontSize: 13 }}>
            <strong>{absenceChecked ? 'Буде записано як «не було»:' : 'Можна явно підтвердити:'}</strong>
            <span className="text-muted">{absentNames.join(', ')}</span>
          </div>
        </>
      )}
      {d.legacyNoSymptoms && d.absent.length === 0 && (
        <div className="card" style={{ fontSize: 13 }}>
          <strong>Стара загальна позначка</strong>
          <span className="text-muted">У старій версії було записано, що симптомів не було, але точний перелік відстежуваних симптомів не збережено. Жодної відсутності за окремим симптомом не виведено.</span>
        </div>
      )}
    </>
  );
}
