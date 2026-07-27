// Екран «Підтримка» — порт docs/prototype/nd-v2.dc.html, шаблон рядки 795–827.
// Логіка _vMiscMain: crBack 1304, crAskF/crYesF/crNoF 1305, crYes/crNo 1306.

import { useApp } from '../../../state/store';
import { crisisAnswer, crisisBack } from '../../../state/actions';
import SubScreenHeader from '../../frame/SubScreenHeader';

export default function CrisisScreen() {
  const { state, dispatch } = useApp();
  return (
    <div data-screen-label="Підтримка" className="nd-screen nd-screen--tight">
      <SubScreenHeader title="Підтримка" backLabel="Назад" onBack={() => dispatch(crisisBack())} />
      {!state.crisisAns && (
        <div className="card" style={{ gap: 10 }}>
          <span style={{ fontSize: 15, lineHeight: 1.5 }}>Дякуємо, що поділилися. Одне необовʼязкове запитання, щоб підказати правильний шлях:</span>
          <span style={{ fontSize: 15, fontWeight: 700 }}>Чи є зараз ризик, що ви завдасте собі шкоди?</span>
          <div style={{ display: 'flex', gap: 10 }}>
            <button className="btn btn-secondary" style={{ flex: 1, minHeight: 50 }} onClick={() => dispatch(crisisAnswer('yes'))}>Так</button>
            <button className="btn btn-secondary" style={{ flex: 1, minHeight: 50 }} onClick={() => dispatch(crisisAnswer('no'))}>Ні</button>
          </div>
          <span style={{ fontSize: 12 }} className="text-muted">Застосунок нічого не робить автоматично й нікому не повідомляє.</span>
        </div>
      )}
      {state.crisisAns === 'yes' && (
        <>
          <div style={{ borderRadius: 24, padding: 16, background: '#a33d2f', color: '#fff', display: 'flex', flexDirection: 'column', gap: 10 }}>
            <span style={{ fontWeight: 700, fontSize: 16 }}>Ви не мусите проходити це наодинці.</span>
            <a href="tel:112" className="btn" style={{ minHeight: 54, background: '#fff', color: '#a33d2f', fontSize: 17, textDecoration: 'none' }}>Подзвонити 112</a>
          </div>
          <div className="card" style={{ gap: 6 }}>
            <span style={{ fontSize: 13.5, lineHeight: 1.55 }}>· Постарайтеся не залишатися наодинці найближчим часом.<br />· Якщо можете — зверніться до психіатра, психотерапевта або сімейного лікаря.<br />· Ці відчуття можуть змінюватися; допомога існує.</span>
          </div>
        </>
      )}
      {state.crisisAns === 'no' && (
        <div className="card" style={{ gap: 8 }}>
          <span style={{ fontSize: 14, lineHeight: 1.55 }}>Дякуємо за відповідь. Навіть без негайного ризику ви заслуговуєте на підтримку. Якщо пригніченість тримається довше двох тижнів або заважає щодня — варто обговорити це з лікарем; щоденник допоможе показати динаміку.</span>
        </div>
      )}
    </div>
  );
}
