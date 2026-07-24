import { describe, expect, it } from 'vitest';
import { emptyData } from '../state/persist';
import { emptyCtx } from './checkin';
import {
  UNGROUPED_ID,
  filterByGroup,
  groupBadges,
  groupedSections,
  normalizeReportSelection,
  reportableSymptomIds,
  trendableSymptomIds
} from './groups';
import type { AppData, DoneEntry } from './types';

const now = new Date('2026-07-22T12:00:00');

function entry(sym: DoneEntry['sym'] = {}, absent: string[] = []): DoneEntry {
  return { status: 'done', wb: null, sym, absent, ctx: emptyCtx(), note: '', flare: null, noSymptoms: false, filledLater: false };
}

function data(): AppData {
  return {
    ...emptyData(),
    active: ['fatigue', 'numb', 'mood'],
    archived: ['headache'],
    groups: [
      { id: 'a', name: 'Стан А', archived: false },
      { id: 'b', name: 'Стан Б', archived: false }
    ],
    symptomGroupIds: { fatigue: ['a', 'b'], numb: ['a'] },
    entries: { '2026-07-21': entry({ headache: { int: 3 } }, ['fatigue']) }
  };
}

describe('manual tracking groups', () => {
  it('one symptom in A+B remains one item in the shared section', () => {
    const sections = groupedSections(['fatigue', 'numb', 'fatigue', 'mood'], data());
    expect(sections.find((section) => section.id === '__shared__')?.symptomIds).toEqual(['fatigue']);
    expect(sections.flatMap((section) => section.symptomIds).filter((id) => id === 'fatigue')).toHaveLength(1);
    expect(groupBadges('fatigue', data())).toEqual(['Стан А', 'Стан Б']);
  });

  it('Без групи is derived and group filters never duplicate ids', () => {
    expect(filterByGroup(['mood', 'mood', 'fatigue'], UNGROUPED_ID, data())).toEqual(['mood']);
    expect(filterByGroup(['fatigue', 'numb', 'fatigue'], 'a', data())).toEqual(['fatigue', 'numb']);
  });

  it('archived symptom is trendable historically and reportable only with in-period known data', () => {
    expect(trendableSymptomIds(data())).toContain('headache');
    expect(reportableSymptomIds(data(), 7, now)).toContain('headache');
    expect(reportableSymptomIds({ ...data(), entries: {} }, 7, now)).not.toContain('headache');
  });

  it('report selection drops stale and duplicate ids', () => {
    expect(normalizeReportSelection(['fatigue', 'stale', 'fatigue'], ['fatigue', 'numb'])).toEqual(['fatigue']);
  });

  it('archived groups keep stored mappings but leave the active organization and badges', () => {
    const value = data();
    value.groups[0].archived = true;
    expect(groupBadges('numb', value)).toEqual([]);
    expect(filterByGroup(['numb'], UNGROUPED_ID, value)).toEqual(['numb']);
    expect(groupedSections(['numb'], value)).toEqual([{ id: UNGROUPED_ID, title: 'Без групи', symptomIds: ['numb'] }]);
    expect(value.symptomGroupIds.numb).toEqual(['a']);
  });

  it('unfinished drafts alone do not make an archived symptom trendable', () => {
    const value = data();
    value.entries = { '2026-07-21': { status: 'draft', d: {
      wb: null, wbSkip: false, sel: ['headache'], sym: { headache: { int: 4 } }, absent: [], groupId: null,
      ctx: emptyCtx(), note: '', flare: null, confirmed: false, noSymptoms: false, step: 3
    } } };
    expect(trendableSymptomIds(value)).not.toContain('headache');
  });
});
