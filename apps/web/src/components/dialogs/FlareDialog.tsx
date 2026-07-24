// Діалог «Можливий початок загострення» — порт шаблону, рядки 877–886, і гілки
// dl.type==='flare' у _vDlg (рядки 1556–1566). Чипи рядка 881 мають
// padding 8px 14px і 12.5px (відмінно від дефолтів Chip) — передаємо style.
// Тости «Позначку збережено»/«Позначку прибрано» ставить reducer.

import { useApp } from '../../state/store';
import { dialogClose, flareDelete, flareDlgNote, flareDlgToggle, flareSave } from '../../state/actions';
import { FLARE_DIALOG_CHIPS } from '../../constants/copy';
import type { DialogState } from '../../lib/types';
import Chip from '../ui/Chip';
import { visibleGroups } from '../../lib/groups';
import { toggle } from '../../lib/utils';
import { flareDlgGroups } from '../../state/actions';

type FlareDlg = Extract<DialogState, { type: 'flare' }>;

export default function FlareDialog({ dlg }: { dlg: FlareDlg }) {
  const { state, dispatch } = useApp();
  const f = dlg.f;
  const groups = visibleGroups(state.data);
  return (
    <>
      <div className="dialog-title">Важлива зміна симптомів для обговорення з лікарем</div>
      <div className="dialog-body" style={{ margin: 0 }}>Це ваша особиста позначка для подальшого обговорення з лікарем, а не діагноз.</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
        {FLARE_DIALOG_CHIPS.map(([k, tx]) => (
          <Chip
            key={k}
            label={tx}
            selected={f[k]}
            onClick={() => dispatch(flareDlgToggle(k))}
            style={{ padding: '8px 14px', fontSize: '12.5px' }}
          />
        ))}
      </div>
      {groups.length > 0 && (
        <>
          <div style={{ fontSize: 12 }} className="text-muted">Організаційні групи · опційно</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
            {groups.map((group) => (
              <Chip key={group.id} label={group.name} selected={(f.groupIds || []).includes(group.id)} onClick={() => dispatch(flareDlgGroups(toggle(f.groupIds || [], group.id)))} />
            ))}
          </div>
          <div style={{ fontSize: 12 }} className="text-muted">Групи не визначають причину зміни й не підтверджують діагноз.</div>
        </>
      )}
      <input
        className="input"
        aria-label="Коментар до позначки"
        placeholder="Коментар (опційно)"
        value={f.note || ''}
        onChange={(e) => dispatch(flareDlgNote(e.target.value))}
        style={{ minHeight: 44, fontSize: 14 }}
      />
      {dlg.had && (
        <button className="btn btn-ghost" style={{ minHeight: 44, color: '#a33d2f' }} onClick={() => dispatch(flareDelete())}>Прибрати позначку</button>
      )}
      <div className="dialog-actions">
        <button className="btn btn-secondary" style={{ minHeight: 46 }} onClick={() => dispatch(dialogClose())}>Скасувати</button>
        <button className="btn btn-primary" style={{ minHeight: 46 }} onClick={() => dispatch(flareSave())}>Зберегти позначку</button>
      </div>
    </>
  );
}
