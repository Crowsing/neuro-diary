import { checkinExit, dialogClose } from '../../state/actions';
import { useApp } from '../../state/store';

export default function DiscardEditDialog() {
  const { dispatch } = useApp();
  const discard = () => {
    dispatch(dialogClose());
    dispatch(checkinExit(false));
  };
  return (
    <>
      <div className="dialog-title">Вийти без збереження змін?</div>
      <div className="dialog-body" style={{ margin: 0 }}>Попередній завершений запис не зміниться.</div>
      <div className="dialog-actions">
        <button data-autofocus className="btn btn-secondary" style={{ minHeight: 46 }} onClick={() => dispatch(dialogClose())}>Продовжити редагування</button>
        <button className="btn" style={{ minHeight: 46, background: '#8c2f24', color: '#fff' }} onClick={discard}>Вийти без збереження</button>
      </div>
    </>
  );
}
