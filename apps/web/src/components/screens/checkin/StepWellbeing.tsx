// Крок 1 чек-іна «Самопочуття» — порт docs/prototype/nd-v2.dc.html:
// шаблон рядки 371–381, логіка _vCheckin рядки 1390–1392 (wbOpts/wbLabel/wbSkipChip).

import { useApp } from '../../../state/store';
import { useNow } from '../../../state/clock';
import { checkinPatch } from '../../../state/actions';
import { isoOff } from '../../../lib/dates';
import ScaleOption from '../../ui/ScaleOption';
import Chip from '../../ui/Chip';

export default function StepWellbeing() {
  const { state: st, dispatch } = useApp();
  const now = useNow();
  const c = st.checkin;
  if (!c) return null;
  const d = c.d;
  const dayWord = c.date === isoOff(now, 0) ? 'сьогодні' : 'цього дня';
  const wbLabel = d.wbSkip
    ? 'Пропущено — залишиться як «не заповнено»'
    : (d.wb ? (d.wb + '/10' + (d.wb <= 3 ? ' — дуже погано' : (d.wb >= 8 ? ' — добре' : ''))) : '');
  return (
    <>
      <p style={{ margin: 0, fontSize: 15 }}>Як ви загалом почувалися {dayWord}?</p>
      <fieldset aria-label="Самопочуття: шкала від 1 — дуже погано до 10 — дуже добре" style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
        <legend className="text-muted" style={{ width: '100%', display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: 0, marginBottom: 8 }}><span>1 — дуже погано</span><span>10 — дуже добре</span></legend>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5,1fr)', gap: 7 }}>
        {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((n) => {
          const lab = n === 1 ? 'дуже погано' : (n === 10 ? 'дуже добре' : '');
          return (
            <ScaleOption
              key={n}
              label={n}
              selected={!d.wbSkip && d.wb === n}
              onClick={() => dispatch(checkinPatch({ wb: n, wbSkip: false }))}
              aria={n + ' із 10' + (lab ? ', ' + lab : '')}
              style={{ minHeight: 52, borderRadius: 16, fontSize: 18 }}
            />
          );
        })}
        </div>
      </fieldset>
      <div style={{ fontSize: 14, minHeight: 22, fontWeight: 600, color: 'var(--color-accent-700)' }}>{wbLabel}</div>
      <Chip
        label="Не знаю / Пропустити"
        selected={d.wbSkip}
        onClick={() => dispatch(checkinPatch({ wbSkip: !d.wbSkip, wb: null }))}
        style={{ alignSelf: 'flex-start', padding: '9px 18px', fontSize: 14 }}
      />
    </>
  );
}
