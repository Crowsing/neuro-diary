import { useEffect, useState } from 'react';
import { useApp } from '../../../state/store';
import { useNow } from '../../../state/clock';
import { checkinExit, checkinFinish, checkinPatch, dialogOpen, navSub } from '../../../state/actions';
import { buildSeq, draftMatchesDone, missingIntensityIds, seqIndex } from '../../../lib/checkin';
import type { SeqStep } from '../../../lib/checkin';
import { fmtLong, isoOff } from '../../../lib/dates';
import { makeSymDef } from '../../../lib/utils';
import { CHECKIN_STEP_TITLES } from '../../../constants/copy';
import StepWellbeing from './StepWellbeing';
import StepSymptoms from './StepSymptoms';
import SymptomCard from './SymptomCard';
import StepContext from './StepContext';
import StepNote from './StepNote';
import StepReview from './StepReview';

export default function CheckinScreen() {
  const { state: st, dispatch } = useApp();
  const now = useNow();
  const [intensityError, setIntensityError] = useState<string | null>(null);
  const c = st.checkin;
  const errorValue = intensityError && c?.d.sym[intensityError]?.int;
  useEffect(() => {
    if (intensityError && errorValue != null) setIntensityError(null);
  }, [intensityError, errorValue]);
  if (!c) return null;

  const d = c.d;
  const seq = buildSeq(d.sel);
  const satellite = d.step === 4 || d.step === 5;
  const index = satellite ? seq.length - 1 : seqIndex(seq, d.step, d.symIdx);
  const current: SeqStep = satellite ? { s: d.step } : seq[index];
  const step = current.s;
  const symDef = makeSymDef(st.data.custom);
  const currentSymptom = step === 3 ? d.sel[current.i || 0] : null;
  const currentDefinition = currentSymptom ? symDef(currentSymptom) : null;
  const original = st.data.entries[c.date];
  const doneOriginal = original?.status === 'done' ? original : null;
  const editingDone = !!doneOriginal;
  const hasEditChanges = !!doneOriginal && !draftMatchesDone(d, doneOriginal);
  const dayWord = c.date === isoOff(now, 0) ? 'сьогодні' : 'цього дня';

  const goTo = (target: SeqStep) => dispatch(checkinPatch(target.s === 3 ? { step: 3, symIdx: target.i } : { step: target.s }));
  const focusIntensity = (id: string) => {
    setIntensityError(id);
    window.setTimeout(() => document.getElementById('intensity-' + id)?.focus(), 0);
  };
  const requestExit = () => {
    if (!editingDone) {
      dispatch(checkinExit(true));
      return;
    }
    if (hasEditChanges) dispatch(dialogOpen({ type: 'discardEdit' }));
    else dispatch(checkinExit(false));
  };
  const back = () => {
    setIntensityError(null);
    if (satellite) dispatch(checkinPatch({ step: 6 }));
    else goTo(seq[index - 1]);
  };
  const next = () => {
    if (satellite) {
      dispatch(checkinPatch({ step: 6 }));
      return;
    }
    if (step === 3 && currentSymptom && currentDefinition?.type === 'scale' && d.sym[currentSymptom]?.int == null) {
      focusIntensity(currentSymptom);
      return;
    }
    if (step === 6) {
      const missing = missingIntensityIds(d, symDef);
      if (missing.length) {
        const symptomIndex = d.sel.indexOf(missing[0]);
        dispatch(checkinPatch({ step: 3, symIdx: Math.max(0, symptomIndex) }));
        focusIntensity(missing[0]);
        return;
      }
      dispatch(checkinFinish());
      return;
    }
    setIntensityError(null);
    goTo(seq[index + 1]);
  };

  const stepText = satellite
    ? 'Опційно · ' + fmtLong(c.date)
    : 'Крок ' + (index + 1) + ' із ' + seq.length + ' · ' + fmtLong(c.date);
  const title = step === 3 ? currentDefinition?.name || '' : CHECKIN_STEP_TITLES[step];
  const progress = satellite ? '100%' : Math.round((index + 1) / seq.length * 100) + '%';
  const showBack = satellite || index > 0;
  const nextLabel = satellite ? 'Готово — до огляду' : (step === 6 ? 'Завершити запис' : 'Далі');

  return (
    <div data-screen-label="Щоденний запис" style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <div style={{ flex: 'none', display: 'flex', alignItems: 'center', gap: 10, padding: '6px 14px 10px' }}>
        <button className="btn btn-icon btn-secondary" aria-label={editingDone ? 'Скасувати редагування' : 'Закрити і зберегти чернетку'} onClick={requestExit} style={{ width: 44, height: 44 }}>
          <svg width="16" height="16" aria-hidden="true"><path d="M2 2 L14 14 M14 2 L2 14" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" /></svg>
        </button>
        <div style={{ flex: 1, minWidth: 0 }}><div style={{ fontSize: '11.5px' }} className="text-muted">{stepText}</div><div style={{ fontFamily: 'var(--font-heading)', fontSize: 18 }}>{title}</div></div>
      </div>
      <div style={{ flex: 'none', height: 5, margin: '0 20px', borderRadius: 99, background: 'var(--color-neutral-200)', overflow: 'hidden' }}><div style={{ height: '100%', borderRadius: 99, background: 'var(--color-accent)', width: progress, transition: 'width .2s' }} /></div>
      <div style={{ flex: 1, overflowY: 'auto', padding: '16px 20px 20px', display: 'flex', flexDirection: 'column', gap: 14 }}>
        {step === 1 && <StepWellbeing />}
        {step === 2 && <StepSymptoms />}
        {step === 3 && (
          <>
            <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 10 }}><span style={{ fontSize: 13, fontWeight: 700 }} className="text-muted">Симптом {(current.i || 0) + 1} із {d.sel.length}</span><span style={{ fontSize: 12 }} className="text-muted">інтенсивність обовʼязкова для шкали</span></div>
            {currentSymptom && <SymptomCard id={currentSymptom} error={intensityError === currentSymptom} />}
            {currentSymptom === 'mood' && d.sym.mood?.int === 5 && (
              <div className="card" style={{ background: 'var(--color-accent-2-100)' }}>
                <span style={{ fontWeight: 600 }}>Схоже, {dayWord} дуже важко.</span>
                <span style={{ fontSize: 13 }}>Можна продовжити запис або відкрити перевірені дії підтримки. Застосунок нічого не робить автоматично.</span>
                <button className="btn btn-secondary" onClick={() => dispatch(navSub('crisis', { crisisAns: '' }))}>Потрібна підтримка зараз</button>
              </div>
            )}
          </>
        )}
        {step === 4 && <StepContext />}
        {step === 5 && <StepNote />}
        {step === 6 && <StepReview />}
      </div>
      <div style={{ flex: 'none', padding: '12px 20px 16px', borderTop: '1px solid var(--color-divider)', display: 'flex', flexDirection: 'column', gap: 8, background: 'var(--color-bg)' }}>
        <div style={{ display: 'flex', gap: 10 }}>
          {showBack && <button className="btn btn-secondary" style={{ minHeight: 52 }} onClick={back}>Назад</button>}
          <button className="btn btn-primary" style={{ flex: 1, minHeight: 52, fontSize: 16 }} onClick={next}>{nextLabel}</button>
        </div>
        <button className="btn btn-ghost" style={{ minHeight: 44 }} onClick={requestExit}>{editingDone ? 'Скасувати редагування' : 'Зберегти як чернетку й вийти'}</button>
      </div>
    </div>
  );
}
