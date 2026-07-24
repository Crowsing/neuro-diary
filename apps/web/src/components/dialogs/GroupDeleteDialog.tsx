import type { DialogState } from '../../lib/types';
import { dialogClose, groupDelete } from '../../state/actions';
import { useApp } from '../../state/store';

type GroupDialog = Extract<DialogState, { type: 'delGroup' }>;

export default function GroupDeleteDialog({ dlg }: { dlg: GroupDialog }) {
  const { state, dispatch } = useApp();
  const group = state.data.groups.find((item) => item.id === dlg.id);
  return (
    <>
      <div className="dialog-title">Видалити групу?</div>
      <div className="dialog-body" style={{ margin: 0 }}>
        Буде видалено лише групу{group ? ` «${group.name}»` : ''} та її організаційні привʼязки. Симптоми й усі записи залишаться.
      </div>
      <div className="dialog-actions">
        <button data-autofocus className="btn btn-secondary" style={{ minHeight: 46 }} onClick={() => dispatch(dialogClose())}>Скасувати</button>
        <button className="btn" style={{ minHeight: 46, background: '#8c2f24', color: '#fff' }} onClick={() => dispatch(groupDelete(dlg.id))}>Видалити групу</button>
      </div>
    </>
  );
}
