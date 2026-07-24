import { useApp } from '../../state/store';
import { dataDelete, dialogClose } from '../../state/actions';

export default function DataDialog() {
  const { dispatch } = useApp();
  return (
    <>
      <div className="dialog-title">Дані застосунку</div>
      <div className="dialog-body" style={{ margin: 0 }}>Дані зберігаються лише у вашому браузері. Видалення безповоротне.</div>
      <button className="btn btn-secondary btn-block" style={{ minHeight: 48 }} onClick={() => dispatch(dataDelete('cycle'))}>Видалити лише дані циклу</button>
      <button className="btn btn-block" style={{ minHeight: 48, background: '#a33d2f', color: '#fff' }} onClick={() => dispatch(dataDelete('all'))}>Видалити всі дані</button>
      <div className="dialog-actions">
        <button data-autofocus className="btn btn-ghost" style={{ minHeight: 44 }} onClick={() => dispatch(dialogClose())}>Закрити</button>
      </div>
    </>
  );
}
