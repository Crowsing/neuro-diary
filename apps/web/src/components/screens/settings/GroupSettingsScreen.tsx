import { useApp } from '../../../state/store';
import {
  dialogOpen,
  groupArchive,
  groupCreate,
  groupEditStart,
  groupFormSet,
  groupMove,
  groupRename,
  groupRestore,
  navSub
} from '../../../state/actions';
import SubScreenHeader from '../../frame/SubScreenHeader';

const GROUP_DISCLAIMER = 'Групи допомагають упорядкувати записи. Вони не означають, що симптом спричинений певним станом, і не підтверджують діагноз.';

export default function GroupSettingsScreen() {
  const { state, dispatch } = useApp();
  const groups = state.data.groups;
  const activeGroups = groups.filter((group) => !group.archived);
  const archivedGroups = groups.filter((group) => group.archived);

  const symptomCount = (groupId: string) => Object.values(state.data.symptomGroupIds)
    .filter((groupIds) => groupIds.includes(groupId)).length;

  const moveActiveGroup = (activeIndex: number, direction: -1 | 1) => {
    const current = activeGroups[activeIndex];
    const neighbor = activeGroups[activeIndex + direction];
    if (!current || !neighbor) return;

    const from = groups.findIndex((group) => group.id === current.id);
    const to = groups.findIndex((group) => group.id === neighbor.id);
    const reducerDirection: -1 | 1 = to > from ? 1 : -1;
    for (let index = from; index !== to; index += reducerDirection) {
      dispatch(groupMove(index, reducerDirection));
    }
  };

  const leave = () => {
    dispatch(groupEditStart(null));
    dispatch(navSub(null, { tab: 'set' }));
  };

  return (
    <div data-screen-label="Групи спостереження" className="nd-screen nd-screen--tight nd-screen--roomy">
      <SubScreenHeader title="Групи спостереження" backLabel="Назад до налаштувань" onBack={leave} />

      <div className="card" style={{ gap: 7 }}>
        <p style={{ fontSize: 13, margin: 0 }}>{GROUP_DISCLAIMER}</p>
        <p style={{ fontSize: 12, margin: 0 }} className="text-muted">Назви груп можуть містити чутливі дані. Вони зберігаються локально разом з іншими даними застосунку.</p>
      </div>

      {state.editingGroupId === null && (
        <form
          className="card"
          style={{ gap: 8 }}
          onSubmit={(event) => {
            event.preventDefault();
            dispatch(groupCreate());
          }}
        >
          <label htmlFor="nd-new-group" style={{ fontSize: 13, fontWeight: 700 }}>Нова група</label>
          <input
            id="nd-new-group"
            className="input"
            value={state.groupName}
            onChange={(event) => dispatch(groupFormSet(event.target.value))}
            aria-invalid={!!state.groupError}
            aria-describedby={state.groupError ? 'nd-new-group-error' : 'nd-new-group-help'}
            placeholder="Назва групи"
            style={{ minHeight: 46 }}
          />
          <span id="nd-new-group-help" style={{ fontSize: 12 }} className="text-muted">Від 1 до 60 символів. Назви мають бути унікальними.</span>
          {state.groupError && <span id="nd-new-group-error" role="alert" style={{ fontSize: 13, color: '#8c291d' }}>{state.groupError}</span>}
          <button type="submit" className="btn btn-primary" style={{ minHeight: 46, alignSelf: 'flex-start' }}>Створити групу</button>
        </form>
      )}

      <section className="card" aria-labelledby="nd-active-groups" style={{ gap: 9 }}>
        <h3 id="nd-active-groups" className="card-kicker" style={{ margin: 0 }}>Активні групи · {activeGroups.length}</h3>
        {activeGroups.length === 0 && <p style={{ fontSize: 13, margin: 0 }} className="text-muted">Активних груп поки немає. Симптоми залишаються доступними без групування.</p>}
        {activeGroups.map((group, activeIndex) => {
          const editing = state.editingGroupId === group.id;
          const count = symptomCount(group.id);
          return (
            <div
              key={group.id}
              style={{ display: 'flex', flexDirection: 'column', gap: 8, minWidth: 0, padding: '9px 0', borderBottom: activeIndex < activeGroups.length - 1 ? '1px solid var(--color-divider)' : undefined }}
            >
              {editing ? (
                <form
                  style={{ display: 'flex', flexDirection: 'column', gap: 8, minWidth: 0 }}
                  onSubmit={(event) => {
                    event.preventDefault();
                    dispatch(groupRename());
                  }}
                >
                  <label htmlFor={`nd-edit-group-${activeIndex}`} style={{ fontSize: 13, fontWeight: 700 }}>Нова назва групи «{group.name}»</label>
                  <input
                    id={`nd-edit-group-${activeIndex}`}
                    className="input"
                    value={state.groupName}
                    onChange={(event) => dispatch(groupFormSet(event.target.value))}
                    aria-invalid={!!state.groupError}
                    aria-describedby={state.groupError ? `nd-edit-group-error-${activeIndex}` : undefined}
                    style={{ minHeight: 46 }}
                  />
                  {state.groupError && <span id={`nd-edit-group-error-${activeIndex}`} role="alert" style={{ fontSize: 13, color: '#8c291d' }}>{state.groupError}</span>}
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                    <button type="submit" className="btn btn-primary" style={{ minHeight: 44 }}>Зберегти назву</button>
                    <button type="button" className="btn btn-secondary" style={{ minHeight: 44 }} onClick={() => dispatch(groupEditStart(null))}>Скасувати</button>
                  </div>
                </form>
              ) : (
                <>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 14.5, fontWeight: 700, minWidth: 0, whiteSpace: 'normal', overflowWrap: 'anywhere' }}>{group.name}</div>
                    <div style={{ fontSize: 12 }} className="text-muted">Симптомів у групі: {count}</div>
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    <button
                      className="btn btn-icon btn-secondary"
                      aria-label={`Перемістити групу «${group.name}» вище`}
                      disabled={activeIndex === 0}
                      onClick={() => moveActiveGroup(activeIndex, -1)}
                      style={{ width: 44, height: 44 }}
                    >
                      <svg width="12" height="12" aria-hidden="true"><path d="M2 8 L6 3 L10 8" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
                    </button>
                    <button
                      className="btn btn-icon btn-secondary"
                      aria-label={`Перемістити групу «${group.name}» нижче`}
                      disabled={activeIndex === activeGroups.length - 1}
                      onClick={() => moveActiveGroup(activeIndex, 1)}
                      style={{ width: 44, height: 44 }}
                    >
                      <svg width="12" height="12" aria-hidden="true"><path d="M2 4 L6 9 L10 4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
                    </button>
                    <button className="btn btn-secondary" aria-label={`Перейменувати групу «${group.name}»`} style={{ minHeight: 44 }} onClick={() => dispatch(groupEditStart(group.id))}>Перейменувати</button>
                    <button className="btn btn-ghost" aria-label={`Архівувати групу «${group.name}»`} style={{ minHeight: 44 }} onClick={() => dispatch(groupArchive(group.id))}>В архів</button>
                    <button className="btn btn-ghost" aria-label={`Видалити групу «${group.name}»`} style={{ minHeight: 44 }} onClick={() => dispatch(dialogOpen({ type: 'delGroup', id: group.id }))}>Видалити</button>
                  </div>
                </>
              )}
            </div>
          );
        })}
      </section>

      {archivedGroups.length > 0 && (
        <section className="card" aria-labelledby="nd-archived-groups" style={{ gap: 9 }}>
          <h3 id="nd-archived-groups" className="card-kicker" style={{ margin: 0 }}>Архів груп · {archivedGroups.length}</h3>
          <p style={{ fontSize: 12, margin: 0 }} className="text-muted">Архівування приховує групу з фільтрів, але зберігає її прив’язки.</p>
          {archivedGroups.map((group) => (
            <div key={group.id} style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, minWidth: 0, padding: '5px 0' }}>
              <span style={{ flex: '1 1 180px', minWidth: 0, fontSize: 13.5, whiteSpace: 'normal', overflowWrap: 'anywhere' }} className="text-muted">
                {group.name} · симптомів: {symptomCount(group.id)}
              </span>
              <button className="btn btn-secondary" aria-label={`Відновити групу «${group.name}»`} style={{ minHeight: 44 }} onClick={() => dispatch(groupRestore(group.id))}>Відновити</button>
              <button className="btn btn-ghost" aria-label={`Видалити групу «${group.name}»`} style={{ minHeight: 44 }} onClick={() => dispatch(dialogOpen({ type: 'delGroup', id: group.id }))}>Видалити</button>
            </div>
          ))}
        </section>
      )}
    </div>
  );
}
