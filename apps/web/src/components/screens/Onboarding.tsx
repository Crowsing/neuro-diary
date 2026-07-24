import { useApp } from '../../state/store';
import {
  groupCreate,
  groupFormSet,
  obFinish,
  obSet,
  symptomGroupsSet
} from '../../state/actions';
import { SYM } from '../../constants/symptoms';
import { OB_CYCLE_CHIPS, OB_PRIVACY } from '../../constants/copy';
import { groupBadges, visibleGroups } from '../../lib/groups';
import { toggle } from '../../lib/utils';
import Chip from '../ui/Chip';

const GROUP_DISCLAIMER = 'Групи допомагають упорядкувати записи. Вони не означають, що симптом спричинений певним станом, і не підтверджують діагноз.';

export default function Onboarding() {
  const { state: st, dispatch } = useApp();
  const step = st.obStep;
  const selectedSymptoms = st.obSyms || [];
  const groups = visibleGroups(st.data);

  const createGroup = () => dispatch(groupCreate());
  const setSymptomGroup = (symptomId: string, groupId: string) => {
    dispatch(symptomGroupsSet(symptomId, toggle(st.data.symptomGroupIds[symptomId] || [], groupId)));
  };

  return (
    <div
      data-screen-label="Onboarding"
      style={{ flex: 1, display: 'flex', flexDirection: 'column', overflowY: 'auto', padding: '20px 22px 24px' }}
    >
      <div aria-label={`Крок ${step + 1} із 5`} style={{ display: 'flex', gap: 6, marginBottom: 26 }}>
        {[0, 1, 2, 3, 4].map((i) => (
          <span
            key={i}
            aria-hidden="true"
            style={{
              flex: 1,
              height: 5,
              borderRadius: 99,
              background: i <= step ? 'var(--color-accent)' : 'var(--color-neutral-300)'
            }}
          />
        ))}
      </div>

      {step === 0 && (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 14 }}>
          <div
            aria-hidden="true"
            style={{
              width: 84,
              height: 84,
              borderRadius: '50%',
              background: 'var(--color-accent-2-200)',
              display: 'grid',
              placeItems: 'center',
              marginBottom: 6
            }}
          >
            <svg width="40" height="40">
              <path d="M6 20 L14 20 L17 10 L23 30 L26 20 L34 20" fill="none" stroke="var(--color-accent-2-800)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
          <h2 style={{ fontSize: 32, margin: 0 }}>Пам’ятати менше.<br />Бачити більше.</h2>
          <p style={{ fontSize: 16, margin: 0 }} className="text-muted">Щоденник допомагає фіксувати симптоми та контекст, бачити власну динаміку й готувати зрозумілі факти для розмови з лікарем.</p>
          <p style={{ fontSize: 13, margin: '8px 0 0', padding: '12px 16px', borderRadius: 18, background: 'var(--color-surface)' }} className="text-muted">Застосунок допомагає фіксувати самопочуття та готувати дані для лікаря. Він не встановлює діагноз, не підтверджує загострення і не призначений для невідкладної допомоги.</p>
        </div>
      )}

      {step === 1 && (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 14 }}>
          <h2 style={{ fontSize: 26, margin: 0 }}>Приватність</h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {OB_PRIVACY.map((p) => (
              <div key={p.k} style={{ display: 'flex', gap: 12, padding: '12px 14px', borderRadius: 18, background: 'var(--color-surface)' }}>
                <span aria-hidden="true" style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--color-accent-2)', flex: 'none', marginTop: 7 }} />
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 14, fontWeight: 600 }}>{p.k}</div>
                  <div style={{ fontSize: 13 }} className="text-muted">{p.v}</div>
                </div>
              </div>
            ))}
          </div>
          <p style={{ fontSize: 12.5, margin: 0 }} className="text-muted">Назви груп і станів також є чутливими даними. Зміст записів не надсилається повідомленнями або стороннім сервісам.</p>
        </div>
      )}

      {step === 2 && (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 12 }}>
          <span className="tag tag-neutral" style={{ alignSelf: 'flex-start', fontSize: 12 }}>Опційно</span>
          <h2 style={{ fontSize: 26, margin: 0 }}>Групи спостереження</h2>
          <p style={{ fontSize: 14, margin: 0 }} className="text-muted">{GROUP_DISCLAIMER}</p>
          <form
            className="card"
            style={{ gap: 9 }}
            onSubmit={(event) => {
              event.preventDefault();
              createGroup();
            }}
          >
            <label htmlFor="nd-ob-group" style={{ fontSize: 13, fontWeight: 600 }}>Назва групи</label>
            <input
              id="nd-ob-group"
              className="input"
              value={st.groupName}
              onChange={(event) => dispatch(groupFormSet(event.target.value))}
              aria-invalid={!!st.groupError}
              aria-describedby={st.groupError ? 'nd-ob-group-error' : 'nd-ob-group-help'}
              placeholder="Наприклад, назва стану"
              style={{ minHeight: 46 }}
            />
            <span id="nd-ob-group-help" style={{ fontSize: 12 }} className="text-muted">1–60 символів. Назву можна змінити пізніше.</span>
            {st.groupError && <span id="nd-ob-group-error" role="alert" style={{ fontSize: 13, color: '#a33d2f' }}>{st.groupError}</span>}
            <button type="submit" className="btn btn-primary" style={{ minHeight: 44, alignSelf: 'flex-start' }}>Додати групу</button>
          </form>
          {groups.length > 0 ? (
            <div className="card" style={{ gap: 8 }}>
              <span className="card-kicker">Створені групи</span>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
                {groups.map((group) => (
                  <span key={group.id} className="tag tag-accent-2" style={{ maxWidth: '100%', overflowWrap: 'anywhere', whiteSpace: 'normal' }}>{group.name}</span>
                ))}
              </div>
            </div>
          ) : (
            <p style={{ fontSize: 13, margin: 0 }} className="text-muted">Можна продовжити без груп і створити їх пізніше в Налаштуваннях.</p>
          )}
        </div>
      )}

      {step === 3 && (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 12 }}>
          <h2 style={{ fontSize: 26, margin: 0 }}>Ваш список симптомів</h2>
          <p style={{ fontSize: 14, margin: 0 }} className="text-muted">Почніть з потрібних симптомів або залиште список порожнім для записів лише про самопочуття. Змінити вибір можна будь-коли.</p>
          {SYM.map((symptom) => {
            const selected = selectedSymptoms.includes(symptom.id);
            const badges = groupBadges(symptom.id, st.data);
            const memberships = st.data.symptomGroupIds[symptom.id] || [];
            return (
              <div key={symptom.id} className="card" style={{ gap: 8, minWidth: 0 }}>
                <Chip
                  label={symptom.name}
                  selected={selected}
                  onClick={() => dispatch(obSet({ obSyms: toggle(selectedSymptoms, symptom.id) }))}
                  style={{ alignSelf: 'flex-start', maxWidth: '100%', whiteSpace: 'normal', overflowWrap: 'anywhere', textAlign: 'left' }}
                />
                {badges.length > 0 && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6, minWidth: 0 }}>
                    {badges.map((name) => <span key={name} className="tag tag-accent-2" style={{ maxWidth: '100%', whiteSpace: 'normal', overflowWrap: 'anywhere' }}>{name}</span>)}
                    {badges.length > 1 && <span aria-hidden="true" style={{ fontSize: 12 }} className="text-muted">також у {badges.slice(1).join(', ')}</span>}
                  </div>
                )}
                {selected && groups.length > 0 && (
                  <div role="group" aria-label={`Групи для симптому «${symptom.name}»`} style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {groups.map((group) => (
                      <Chip
                        key={group.id}
                        label={group.name}
                        aria={`${group.name} для симптому «${symptom.name}»`}
                        selected={memberships.includes(group.id)}
                        onClick={() => setSymptomGroup(symptom.id, group.id)}
                        style={{ padding: '7px 12px', fontSize: 12, maxWidth: '100%', whiteSpace: 'normal', overflowWrap: 'anywhere' }}
                      />
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {step === 4 && (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 14 }}>
          <span className="tag tag-neutral" style={{ alignSelf: 'flex-start', fontSize: 12 }}>Опційно · вимкнено за замовчуванням</span>
          <h2 style={{ fontSize: 26, margin: 0 }}>Менструальний цикл</h2>
          <p style={{ fontSize: 14, margin: 0 }} className="text-muted">День циклу рахується автоматично від позначеного початку менструації та може допомогти побачити збіги. Дані циклу зберігаються локально разом зі щоденником і вимкнені у звіті за замовчуванням.</p>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {OB_CYCLE_CHIPS.map(([key, label]) => (
              <Chip key={key} label={label} selected={(st.obCycle ? 'on' : 'off') === key} onClick={() => dispatch(obSet({ obCycle: key === 'on' }))} style={{ minHeight: 48, padding: '9px 20px', fontSize: 14.5 }} />
            ))}
          </div>
        </div>
      )}

      <div style={{ display: 'flex', gap: 10, marginTop: 18 }}>
        {step > 0 && <button className="btn btn-secondary" style={{ minHeight: 48 }} onClick={() => dispatch(obSet({ obStep: step - 1 }))}>Назад</button>}
        <button
          className="btn btn-primary"
          style={{ flex: 1, minHeight: 48, fontSize: 16 }}
          onClick={() => step === 4 ? dispatch(obFinish()) : dispatch(obSet({ obStep: step + 1 }))}
        >{step === 4 ? 'Почати' : 'Далі'}</button>
      </div>
      {step === 2 && <button className="btn btn-ghost" style={{ minHeight: 44, marginTop: 6 }} onClick={() => dispatch(obSet({ obStep: 3 }))}>Пропустити цей крок</button>}
      {step === 3 && <button className="btn btn-ghost" style={{ minHeight: 44, marginTop: 6 }} onClick={() => dispatch(obFinish())}>Пропустити решту налаштувань</button>}
    </div>
  );
}
