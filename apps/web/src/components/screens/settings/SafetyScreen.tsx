// Екран «Термінова допомога» — порт docs/prototype/nd-v2.dc.html, шаблон рядки 749–770.
// Логіка _vMiscMain: sfBack 1300, sfMoreF/sfMoreBtnF/sfMoreOn 1301.

import { useApp } from '../../../state/store';
import { navSub, safetyMore } from '../../../state/actions';
import SubScreenHeader from '../../frame/SubScreenHeader';

export default function SafetyScreen() {
  const { state, dispatch } = useApp();
  return (
    <div data-screen-label="Термінова допомога" className="nd-screen nd-screen--tight">
      <SubScreenHeader
        title="Коли потрібна термінова допомога?"
        backLabel="Назад"
        onBack={() => dispatch(navSub(null))}
      />
      <p style={{ margin: 0, fontSize: 14 }} className="text-muted">Це загальна довідка, а не оцінка ваших записів — застосунок не аналізує симптоми автоматично.</p>
      <div style={{ borderRadius: 24, padding: 16, background: '#a33d2f', color: '#fff', display: 'flex', flexDirection: 'column', gap: 10 }}>
        <span style={{ fontWeight: 700, fontSize: 16 }}>Якщо у вас зараз є щось із переліченого нижче…</span>
        <span style={{ fontSize: 14, lineHeight: 1.5 }}>…і воно виникло раптово, сильне або швидко посилюється — не витрачайте час на щоденник, зателефонуйте 112.</span>
        <a href="tel:112" className="btn" style={{ minHeight: 54, background: '#fff', color: '#a33d2f', fontSize: 17, textDecoration: 'none' }}>Подзвонити 112</a>
      </div>
      <div className="card" style={{ gap: 8 }}>
        <span className="card-kicker">Ключові ознаки</span>
        <span style={{ fontSize: 13.5, lineHeight: 1.6 }}>· Раптова однобічна слабкість або оніміння<br />· Асиметрія обличчя чи порушення мовлення<br />· Раптова втрата зору<br />· Раптовий надзвичайно сильний головний біль</span>
        {!!state.safetyMore && (
          <span style={{ fontSize: 13.5, lineHeight: 1.6 }}>· Втрата свідомості<br />· Перший судомний напад, напад понад 5 хвилин або повторні напади без відновлення свідомості<br />· Гостре утруднення дихання чи ковтання<br />· Нова затримка або нетримання сечі/калу разом з онімінням промежини чи двобічною слабкістю ніг</span>
        )}
        {!state.safetyMore && (
          <button className="btn btn-ghost" style={{ minHeight: 44, alignSelf: 'flex-start' }} onClick={() => dispatch(safetyMore())}>Показати більше ознак</button>
        )}
      </div>
    </div>
  );
}
