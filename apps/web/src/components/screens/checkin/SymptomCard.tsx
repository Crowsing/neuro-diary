// Картка одного симптому в кроці 3 чек-іна — порт docs/prototype/nd-v2.dc.html:
// шаблон рядки 394–436, логіка _vCheckin рядки 1337–1353 (cards).

import type { ChangeEvent } from 'react';
import { useApp } from '../../../state/store';
import { checkinSymPatch } from '../../../state/actions';
import { makeSymDef, toggle } from '../../../lib/utils';
import { IMPACT, INT } from '../../../constants/symptoms';
import type { SymptomDef, SymValue } from '../../../lib/types';
import Chip from '../../ui/Chip';
import ScaleOption from '../../ui/ScaleOption';

export default function SymptomCard({ id, error = false }: { id: string; error?: boolean }) {
  const { state: st, dispatch } = useApp();
  const c = st.checkin;
  if (!c) return null;
  const d = c.d;
  const symDef = makeSymDef(st.data.custom);
  // Фолбек прототипу (рядок 1338): {name:id,type:'bool'}.
  const s: SymptomDef = symDef(id) || { id, name: id, type: 'bool' };
  const sv: SymValue = d.sym[id] || {};
  const usym = (patch: Partial<SymValue>) => dispatch(checkinSymPatch(id, patch));
  const intLabel = sv.int ? sv.int + ' — ' + INT[sv.int] : 'Оберіть інтенсивність (відсутність — це окремий стан, не «1»)';
  const moreF = !!sv.more;
  const errorId = 'intensity-error-' + id;
  return (
    <div className="card" style={{ gap: 12 }}>
      {s.type === 'scale' && (
        <fieldset
          id={'intensity-' + id}
          tabIndex={-1}
          aria-describedby={error ? errorId : undefined}
          style={{ border: 0, padding: 0, margin: 0, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 10 }}
        >
          <legend style={{ fontFamily: 'var(--font-heading)', fontSize: 17, padding: 0, marginBottom: 6 }}>Інтенсивність — {s.name}</legend>
          <div style={{ display: 'flex', gap: 6 }}>
            {[1, 2, 3, 4, 5].map((n) => (
              <ScaleOption
                key={n}
                label={n}
                selected={sv.int === n}
                onClick={() => usym({ int: n })}
                aria={'Інтенсивність ' + s.name + ': ' + n + ' із 5, ' + INT[n]}
              />
            ))}
          </div>
          <div style={{ fontSize: '12.5px' }} className="text-muted">{intLabel}</div>
          {error && <div id={errorId} role="alert" className="nd-inline-error">Оберіть інтенсивність, щоб продовжити.</div>}
        </fieldset>
      )}
      {s.type === 'bool' && <div style={{ fontFamily: 'var(--font-heading)', fontSize: 17 }}>{s.name}</div>}
      {moreF && (
        <>
          {!!s.side && (
            <>
              <div style={{ fontSize: 12 }} className="text-muted">Сторона</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
                {(s.side || []).map((t) => (
                  <Chip
                    key={t}
                    label={t}
                    selected={sv.side === t}
                    onClick={() => usym({ side: sv.side === t ? null : t })}
                    style={{ padding: '8px 14px', fontSize: 13 }}
                  />
                ))}
              </div>
            </>
          )}
          {!!s.extra && (
            <>
              <div style={{ fontSize: 12 }} className="text-muted">{s.extra ? s.extra.label : ''}</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
                {(s.extra ? s.extra.opts : []).map((t) => (
                  <Chip
                    key={t}
                    label={t}
                    selected={(sv.extra || []).includes(t)}
                    onClick={() => usym({ extra: toggle(sv.extra || [], t) })}
                    style={{ padding: '8px 14px', fontSize: 13 }}
                  />
                ))}
              </div>
            </>
          )}
          {!!s.episodes && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <span style={{ fontSize: 13 }} className="text-muted">Кількість епізодів</span>
              <button className="btn btn-icon btn-secondary" aria-label="Менше епізодів" onClick={() => usym({ ep: Math.max(0, (sv.ep || 0) - 1) })} style={{ width: 44, height: 44, fontSize: 20 }}>−</button>
              <span style={{ minWidth: 28, textAlign: 'center', fontSize: 17, fontWeight: 700 }}>{sv.ep || 0}</span>
              <button className="btn btn-icon btn-secondary" aria-label="Більше епізодів" onClick={() => usym({ ep: (sv.ep || 0) + 1 })} style={{ width: 44, height: 44, fontSize: 20 }}>+</button>
            </div>
          )}
          {!!s.impact && (
            <>
              <div style={{ fontSize: 12 }} className="text-muted">Вплив на функціонування</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
                {IMPACT.map((t) => (
                  <Chip
                    key={t}
                    label={t}
                    selected={sv.impact === t}
                    onClick={() => usym({ impact: sv.impact === t ? null : t })}
                    style={{ padding: '8px 14px', fontSize: '12.5px' }}
                  />
                ))}
              </div>
            </>
          )}
          <input
            className="input"
            aria-label="Короткий коментар"
            placeholder="Короткий коментар (опційно)"
            value={sv.comment || ''}
            onChange={(e: ChangeEvent<HTMLInputElement>) => usym({ comment: e.target.value })}
            style={{ fontSize: 14, minHeight: 44 }}
          />
        </>
      )}
      {!moreF && (
        <button className="btn btn-ghost" style={{ minHeight: 44, alignSelf: 'flex-start' }} onClick={() => usym({ more: true })}>Додати деталі · сторона, уточнення, коментар</button>
      )}
    </div>
  );
}
