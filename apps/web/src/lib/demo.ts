// Детерміновані демо-дані, похідні від архівного genDemo з
// docs/prototype/nd-v2.dc.html. Неактуальні продуктові налаштування не переносяться.
// Порядок викликів Lehmer PRNG збережено для стабільності fixtures.

import type { AppData, Entry, SymValue } from './types';
import { SYM } from '../constants/symptoms';
import { isoOff } from './dates';

export function genDemo(now: Date): AppData {
  let seed = 987654321;
  const rnd = () => ((seed = (Math.imul(48271, seed) & 0x7fffffff)) / 2147483648);
  const cl = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, Math.round(v)));
  const entries: Record<string, Entry> = {};
  const missed = [-27, -19, -11];
  const heat = [-22, -15, -8, -5];
  const notes: Record<string, string> = {
    '-8': 'Дуже спекотно, майже не спала. Права рука слабша, ніж зазвичай.',
    '-12': 'Почалися місячні, тягнучий біль унизу живота.',
    '-20': 'Спокійний день, багато гуляла.',
    '-4': 'Стресовий день на роботі, зʼїла обід аж о 16:00.'
  };
  for (let o = -30; o <= -1; o++) {
    if (missed.includes(o)) continue;
    const iso = isoOff(now, o);
    if (o === -2) {
      entries[iso] = {
        status: 'draft',
        d: {
          wb: 6, wbSkip: false, sel: ['fatigue'], sym: { fatigue: { int: 3, extra: ['Фізична'] } },
          absent: [], groupId: null,
          ctx: { stress: null, sleepQ: null, sleepH: null, activity: null, actType: '', heat: null, extras: [] },
          note: '', flare: null, confirmed: false, noSymptoms: false, step: 3
        }
      };
      continue;
    }
    const stress = [-8, -9, -15, -4].includes(o) ? (rnd() < .4 ? 5 : 4) : cl(1 + Math.floor(rnd() * 3), 1, 5);
    const sleepQ = cl(stress >= 4 ? 1 + Math.floor(rnd() * 2) : 3 + Math.floor(rnd() * 3), 1, 5);
    const sleepH = Math.max(4, Math.min(9.5, Math.round((4.5 + rnd() * 4) * 2) / 2));
    const sym: Record<string, SymValue> = {};
    const f = cl(stress + (sleepQ <= 2 ? 1 : 0) + Math.floor(rnd() * 2) - 1 + (heat.includes(o) ? 1 : 0), 1, 5);
    if (f >= 2 || rnd() < .5) sym.fatigue = { int: f, extra: [rnd() < .5 ? 'Фізична' : 'Когнітивна'] };
    if (o === -8 || rnd() < .42) sym.armWeak = { int: o === -8 ? 4 : cl(1 + Math.floor(rnd() * 3), 1, 5), side: 'Права' };
    if ([-25, -16, -9, -3].includes(o)) sym.headache = { int: cl(2 + Math.floor(rnd() * 3), 1, 5), extra: (o === -9 || o === -25) ? ['Аура', 'Світлочутливість'] : ['Світлочутливість'] };
    if (stress >= 4 && rnd() < .6) sym.mood = { int: cl(2 + Math.floor(rnd() * 2), 1, 5) };
    entries[iso] = {
      status: 'done', wb: cl(9 - f - (stress >= 4 ? 1 : 0), 2, 9), sym,
      absent: SYM.map((item) => item.id).filter((id) => !Object.prototype.hasOwnProperty.call(sym, id)),
      ctx: { stress, sleepQ, sleepH, activity: rnd() < .4, actType: 'Прогулянка', heat: heat.includes(o), extras: heat.includes(o) ? ['Задушливе приміщення'] : [] },
      note: notes[String(o)] || '',
      flare: o === -8 ? { isNew: true, dur24: true, temp: false, note: 'Слабкість правої руки помітно сильніша за звичну.' } : null,
      noSymptoms: Object.keys(sym).length === 0, filledLater: false
    };
  }
  return {
    schemaVersion: 4,
    entries,
    cycleStarts: [isoOff(now, -12)],
    active: SYM.map((s) => s.id),
    archived: [],
    custom: [],
    groups: [],
    symptomGroupIds: {},
    cycleOn: true,
    lock: false
  };
}
