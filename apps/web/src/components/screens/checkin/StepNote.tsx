// Крок 5 чек-іна «Нотатка й позначки» — порт docs/prototype/nd-v2.dc.html:
// шаблон рядки 491–508, логіка _vCheckin рядки 1410–1414 (ciNote/flareTg/flChips/flTip).

import type { ChangeEvent } from 'react';
import { useApp } from '../../../state/store';
import { checkinPatch } from '../../../state/actions';
import { FLARE_CHECKIN_CHIPS } from '../../../constants/copy';
import { visibleGroups } from '../../../lib/groups';
import { toggle } from '../../../lib/utils';
import Chip from '../../ui/Chip';
import Toggle from '../../ui/Toggle';

export default function StepNote() {
  const { state: st, dispatch } = useApp();
  const c = st.checkin;
  if (!c) return null;
  const d = c.d;
  const fl = d.flare;
  const groups = visibleGroups(st.data);
  return (
    <>
      <textarea
        className="input"
        aria-label="Нотатка дня"
        placeholder="Вільна нотатка про день (опційно)"
        value={d.note || ''}
        onChange={(e: ChangeEvent<HTMLTextAreaElement>) => dispatch(checkinPatch({ note: e.target.value }))}
        style={{ fontSize: 15, borderRadius: 20, minHeight: 110 }}
      />
      <div className="card" style={{ gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <Toggle
            selected={!!fl}
            onClick={() => dispatch(checkinPatch({ flare: fl ? null : { isNew: false, dur24: false, temp: false, note: '' } }))}
            aria="Позначити важливу зміну симптомів для обговорення з лікарем"
          />
          <span style={{ fontSize: 15, fontWeight: 600 }}>Важлива зміна симптомів для обговорення з лікарем</span>
        </div>
        <span style={{ fontSize: '12.5px' }} className="text-muted">Це ваша особиста позначка для подальшого обговорення з лікарем, а не діагноз.</span>
        {!!fl && (
          <>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
              {FLARE_CHECKIN_CHIPS.map(([k, t]) => (
                <Chip
                  key={k}
                  label={t}
                  selected={fl[k]}
                  onClick={() => dispatch(checkinPatch({ flare: { ...fl, [k]: !fl[k] } }))}
                  style={{ padding: '8px 14px', fontSize: '12.5px' }}
                />
              ))}
            </div>
            {groups.length > 0 && (
              <>
                <span style={{ fontSize: 12 }} className="text-muted">Організаційні групи · опційно</span>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
                  {groups.map((group) => (
                    <Chip
                      key={group.id}
                      label={group.name}
                      selected={(fl.groupIds || []).includes(group.id)}
                      onClick={() => dispatch(checkinPatch({ flare: { ...fl, groupIds: toggle(fl.groupIds || [], group.id) } }))}
                    />
                  ))}
                </div>
                <span style={{ fontSize: 12 }} className="text-muted">Групи лише впорядковують запис і не визначають причину зміни.</span>
              </>
            )}
            {!!(fl.isNew && fl.dur24) && (
              <div style={{ padding: '11px 14px', borderRadius: 16, background: 'var(--color-accent-100)', color: 'var(--color-accent-800)', fontSize: 13 }}>Якщо новий або значно сильніший симптом триває понад добу, звʼяжіться зі своїм лікарем або медичною командою.</div>
            )}
          </>
        )}
      </div>
    </>
  );
}
