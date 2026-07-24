import { useApp } from '../../state/store';
import { dialogClose } from '../../state/actions';
import { downloadCsv, downloadJson } from '../../lib/export';

export default function PdfDialog() {
  const { state, dispatch } = useApp();
  const printReport = () => {
    dispatch(dialogClose());
    window.print();
  };

  return (
    <>
      <div className="dialog-title">Зберегти звіт</div>
      <div className="dialog-body" style={{ margin: 0 }}>Друк браузера дозволяє надрукувати звіт або зберегти його як PDF. PDF містить лише вибрані дані; назви груп потрапляють у нього тільки коли ввімкнено «Показувати назви груп».</div>
      <button className="btn btn-primary btn-block" style={{ minHeight: 48 }} onClick={printReport}>Друк / зберегти як PDF</button>
      <div style={{ fontSize: 13 }} className="text-muted">CSV містить записи, явно підтверджену відсутність симптомів, незаповнені значення контексту та поточні назви груп для симптомів у кожному рядку. JSON містить усі локальні дані: версію схеми, записи, групи, відповідності, поточні налаштування звіту та введені імʼя/дату народження.</div>
      <button className="btn btn-secondary btn-block" style={{ minHeight: 48 }} onClick={() => downloadCsv(state.data)}>Завантажити CSV записів</button>
      <button className="btn btn-secondary btn-block" style={{ minHeight: 48 }} onClick={() => downloadJson(state)}>Завантажити JSON усіх локальних даних</button>
      <div style={{ fontSize: 12.5 }} className="text-muted">Назви груп та ідентифікаційні дані є чутливими даними про здоровʼя. Після експорту файл більше не контролюється застосунком.</div>
      <div className="dialog-actions">
        <button className="btn btn-ghost" style={{ minHeight: 44 }} onClick={() => dispatch(dialogClose())}>Закрити</button>
      </div>
    </>
  );
}
