// Діалог «Почалися місячні» — порт рядків 843–854 шаблону прототипу
// docs/prototype/nd-v2.dc.html; обробники — _vDlg, рядки 1525–1541
// (mensChips/mensDateOn → MENSES_SET, mensConfirm → MENSES_CONFIRM, mensCancel → DIALOG_CLOSE).

import type { DialogState } from '../../lib/types';
import { useApp } from '../../state/store';
import { useNow } from '../../state/clock';
import { isoOff } from '../../lib/dates';
import { dialogClose, mensesConfirm, mensesSet } from '../../state/actions';
import Chip from '../ui/Chip';

export type MensesDlg = Extract<DialogState, { type: 'menses' }>;

export default function MensesDialog({ dlg }: { dlg: MensesDlg }) {
  const { dispatch } = useApp();
  const now = useNow();
  const t = isoOff(now, 0);
  return (
    <>
      <div className="dialog-title">Почалися місячні</div>
      <div className="dialog-body" style={{ margin: 0 }}>Позначте дату початку — день циклу рахуватиметься від неї автоматично. Позначку можна виправити або видалити пізніше.</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
        <Chip label="Сьогодні" selected={dlg.sel === 'today'} onClick={() => dispatch(mensesSet({ sel: 'today' }))} />
        <Chip label="Вчора" selected={dlg.sel === 'yest'} onClick={() => dispatch(mensesSet({ sel: 'yest' }))} />
      </div>
      <div className="field"><label htmlFor="nd-menses-date">Інша дата</label><input id="nd-menses-date" type="date" max={t} className="input" value={dlg.custom} onChange={(e) => dispatch(mensesSet({ custom: e.target.value }))} style={{ minHeight: 44 }} /></div>
      {dlg.dup && (
        <div style={{ padding: '11px 14px', borderRadius: 16, background: 'var(--color-accent-100)', color: 'var(--color-accent-800)', fontSize: 13 }}>Ця дата вже позначена як початок менструації. Дубль не створено.</div>
      )}
      {dlg.invalid && (
        <div role="alert" className="nd-inline-error">Виберіть коректну дату не пізніше сьогодні.</div>
      )}
      <div className="dialog-actions"><button className="btn btn-secondary" style={{ minHeight: 46 }} onClick={() => dispatch(dialogClose())}>Скасувати</button><button className="btn btn-primary" style={{ minHeight: 46 }} onClick={() => dispatch(mensesConfirm())}>Позначити</button></div>
    </>
  );
}
