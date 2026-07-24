// Діалог «Видалити запис?» — порт шаблону, рядки 855–859, і гілки
// dl.type==='delEntry' у _vDlg (рядки 1542–1546).
// delETxt = fmtLong(iso) з великої літери + '.' (рядок 1543).

import { useApp } from '../../state/store';
import { fmtLong } from '../../lib/dates';
import { dialogClose, entryDelete } from '../../state/actions';
import type { DialogState } from '../../lib/types';

type DelEntryDlg = Extract<DialogState, { type: 'delEntry' }>;

export default function DeleteEntryDialog({ dlg }: { dlg: DelEntryDlg }) {
  const { dispatch } = useApp();
  const delETxt = fmtLong(dlg.iso).replace(/^./, (c) => c.toUpperCase()) + '.';
  return (
    <>
      <div className="dialog-title">Видалити запис?</div>
      <div className="dialog-body" style={{ margin: 0 }}>{delETxt} Запис буде видалено безповоротно, день стане «не заповнено».</div>
      <div className="dialog-actions">
        <button data-autofocus className="btn btn-secondary" style={{ minHeight: 46 }} onClick={() => dispatch(dialogClose())}>Скасувати</button>
        <button className="btn" style={{ minHeight: 46, background: '#a33d2f', color: '#fff' }} onClick={() => dispatch(entryDelete(dlg.iso))}>Видалити</button>
      </div>
    </>
  );
}
