import { useApp } from '../../../state/store';
import { useNow } from '../../../state/clock';
import { checkinPatch, navSub } from '../../../state/actions';
import { filterByGroup, groupedSections, UNGROUPED_ID, visibleGroups } from '../../../lib/groups';
import { makeSymDef, toggle } from '../../../lib/utils';
import { isoOff } from '../../../lib/dates';
import Chip from '../../ui/Chip';
import GroupBadges from '../../ui/GroupBadges';

export default function StepSymptoms() {
  const { state: st, dispatch } = useApp();
  const now = useNow();
  const c = st.checkin;
  if (!c) return null;
  const d = c.d;
  const symDef = makeSymDef(st.data.custom);
  const active = [...new Set(st.data.active)];
  const groups = visibleGroups(st.data);
  const hasUngrouped = filterByGroup(active, UNGROUPED_ID, st.data).length > 0;
  const sections = groupedSections(active, st.data, d.groupId);
  const dayWord = c.date === isoOff(now, 0) ? 'сьогодні' : 'цього дня';

  return (
    <>
      <p style={{ margin: 0, fontSize: 15 }}>Позначте симптоми, які були {dayWord}. Невибрані залишаться як «не заповнено», доки ви явно не підтвердите їхню відсутність в огляді.</p>
      <div className="nd-filter-row" aria-label="Фільтр груп спостереження">
        <Chip label="Усі" selected={d.groupId === null} onClick={() => dispatch(checkinPatch({ groupId: null }))} />
        {groups.map((group) => (
          <Chip key={group.id} label={group.name} selected={d.groupId === group.id} onClick={() => dispatch(checkinPatch({ groupId: group.id }))} />
        ))}
        {hasUngrouped && <Chip label="Без групи" selected={d.groupId === UNGROUPED_ID} onClick={() => dispatch(checkinPatch({ groupId: UNGROUPED_ID }))} />}
      </div>
      {sections.map((section) => (
        <section key={section.id} className="card" aria-labelledby={'checkin-group-' + section.id}>
          <h3 id={'checkin-group-' + section.id} className="card-kicker" style={{ margin: 0 }}>{section.title}</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {section.symptomIds.map((id) => {
              const symptom = symDef(id);
              const nextSelected = toggle(d.sel, id);
              return (
                <div key={id} className="nd-symptom-option">
                  <Chip
                    label={symptom?.name || id}
                    selected={d.sel.includes(id)}
                    onClick={() => dispatch(checkinPatch({
                      sel: nextSelected,
                      absent: nextSelected.includes(id) ? d.absent.filter((item) => item !== id) : d.absent,
                      noSymptoms: false,
                      symIdx: 0
                    }))}
                    style={{ maxWidth: '100%', whiteSpace: 'normal' }}
                  />
                  <GroupBadges symptomId={id} data={st.data} />
                </div>
              );
            })}
          </div>
        </section>
      ))}
      {!active.length && (
        <div className="card"><span className="text-muted" style={{ fontSize: 14 }}>Активних симптомів немає. Можна продовжити запис лише із самопочуттям і контекстом.</span></div>
      )}
      <button className="btn btn-ghost" style={{ minHeight: 44, alignSelf: 'flex-start' }} onClick={() => dispatch(navSub('catalog', { catFrom: 'checkin' }))}>Повний каталог симптомів</button>
    </>
  );
}
