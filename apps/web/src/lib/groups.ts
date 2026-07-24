import { SYM } from '../constants/symptoms';
import { isoOff } from './dates';
import type { AppData, Entry, TrackingGroup } from './types';

export const UNGROUPED_ID = '__ungrouped__';

export function uniqueIds(ids: readonly string[]): string[] {
  return [...new Set(ids.filter(Boolean))];
}

export function visibleGroups(data: Pick<AppData, 'groups'>): TrackingGroup[] {
  return data.groups.filter((group) => !group.archived);
}

export function validGroupIds(data: Pick<AppData, 'groups'>): Set<string> {
  return new Set(data.groups.map((group) => group.id));
}

export function symptomGroups(
  symptomId: string,
  data: Pick<AppData, 'groups' | 'symptomGroupIds'>
): TrackingGroup[] {
  const ids = new Set(data.symptomGroupIds[symptomId] || []);
  return data.groups.filter((group) => !group.archived && ids.has(group.id));
}

export function groupBadges(
  symptomId: string,
  data: Pick<AppData, 'groups' | 'symptomGroupIds'>
): string[] {
  return symptomGroups(symptomId, data).map((group) => group.name);
}

export function isInGroup(
  symptomId: string,
  groupId: string | null,
  data: Pick<AppData, 'groups' | 'symptomGroupIds'>
): boolean {
  if (!groupId) return true;
  const memberships = (data.symptomGroupIds[symptomId] || []).filter((id) =>
    data.groups.some((group) => group.id === id && !group.archived)
  );
  return groupId === UNGROUPED_ID ? memberships.length === 0 : memberships.includes(groupId);
}

export function filterByGroup(
  ids: readonly string[],
  groupId: string | null,
  data: Pick<AppData, 'groups' | 'symptomGroupIds'>
): string[] {
  return uniqueIds(ids).filter((id) => isInGroup(id, groupId, data));
}

export function filterByGroups(
  ids: readonly string[],
  groupIds: readonly string[],
  data: Pick<AppData, 'groups' | 'symptomGroupIds'>
): string[] {
  const filters = uniqueIds(groupIds);
  if (!filters.length) return uniqueIds(ids);
  return uniqueIds(ids).filter((id) => filters.some((groupId) => isInGroup(id, groupId, data)));
}

export interface GroupSection {
  id: string;
  title: string;
  symptomIds: string[];
}

/** Builds scan-friendly sections while guaranteeing that each symptom id occurs once. */
export function groupedSections(
  ids: readonly string[],
  data: Pick<AppData, 'groups' | 'symptomGroupIds'>,
  groupId: string | null = null
): GroupSection[] {
  const filtered = filterByGroup(ids, groupId, data);
  if (groupId) {
    const title = groupId === UNGROUPED_ID
      ? 'Без групи'
      : data.groups.find((group) => group.id === groupId)?.name || 'Група';
    return filtered.length ? [{ id: groupId, title, symptomIds: filtered }] : [];
  }

  const sections = new Map<string, GroupSection>();
  const add = (id: string, title: string, symptomId: string) => {
    const section = sections.get(id) || { id, title, symptomIds: [] };
    section.symptomIds.push(symptomId);
    sections.set(id, section);
  };

  filtered.forEach((symptomId) => {
    const memberships = symptomGroups(symptomId, data);
    if (memberships.length > 1) add('__shared__', 'Спільні для кількох груп', symptomId);
    else if (memberships.length === 1) add(memberships[0].id, memberships[0].name, symptomId);
    else add(UNGROUPED_ID, 'Без групи', symptomId);
  });

  const order = new Map(data.groups.map((group, index) => [group.id, index]));
  return [...sections.values()].sort((a, b) => {
    if (a.id === '__shared__') return -1;
    if (b.id === '__shared__') return 1;
    if (a.id === UNGROUPED_ID) return 1;
    if (b.id === UNGROUPED_ID) return -1;
    return (order.get(a.id) ?? 0) - (order.get(b.id) ?? 0);
  });
}

export function historicalSymptomIds(entries: Record<string, Entry>): string[] {
  const ids: string[] = [];
  Object.values(entries).forEach((entry) => {
    if (entry.status === 'done') ids.push(...Object.keys(entry.sym || {}), ...(entry.absent || []));
  });
  return uniqueIds(ids);
}

export function knownSymptomIdsInPeriod(
  entries: Record<string, Entry>,
  period: number,
  now: Date
): string[] {
  const ids: string[] = [];
  for (let offset = 0; offset > -period; offset--) {
    const entry = entries[isoOff(now, offset)];
    if (entry?.status === 'done') ids.push(...Object.keys(entry.sym || {}), ...(entry.absent || []));
  }
  return uniqueIds(ids);
}

export function knownSymptomIds(data: Pick<AppData, 'custom'>): Set<string> {
  return new Set([...SYM.map((symptom) => symptom.id), ...data.custom.map((symptom) => symptom.id)]);
}

/** Active first, then archived/historical ids that have known observations in the period. */
export function reportableSymptomIds(
  data: AppData,
  period: number,
  now: Date,
  groupIds: readonly string[] = []
): string[] {
  const knownDefs = knownSymptomIds(data);
  const observed = new Set(knownSymptomIdsInPeriod(data.entries, period, now));
  const ids = uniqueIds([
    ...data.active,
    ...data.archived.filter((id) => observed.has(id)),
    ...historicalSymptomIds(data.entries).filter((id) => observed.has(id))
  ]).filter((id) => knownDefs.has(id) && (data.active.includes(id) || observed.has(id)));
  return filterByGroups(ids, groupIds, data);
}

export function trendableSymptomIds(data: AppData): string[] {
  const knownDefs = knownSymptomIds(data);
  const historical = historicalSymptomIds(data.entries);
  return uniqueIds([...data.active, ...data.archived, ...historical])
    .filter((id) => knownDefs.has(id) && (data.active.includes(id) || historical.includes(id)));
}

export function normalizeReportSelection(selection: readonly string[], available: readonly string[]): string[] {
  const allowed = new Set(available);
  return uniqueIds(selection).filter((id) => allowed.has(id));
}

export function cleanSymptomGroupIds(
  mappings: Record<string, string[]> | null | undefined,
  groups: readonly TrackingGroup[]
): Record<string, string[]> {
  const valid = new Set(groups.map((group) => group.id));
  return Object.fromEntries(
    Object.entries(mappings || {})
      .map(([symptomId, ids]) => [symptomId, uniqueIds(Array.isArray(ids) ? ids : []).filter((id) => valid.has(id))])
      .filter(([, ids]) => ids.length > 0)
  );
}

export function validateGroupName(name: string, groups: readonly TrackingGroup[], ignoreId?: string): string {
  const trimmed = name.trim();
  if (!trimmed) return 'Введіть назву групи.';
  if ([...trimmed].length > 60) return 'Назва має містити не більше 60 символів.';
  const folded = trimmed.toLocaleLowerCase('uk-UA');
  if (groups.some((group) => group.id !== ignoreId && group.name.trim().toLocaleLowerCase('uk-UA') === folded)) {
    return 'Група з такою назвою вже існує.';
  }
  return '';
}
