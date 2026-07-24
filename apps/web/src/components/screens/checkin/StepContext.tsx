import { useApp } from '../../../state/store';
import { checkinCtxPatch, checkinPatch } from '../../../state/actions';
import { toggle } from '../../../lib/utils';
import { ACTIVITY_TYPES, CTX_EXTRA_CHIPS } from '../../../constants/copy';
import type { Ctx } from '../../../lib/types';
import Chip from '../../ui/Chip';
import ScaleOption from '../../ui/ScaleOption';

const STRESS_LABELS = ['', 'дуже низький', 'низький', 'помірний', 'високий', 'дуже високий'];
const SLEEP_LABELS = ['', 'дуже погано', 'погано', 'посередньо', 'добре', 'дуже добре'];

function fieldsetStyle() {
  return { border: 0, margin: 0, minWidth: 0 } as const;
}

export default function StepContext() {
  const { state: st, dispatch } = useApp();
  const c = st.checkin;
  if (!c) return null;
  const d = c.d;
  const cx = d.ctx;
  const uctx = (patch: Partial<Ctx>) => dispatch(checkinCtxPatch(patch));

  return (
    <>
      <fieldset className="card" style={fieldsetStyle()}>
        <legend style={{ fontWeight: 600, fontSize: 15, padding: 0, marginBottom: 8 }}>Стрес</legend>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }} className="text-muted"><span>1 — дуже низький</span><span>5 — дуже високий</span></div>
        <div style={{ display: 'flex', gap: 6 }}>
          {[1, 2, 3, 4, 5].map((n) => (
            <ScaleOption key={n} label={n} selected={cx.stress === n} onClick={() => uctx({ stress: n })} aria={'Стрес: ' + n + ' із 5, ' + STRESS_LABELS[n]} />
          ))}
        </div>
        <button className="btn btn-ghost" style={{ minHeight: 44, alignSelf: 'flex-start' }} onClick={() => uctx({ stress: null })}>Не заповнювати стрес</button>
      </fieldset>

      <fieldset className="card" style={fieldsetStyle()}>
        <legend style={{ fontWeight: 600, fontSize: 15, padding: 0, marginBottom: 8 }}>Якість сну</legend>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }} className="text-muted"><span>1 — дуже погано</span><span>5 — дуже добре</span></div>
        <div style={{ display: 'flex', gap: 6 }}>
          {[1, 2, 3, 4, 5].map((n) => (
            <ScaleOption key={n} label={n} selected={cx.sleepQ === n} onClick={() => uctx({ sleepQ: n })} aria={'Якість сну: ' + n + ' із 5, ' + SLEEP_LABELS[n]} />
          ))}
        </div>
        <button className="btn btn-ghost" style={{ minHeight: 44, alignSelf: 'flex-start' }} onClick={() => uctx({ sleepQ: null })}>Не заповнювати якість сну</button>
        <div className="field">
          <label htmlFor="nd-sleep-hours">Годин сну (опційно)</label>
          <input
            id="nd-sleep-hours"
            className="input"
            type="number"
            inputMode="decimal"
            min="0"
            max="24"
            step="0.5"
            value={cx.sleepH ?? ''}
            placeholder="Не заповнено"
            onChange={(event) => uctx({ sleepH: event.target.value === '' ? null : Math.max(0, Math.min(24, Number(event.target.value))) })}
          />
        </div>
      </fieldset>

      {!d.ctxMore && (
        <button className="btn btn-ghost" style={{ minHeight: 44, alignSelf: 'flex-start' }} onClick={() => dispatch(checkinPatch({ ctxMore: true }))}>Додати контекст · активність, спека, інше</button>
      )}
      {!!d.ctxMore && (
        <>
          <fieldset className="card" style={fieldsetStyle()}>
            <legend style={{ fontSize: 15, fontWeight: 600, padding: 0, marginBottom: 8 }}>Фізична активність</legend>
            <div className="nd-filter-row">
              <Chip label="Так" selected={cx.activity === true} onClick={() => uctx({ activity: true })} />
              <Chip label="Ні" selected={cx.activity === false} onClick={() => uctx({ activity: false, actType: '' })} />
              <Chip label="Не заповнено" selected={cx.activity === null} onClick={() => uctx({ activity: null, actType: '' })} />
            </div>
            {cx.activity === true && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
                {ACTIVITY_TYPES.map((type) => (
                  <Chip key={type} label={type} selected={cx.actType === type} onClick={() => uctx({ actType: type })} />
                ))}
              </div>
            )}
          </fieldset>
          <fieldset className="card" style={fieldsetStyle()}>
            <legend style={{ fontSize: 15, fontWeight: 600, padding: 0, marginBottom: 8 }}>Було дуже спекотно / перегрів</legend>
            <div className="nd-filter-row">
              <Chip label="Так" selected={cx.heat === true} onClick={() => uctx({ heat: true })} />
              <Chip label="Ні" selected={cx.heat === false} onClick={() => uctx({ heat: false })} />
              <Chip label="Не заповнено" selected={cx.heat === null} onClick={() => uctx({ heat: null })} />
            </div>
          </fieldset>
          <fieldset className="card" style={fieldsetStyle()}>
            <legend className="card-kicker" style={{ padding: 0, marginBottom: 8 }}>Додатково · опційно</legend>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
              {CTX_EXTRA_CHIPS.map((type) => (
                <Chip key={type} label={type} selected={(cx.extras || []).includes(type)} onClick={() => uctx({ extras: toggle(cx.extras || [], type) })} />
              ))}
            </div>
          </fieldset>
        </>
      )}
    </>
  );
}
