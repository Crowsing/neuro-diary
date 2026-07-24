// Хост діалогів — порт оверлея рядків 840–889 прототипу.
// sc-if dlgOpen → unmount (state.dialog === null → нічого не рендеримо);
// пʼять гілок dMenses/dDelE/dDelD/dPdf/dFlare — паралельні sc-if всередині
// спільного .dialog-діва (рядки 841–842), тут — розгалуження за dlg.type.
//
// Доповнення поза шаблоном (AC7, клавіатурна доступність; референс focus-менеджменту
// не має): модальна семантика role="dialog"/aria-modal, перенесення фокуса всередину
// при відкритті, Escape закриває, Tab циклюється всередині, при закритті фокус
// повертається на елемент-відкривач.

import { useEffect, useRef } from 'react';
import type { KeyboardEvent } from 'react';
import type { DialogState } from '../../lib/types';
import { useApp } from '../../state/store';
import { dialogClose } from '../../state/actions';
import MensesDialog from './MensesDialog';
import DeleteEntryDialog from './DeleteEntryDialog';
import DataDialog from './DataDialog';
import PdfDialog from './PdfDialog';
import FlareDialog from './FlareDialog';
import GroupDeleteDialog from './GroupDeleteDialog';
import DiscardEditDialog from './DiscardEditDialog';

const DIALOG_LABELS: Record<DialogState['type'], string> = {
  menses: 'Почалися місячні',
  delEntry: 'Видалити запис?',
  delGroup: 'Видалити групу?',
  discardEdit: 'Вийти без збереження змін?',
  delData: 'Дані застосунку',
  pdf: 'Зберегти звіт',
  flare: 'Важлива зміна симптомів для обговорення з лікарем',
};

const FOCUSABLE = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

function DialogOverlay({ dlg }: { dlg: DialogState }) {
  const { dispatch } = useApp();
  const boxRef = useRef<HTMLDivElement | null>(null);
  const openerRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    openerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const box = boxRef.current;
    const first = box?.querySelector<HTMLElement>('[data-autofocus]') ?? box?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? box)?.focus();
    return () => {
      const opener = openerRef.current;
      if (opener && opener.isConnected) {
        opener.focus();
        return;
      }
      // Destructive actions may remove their opener. Focus the resulting screen
      // after React commits it instead of leaving keyboard users on <body>.
      window.setTimeout(() => {
        const screen = document.querySelector<HTMLElement>('[data-screen-label]');
        if (!screen) return;
        if (!screen.hasAttribute('tabindex')) screen.tabIndex = -1;
        screen.focus();
      }, 0);
    };
  }, []);

  const onKeyDown = (ev: KeyboardEvent<HTMLDivElement>) => {
    if (ev.key === 'Escape') {
      ev.stopPropagation();
      dispatch(dialogClose());
      return;
    }
    if (ev.key !== 'Tab') return;
    // Пастка фокуса: Tab/Shift+Tab циклюються всередині діалогу.
    const box = boxRef.current;
    if (!box) return;
    const items = Array.from(box.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => !el.hasAttribute('disabled'));
    if (!items.length) { ev.preventDefault(); return; }
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (ev.shiftKey && (active === first || active === box)) { ev.preventDefault(); last.focus(); }
    else if (!ev.shiftKey && active === last) { ev.preventDefault(); first.focus(); }
  };

  return (
    <div className="nd-dialog-overlay" onKeyDown={onKeyDown} style={{ background: 'color-mix(in srgb, var(--color-neutral-900) 45%, transparent)', display: 'grid', placeItems: 'center', padding: 22, zIndex: 50 }}>
      <div ref={boxRef} role="dialog" aria-modal="true" aria-label={DIALOG_LABELS[dlg.type]} tabIndex={-1} className="dialog elev-lg" style={{ width: '100%', gap: 12, maxHeight: '90%', overflowY: 'auto' }}>
        {dlg.type === 'menses' && <MensesDialog dlg={dlg} />}
        {dlg.type === 'delEntry' && <DeleteEntryDialog dlg={dlg} />}
        {dlg.type === 'delGroup' && <GroupDeleteDialog dlg={dlg} />}
        {dlg.type === 'discardEdit' && <DiscardEditDialog />}
        {dlg.type === 'delData' && <DataDialog />}
        {dlg.type === 'pdf' && <PdfDialog />}
        {dlg.type === 'flare' && <FlareDialog dlg={dlg} />}
      </div>
    </div>
  );
}

export default function DialogHost() {
  const { state } = useApp();
  const dlg = state.dialog;
  if (dlg === null) return null;
  return <DialogOverlay dlg={dlg} />;
}
