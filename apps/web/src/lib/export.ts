import type { AppData, AppState } from './types';
import { groupBadges, uniqueIds } from './groups';

function csvCell(value: unknown) {
  const text = value == null ? '' : (typeof value === 'string' ? value : JSON.stringify(value));
  return `"${text.replaceAll('"', '""')}"`;
}

export function dataToCsv(data: AppData) {
  const header = [
    'date', 'status', 'wellbeing', 'confirmed', 'noSymptoms', 'symptoms', 'context', 'note', 'flare',
    'absentSymptoms', 'symptomGroups'
  ];
  const rows = Object.entries(data.entries).sort(([a], [b]) => a.localeCompare(b)).map(([date, entry]) => {
    const value = entry.status === 'draft' ? entry.d : entry;
    const symptoms = entry.status === 'draft'
      ? Object.fromEntries(uniqueIds(entry.d.sel || []).map((id) => [id, entry.d.sym[id] || {}]))
      : entry.sym;
    const symptomIds = uniqueIds([...Object.keys(symptoms || {}), ...(value.absent || [])]);
    const symptomGroups = Object.fromEntries(symptomIds.map((id) => [id, groupBadges(id, data)]));
    return [
      date,
      entry.status,
      value.wb,
      value.confirmed,
      value.noSymptoms,
      symptoms,
      value.ctx,
      value.note,
      value.flare,
      value.absent,
      symptomGroups
    ].map(csvCell).join(',');
  });
  return [header.join(','), ...rows].join('\r\n');
}

export function stateToJson(state: AppState) {
  const persisted: Partial<AppState> = { ...state };
  delete persisted.dialog;
  delete persisted.toast;
  return JSON.stringify(persisted, null, 2);
}

function download(contents: string, mime: string, extension: 'csv' | 'json') {
  const url = URL.createObjectURL(new Blob([contents], { type: mime }));
  const link = document.createElement('a');
  link.href = url;
  link.download = `neuro-diary-${new Date().toISOString().slice(0, 10)}.${extension}`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function downloadCsv(data: AppData) {
  download('\uFEFF' + dataToCsv(data), 'text/csv;charset=utf-8', 'csv');
}

export function downloadJson(state: AppState) {
  download(stateToJson(state), 'application/json', 'json');
}
