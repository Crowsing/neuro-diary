import { useApp } from '../../../state/store';
import {
  catAddCustom,
  catAddFromLib,
  catArchive,
  catBack,
  catMove,
  catRestore,
  catSet,
  symptomGroupsSet,
  toastShow
} from '../../../state/actions';
import { CAT } from '../../../constants/catalog';
import { NEW_TYPE_CHIPS } from '../../../constants/copy';
import { SYM } from '../../../constants/symptoms';
import { filterByGroup, groupBadges, UNGROUPED_ID, visibleGroups } from '../../../lib/groups';
import { makeSymDef, toggle } from '../../../lib/utils';
import type { SymType, SymptomDef } from '../../../lib/types';
import Chip from '../../ui/Chip';

const GROUP_DISCLAIMER = 'Групи допомагають упорядкувати записи. Вони не означають, що симптом спричинений певним станом, і не підтверджують діагноз.';
const HIDDEN_CATEGORIES = new Set(['Приватний опційний модуль', 'Подієві']);
const foldName = (name: string) => name.trim().toLocaleLowerCase('uk-UA');

export default function CatalogScreen() {
  const { state, dispatch } = useApp();
  const data = state.data;
  const groups = visibleGroups(data);
  const query = foldName(state.catQ || '');
  const symDef = makeSymDef(data.custom);
  const trackedIds = new Set([...data.active, ...data.archived]);
  const knownByName = new Map([...SYM, ...data.custom].map((symptom) => [foldName(symptom.name), symptom.id]));
  const selectedGroupId = groups.some((group) => group.id === state.catalogGroupId)
    ? state.catalogGroupId
    : null;
  const filteredActive = filterByGroup(data.active, state.catalogGroupId, data);
  const filteredArchived = filterByGroup(data.archived, state.catalogGroupId, data);
  const validNewGroupIds = state.newGroupIds.filter((id) => groups.some((group) => group.id === id));

  const setGroups = (symptomId: string, groupId: string) => {
    const current = data.symptomGroupIds[symptomId] || [];
    dispatch(symptomGroupsSet(symptomId, toggle(current, groupId)));
  };

  const moveFilteredSymptom = (filteredIndex: number, direction: -1 | 1) => {
    const current = filteredActive[filteredIndex];
    const neighbor = filteredActive[filteredIndex + direction];
    if (!current || !neighbor) return;

    const from = data.active.indexOf(current);
    const to = data.active.indexOf(neighbor);
    const reducerDirection: -1 | 1 = to > from ? 1 : -1;
    for (let index = from; index !== to; index += reducerDirection) {
      dispatch(catMove(index, reducerDirection));
    }
  };

  const addFromLibrary = (name: string, category: string) => {
    const existingId = knownByName.get(foldName(name));
    if (!existingId) {
      dispatch(catAddFromLib(name, category));
      return;
    }

    let changed = false;
    if (!trackedIds.has(existingId)) {
      dispatch(catRestore(existingId));
      changed = true;
    }
    if (selectedGroupId && !(data.symptomGroupIds[existingId] || []).includes(selectedGroupId)) {
      dispatch(symptomGroupsSet(existingId, [...(data.symptomGroupIds[existingId] || []), selectedGroupId]));
      changed = true;
    }
    dispatch(toastShow(changed ? 'Симптом додано до вибраного списку' : 'Уже у вашому списку'));
  };

  const addCustom = () => {
    const name = state.newName.trim();
    const existingId = knownByName.get(foldName(name));
    if (!name || !existingId) {
      if (validNewGroupIds.length !== state.newGroupIds.length) dispatch(catSet({ newGroupIds: validNewGroupIds }));
      dispatch(catAddCustom());
      return;
    }

    let changed = false;
    if (!trackedIds.has(existingId)) {
      dispatch(catRestore(existingId));
      changed = true;
    }
    const memberships = data.symptomGroupIds[existingId] || [];
    const merged = [...new Set([...memberships, ...validNewGroupIds])];
    if (merged.length !== memberships.length) {
      dispatch(symptomGroupsSet(existingId, merged));
      changed = true;
    }
    dispatch(catSet({ newName: '', newGroupIds: [] }));
    dispatch(toastShow(changed ? 'Наявний симптом додано до вибраного списку' : 'Симптом із такою назвою вже існує'));
  };

  const catalog = CAT
    .filter(([category]) => !HIDDEN_CATEGORIES.has(category))
    .map(([category, names]) => ({
      category,
      names: names.filter((name) => !query || foldName(name).includes(query))
    }))
    .filter(({ names }) => names.length > 0);

  const groupAssignments = (symptom: SymptomDef) => {
    const memberships = data.symptomGroupIds[symptom.id] || [];
    return groups.length > 0 && (
      <div role="group" aria-label={`Групи для симптому «${symptom.name}»`} style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {groups.map((group) => {
          const selected = memberships.includes(group.id);
          return (
            <Chip
              key={group.id}
              label={group.name}
              aria={`${selected ? 'Прибрати з' : 'Додати до'} групи «${group.name}» симптом «${symptom.name}»`}
              selected={selected}
              onClick={() => setGroups(symptom.id, group.id)}
              style={{ padding: '7px 11px', fontSize: 12, maxWidth: '100%', whiteSpace: 'normal', overflowWrap: 'anywhere', textAlign: 'left' }}
            />
          );
        })}
      </div>
    );
  };

  const badges = (symptom: SymptomDef) => {
    const names = groupBadges(symptom.id, data);
    return (
      <div aria-label={names.length ? `Групи: ${names.join(', ')}` : 'Без групи'} style={{ display: 'flex', flexWrap: 'wrap', gap: 5, minWidth: 0 }}>
        {names.length > 0
          ? names.map((name) => <span key={name} className="tag tag-accent-2" style={{ maxWidth: '100%', whiteSpace: 'normal', overflowWrap: 'anywhere' }}>{name}</span>)
          : <span className="tag tag-neutral">Без групи</span>}
      </div>
    );
  };

  return (
    <div
      data-screen-label="Мої симптоми"
      style={{ flex: 1, overflowY: 'auto', padding: '6px 20px 24px', display: 'flex', flexDirection: 'column', gap: 13 }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <button className="btn btn-icon btn-secondary" aria-label="Назад" onClick={() => dispatch(catBack())} style={{ width: 44, height: 44, flex: 'none' }}>
          <svg width="16" height="16" aria-hidden="true"><path d="M10 2 L4 8 L10 14" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </button>
        <h2 style={{ fontSize: 20, margin: 0, flex: 1, minWidth: 0, overflowWrap: 'anywhere' }}>Мої симптоми</h2>
      </div>

      <input
        className="input"
        aria-label="Пошук симптому в каталозі"
        placeholder="Пошук у каталозі…"
        value={state.catQ}
        onChange={(event) => dispatch(catSet({ catQ: event.target.value }))}
        style={{ minHeight: 48, fontSize: 15 }}
      />

      <section className="card" aria-labelledby="nd-catalog-filter" style={{ gap: 8 }}>
        <h3 id="nd-catalog-filter" className="card-kicker" style={{ margin: 0 }}>Фільтр за групою</h3>
        <p style={{ fontSize: 12.5, margin: 0 }} className="text-muted">{GROUP_DISCLAIMER}</p>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
          <Chip
            label="Усі"
            aria="Показати симптоми з усіх груп"
            selected={state.catalogGroupId === null}
            onClick={() => dispatch(catSet({ catalogGroupId: null }))}
            style={{ padding: '7px 13px', fontSize: 12.5 }}
          />
          {groups.map((group) => (
            <Chip
              key={group.id}
              label={group.name}
              aria={`Показати симптоми групи «${group.name}»`}
              selected={state.catalogGroupId === group.id}
              onClick={() => dispatch(catSet({ catalogGroupId: group.id }))}
              style={{ padding: '7px 13px', fontSize: 12.5, maxWidth: '100%', whiteSpace: 'normal', overflowWrap: 'anywhere', textAlign: 'left' }}
            />
          ))}
          <Chip
            label="Без групи"
            aria="Показати симптоми без групи"
            selected={state.catalogGroupId === UNGROUPED_ID}
            onClick={() => dispatch(catSet({ catalogGroupId: UNGROUPED_ID }))}
            style={{ padding: '7px 13px', fontSize: 12.5 }}
          />
        </div>
      </section>

      <section className="card" aria-labelledby="nd-active-symptoms" style={{ gap: 8 }}>
        <h3 id="nd-active-symptoms" className="card-kicker" style={{ margin: 0 }}>Активні · питаємо щодня · {filteredActive.length}</h3>
        {filteredActive.length === 0 && <p style={{ fontSize: 13, margin: 0 }} className="text-muted">У цьому фільтрі активних симптомів немає.</p>}
        {filteredActive.map((id, filteredIndex) => {
          const symptom: SymptomDef = symDef(id) || { id, name: String(id), type: 'bool' };
          const meta = `${symptom.type === 'scale' ? 'шкала 1–5' : 'так / ні'}${symptom.side ? ' · сторона' : ''}${String(id)[0] === 'c' ? ' · власний' : ''}`;
          return (
            <div key={id} style={{ display: 'flex', flexDirection: 'column', gap: 7, minWidth: 0, borderBottom: filteredIndex < filteredActive.length - 1 ? '1px solid var(--color-divider)' : undefined, padding: '7px 0' }}>
              <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, minWidth: 0 }}>
                <span style={{ flex: '1 1 150px', minWidth: 0, display: 'flex', flexDirection: 'column' }}>
                  <span style={{ fontSize: 13.5, fontWeight: 600, whiteSpace: 'normal', overflowWrap: 'anywhere' }}>{symptom.name}</span>
                  <span style={{ fontSize: 11 }} className="text-muted">{meta}</span>
                </span>
                <button
                  className="btn btn-icon btn-secondary"
                  aria-label={`Перемістити симптом «${symptom.name}» вище`}
                  disabled={filteredIndex === 0}
                  onClick={() => moveFilteredSymptom(filteredIndex, -1)}
                  style={{ width: 44, height: 44 }}
                >
                  <svg width="12" height="12" aria-hidden="true"><path d="M2 8 L6 3 L10 8" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
                </button>
                <button
                  className="btn btn-icon btn-secondary"
                  aria-label={`Перемістити симптом «${symptom.name}» нижче`}
                  disabled={filteredIndex === filteredActive.length - 1}
                  onClick={() => moveFilteredSymptom(filteredIndex, 1)}
                  style={{ width: 44, height: 44 }}
                >
                  <svg width="12" height="12" aria-hidden="true"><path d="M2 4 L6 9 L10 4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
                </button>
                <button className="btn btn-ghost" aria-label={`Архівувати симптом «${symptom.name}»`} style={{ minHeight: 44, fontSize: 12 }} onClick={() => dispatch(catArchive(id))}>В архів</button>
              </div>
              {badges(symptom)}
              {groupAssignments(symptom)}
            </div>
          );
        })}
        <p style={{ fontSize: 11.5, margin: 0 }} className="text-muted">Кнопки «вище/нижче» — доступна альтернатива перетягуванню. Архівування не видаляє історію.</p>
      </section>

      {filteredArchived.length > 0 && (
        <section className="card" aria-labelledby="nd-archived-symptoms" style={{ gap: 8 }}>
          <h3 id="nd-archived-symptoms" className="card-kicker" style={{ margin: 0 }}>Архів · {filteredArchived.length}</h3>
          {filteredArchived.map((id) => {
            const symptom: SymptomDef = symDef(id) || { id, name: String(id), type: 'bool' as SymType };
            return (
              <div key={id} style={{ display: 'flex', flexDirection: 'column', gap: 7, minWidth: 0, padding: '5px 0' }}>
                <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, minWidth: 0 }}>
                  <span style={{ flex: '1 1 170px', minWidth: 0, fontSize: 13.5, whiteSpace: 'normal', overflowWrap: 'anywhere' }} className="text-muted">{symptom.name}</span>
                  <button className="btn btn-secondary" aria-label={`Відновити симптом «${symptom.name}»`} style={{ minHeight: 44, fontSize: 12 }} onClick={() => dispatch(catRestore(id))}>Відновити</button>
                </div>
                {badges(symptom)}
                {groupAssignments(symptom)}
              </div>
            );
          })}
        </section>
      )}

      <section className="card" aria-labelledby="nd-symptom-library" style={{ gap: 10 }}>
        <h3 id="nd-symptom-library" className="card-kicker" style={{ margin: 0 }}>Каталог симптомів</h3>
        <p style={{ fontSize: 12, margin: 0 }} className="text-muted">
          Виберіть симптом, щоб додати його до активного списку{selectedGroupId ? ' та поточної групи' : ''}.
        </p>
        {catalog.map(({ category, names }) => (
          <div key={category} style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0 }}>
            <h4 style={{ fontFamily: 'inherit', fontSize: 11.5, letterSpacing: '.06em', textTransform: 'uppercase', fontWeight: 700, margin: 0 }} className="text-muted">{category}</h4>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {names.map((name) => {
                const existingId = knownByName.get(foldName(name));
                const tracked = !!existingId && trackedIds.has(existingId);
                const complete = tracked && (!selectedGroupId || (data.symptomGroupIds[existingId] || []).includes(selectedGroupId));
                return (
                  <button
                    key={name}
                    type="button"
                    aria-label={complete
                      ? `Симптом «${name}» уже у вибраному списку`
                      : selectedGroupId
                        ? `Додати симптом «${name}» до активного списку та вибраної групи`
                        : `Додати симптом «${name}» до активного списку`}
                    onClick={() => addFromLibrary(name, category)}
                    style={{ minHeight: 44, maxWidth: '100%', padding: '8px 13px', borderRadius: 999, font: 'inherit', fontSize: 12.5, cursor: 'pointer', border: `1.5px solid ${complete ? 'var(--color-accent-2)' : 'var(--color-divider)'}`, background: complete ? 'var(--color-accent-2-100)' : 'var(--color-surface)', color: complete ? 'var(--color-accent-2-800)' : 'var(--color-text)', whiteSpace: 'normal', overflowWrap: 'anywhere', textAlign: 'left' }}
                  >
                    {name}{complete ? ' ✓' : ''}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
        {catalog.length === 0 && <p style={{ fontSize: 13, margin: 0 }} className="text-muted">За цим пошуком нічого не знайдено.</p>}
      </section>

      <section className="card" aria-labelledby="nd-custom-symptom" style={{ gap: 9 }}>
        <h3 id="nd-custom-symptom" className="card-kicker" style={{ margin: 0 }}>Власний симптом</h3>
        <label htmlFor="nd-custom-symptom-name" style={{ fontSize: 13, fontWeight: 600 }}>Назва</label>
        <input
          id="nd-custom-symptom-name"
          className="input"
          placeholder="Назва симптому"
          value={state.newName}
          onChange={(event) => dispatch(catSet({ newName: event.target.value }))}
          style={{ minHeight: 44 }}
        />
        <div role="group" aria-label="Тип власного симптому" style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
          {NEW_TYPE_CHIPS.map(([key, label]) => (
            <Chip
              key={key}
              label={label}
              aria={`Тип власного симптому: ${label}`}
              selected={state.newType === key}
              onClick={() => dispatch(catSet({ newType: key as SymType }))}
              style={{ padding: '8px 14px', fontSize: 13 }}
            />
          ))}
        </div>
        {groups.length > 0 && (
          <div role="group" aria-label="Групи власного симптому" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <span style={{ fontSize: 12 }} className="text-muted">Групи · можна вибрати кілька або жодної</span>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
              {groups.map((group) => {
                const selected = validNewGroupIds.includes(group.id);
                return (
                  <Chip
                    key={group.id}
                    label={group.name}
                    aria={`${selected ? 'Прибрати' : 'Додати'} групу «${group.name}» для нового симптому`}
                    selected={selected}
                    onClick={() => dispatch(catSet({ newGroupIds: toggle(validNewGroupIds, group.id) }))}
                    style={{ padding: '7px 11px', fontSize: 12, maxWidth: '100%', whiteSpace: 'normal', overflowWrap: 'anywhere', textAlign: 'left' }}
                  />
                );
              })}
            </div>
          </div>
        )}
        <button className="btn btn-primary" style={{ minHeight: 46 }} onClick={addCustom}>Додати симптом</button>
      </section>
    </div>
  );
}
