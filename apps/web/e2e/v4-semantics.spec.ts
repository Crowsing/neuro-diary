import { expect, test, type Page } from '@playwright/test';
import {
  APP_URL,
  demoData,
  gotoWithBrokenStorage,
  readState,
  seedState
} from './helpers';

const ctx = {
  stress: null,
  sleepQ: null,
  sleepH: null,
  activity: null,
  actType: '',
  heat: null,
  extras: []
};

function done(
  sym: Record<string, { int?: number; side?: string }> = {},
  absent: string[] = [],
  wb: number | null = 5
) {
  return {
    status: 'done' as const,
    wb,
    sym,
    absent,
    ctx,
    note: '',
    flare: null,
    noSymptoms: false,
    filledLater: false
  };
}

async function gotoData(
  page: Page,
  dataPatch: Record<string, unknown>,
  statePatch: Record<string, unknown> = {}
) {
  const data = {
    ...demoData(),
    entries: {},
    cycleStarts: [],
    active: [],
    archived: [],
    groups: [],
    symptomGroupIds: {},
    ...dataPatch
  };
  await seedState(page, { view: 'app', data, ...statePatch });
  await page.goto(APP_URL);
}

test('v4 onboarding: дві групи можуть явно ділити один symptom ID', async ({ page }) => {
  await page.goto(APP_URL);
  await page.getByRole('button', { name: 'Далі', exact: true }).click();
  await page.getByRole('button', { name: 'Далі', exact: true }).click();

  await page.getByLabel('Назва групи').fill('Група A');
  await page.getByRole('button', { name: 'Додати групу' }).click();
  await page.getByLabel('Назва групи').fill('Група B');
  await page.getByRole('button', { name: 'Додати групу' }).click();
  await page.getByRole('button', { name: 'Далі', exact: true }).click();

  const fatigue = page.getByRole('button', { name: 'Втома', exact: true });
  await expect(fatigue).toHaveCount(1);
  await fatigue.click();
  await page.getByRole('button', { name: 'Група A для симптому «Втома»' }).click();
  await page.getByRole('button', { name: 'Група B для симптому «Втома»' }).click();
  await page.getByRole('button', { name: 'Пропустити решту налаштувань' }).click();

  await expect(page.getByRole('heading', { name: 'Четвер, 15 січня' })).toBeVisible();
  await expect.poll(async () => (await readState(page)).data.groups.length).toBe(2);
  const stored = await readState(page);
  expect(stored.data.active).toEqual(['fatigue']);
  expect(stored.data.symptomGroupIds.fatigue).toHaveLength(2);
  expect(new Set(stored.data.symptomGroupIds.fatigue).size).toBe(2);
});

test('v4 check-in: shared symptom іде один раз, а absence обмежена вибраною групою', async ({ page }) => {
  await gotoData(page, {
    active: ['fatigue', 'armWeak', 'headache'],
    groups: [
      { id: 'a', name: 'Група A', archived: false },
      { id: 'b', name: 'Група B', archived: false }
    ],
    symptomGroupIds: { fatigue: ['a', 'b'], armWeak: ['a'], headache: ['b'] }
  });

  await page.getByRole('button', { name: 'Заповнити день', exact: true }).click();
  await page.getByRole('button', { name: 'Далі', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Втома', exact: true })).toHaveCount(1);
  await expect(page.getByRole('heading', { name: 'Спільні для кількох груп' })).toBeVisible();
  await page.getByRole('button', { name: 'Група A', exact: true }).click();
  await page.getByRole('button', { name: 'Втома', exact: true }).click();
  await page.getByRole('button', { name: 'Далі', exact: true }).click();

  await expect(page.getByText('Симптом 1 із 1')).toBeVisible();
  await page.getByRole('button', { name: 'Інтенсивність Втома: 3 із 5, помірно' }).click();
  await page.getByRole('button', { name: 'Далі', exact: true }).click();

  const absenceCard = page.locator('.card', { hasText: 'Можна явно підтвердити:' }).last();
  await expect(absenceCard).toContainText('Слабкість у руці/руках');
  await expect(absenceCard).not.toContainText('Головний біль');
  await page.getByRole('checkbox', { name: /Підтвердити відсутність у показаній секції/ }).click();
  await page.getByRole('button', { name: 'Завершити запис' }).click();

  const entry = (await readState(page)).data.entries['2026-01-15'];
  expect(Object.keys(entry.sym)).toEqual(['fatigue']);
  expect(entry.absent).toEqual(['armWeak']);
  expect(entry.absent).not.toContain('headache');
});

test('v4 quick action snapshot-ить усі unique active IDs як explicit absent', async ({ page }) => {
  await gotoData(page, { active: ['fatigue', 'armWeak', 'fatigue'] });
  await page.getByRole('button', { name: 'Сьогодні не було жодного з відстежуваних симптомів' }).click();
  await expect(page.getByText('Завершено', { exact: true })).toBeVisible();

  const entry = (await readState(page)).data.entries['2026-01-15'];
  expect(entry.sym).toEqual({});
  expect(entry.absent).toEqual(['fatigue', 'armWeak']);
  expect(entry.noSymptoms).toBe(true);
});

test('v4 regroup не змінює history/trends, archived symptom лишається в Trends і Report', async ({ page }) => {
  await gotoData(page, {
    entries: { '2026-01-14': done({ fatigue: { int: 4 } }, ['armWeak']) },
    active: ['fatigue'],
    archived: ['armWeak'],
    groups: [
      { id: 'a', name: 'Група A', archived: false },
      { id: 'b', name: 'Група B', archived: false }
    ],
    symptomGroupIds: { fatigue: ['a'], armWeak: ['a'] }
  });

  await page.getByRole('button', { name: 'Динаміка', exact: true }).click();
  const fatigueTrend = page.getByRole('button', { name: /^Втома / });
  const before = await fatigueTrend.locator('.text-muted').textContent();
  const archivedTrend = page.getByRole('button', { name: /^Слабкість у руці\/руках / });
  await expect(archivedTrend.getByText('Історичний')).toBeVisible();

  await page.getByRole('button', { name: 'Налаштування', exact: true }).click();
  await page.getByRole('button', { name: /Мої симптоми/ }).click();
  await page.getByRole('button', { name: 'Додати до групи «Група B» симптом «Втома»' }).click();

  const persisted = await readState(page);
  expect(persisted.data.entries['2026-01-14'].sym.fatigue.int).toBe(4);
  expect(persisted.data.entries['2026-01-14'].absent).toEqual(['armWeak']);

  await page.getByRole('button', { name: 'Назад', exact: true }).click();
  await page.getByRole('button', { name: 'Динаміка', exact: true }).click();
  await expect(page.getByRole('button', { name: /^Втома / }).locator('.text-muted')).toHaveText(before || '');

  await page.getByRole('button', { name: 'Історія', exact: true }).click();
  await page.getByRole('button', { name: /Середа, 14 січня/ }).click();
  await expect(page.getByText('4/5 — сильно')).toBeVisible();
  await expect(page.getByText('Група B', { exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Назад до історії' }).click();
  await page.getByRole('button', { name: 'Звіт', exact: true }).click();
  await page.getByRole('button', { name: 'Далі: вибір даних' }).click();
  await expect(page.getByText('Архівні з відомими спостереженнями за період')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Слабкість у руці/руках · Група A' })).toBeVisible();
});

test('v4 done edit: past copy чесна, exit підтверджується і не створює false toast', async ({ page }) => {
  await gotoData(page, {
    entries: { '2026-01-14': done({ fatigue: { int: 2 } }, [], 5) },
    active: ['fatigue']
  });

  await page.getByRole('button', { name: 'Історія', exact: true }).click();
  await page.getByRole('button', { name: /Середа, 14 січня/ }).click();
  await page.getByRole('button', { name: 'Редагувати запис' }).click();
  await page.getByRole('button', { name: 'Змінити самопочуття' }).click();
  await expect(page.getByText('Як ви загалом почувалися цього дня?')).toBeVisible();
  await expect(page.getByText(/почувалися сьогодні/)).toHaveCount(0);
  await page.getByRole('button', { name: '8 із 10', exact: true }).click();
  await page.getByRole('button', { name: 'Скасувати редагування' }).last().click();

  const dialog = page.getByRole('dialog', { name: 'Вийти без збереження змін?' });
  await expect(dialog).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.activeElement?.textContent?.trim())).toBe('Продовжити редагування');
  await dialog.getByRole('button', { name: 'Вийти без збереження' }).click();

  await expect(page.getByRole('heading', { name: 'Середа, 14 січня' })).toBeVisible();
  expect((await readState(page)).data.entries['2026-01-14'].wb).toBe(5);
  await expect(page.getByRole('status')).toHaveCount(0);
});

test('v4 required intensity блокує/focus-ить; completed unknown має точний label', async ({ page }) => {
  await gotoData(page, {
    entries: { '2026-01-14': done({}, []) },
    active: ['fatigue']
  });

  await page.getByRole('button', { name: 'Заповнити день', exact: true }).click();
  await page.getByRole('button', { name: 'Далі', exact: true }).click();
  await page.getByRole('button', { name: 'Втома', exact: true }).click();
  await page.getByRole('button', { name: 'Далі', exact: true }).click();
  await page.getByRole('button', { name: 'Далі', exact: true }).click();

  await expect(page.getByRole('alert')).toHaveText('Оберіть інтенсивність, щоб продовжити.');
  await expect.poll(() => page.evaluate(() => document.activeElement?.id)).toBe('intensity-fatigue');
  await page.getByRole('button', { name: 'Інтенсивність Втома: 3 із 5, помірно' }).click();
  await page.getByRole('button', { name: 'Далі', exact: true }).click();
  await page.getByRole('button', { name: 'Завершити запис' }).click();

  await page.getByRole('button', { name: 'Динаміка', exact: true }).click();
  await page.getByRole('button', { name: /^Втома / }).click();
  await page.getByRole('button', { name: 'Таблиця', exact: true }).click();
  await expect(page.getByRole('button', { name: /^14\.01: симптом не заповнено цього дня/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /^13\.01: день не заповнено/ })).toBeVisible();
});

test('v4 report dedup-ить shared symptom, приховує group names та не persist-ить identity', async ({ page }) => {
  await gotoData(page, {
    entries: { '2026-01-14': done({ fatigue: { int: 4 } }) },
    active: ['fatigue'],
    groups: [
      { id: 'a', name: 'Конфіденційна група A', archived: false },
      { id: 'b', name: 'Конфіденційна група B', archived: false }
    ],
    symptomGroupIds: { fatigue: ['a', 'b'] }
  });

  await page.getByRole('button', { name: 'Звіт', exact: true }).click();
  await page.getByRole('button', { name: 'Далі: вибір даних' }).click();
  await page.getByRole('button', { name: 'Конфіденційна група A', exact: true }).click();
  const fatigue = page.getByRole('button', { name: 'Втома · Конфіденційна група A, Конфіденційна група B' });
  await expect(fatigue).toHaveCount(1);
  await fatigue.click();
  await expect(page.getByRole('switch', { name: 'Показувати назви груп' })).toHaveAttribute('aria-checked', 'false');
  await page.getByLabel('Імʼя').fill('Олена Тестова');
  await page.getByLabel('Дата народження').fill('1990-05-06');
  await page.getByRole('button', { name: 'Переглянути PDF' }).click();

  const preview = page.locator('.nd-print-report');
  await expect(preview).toContainText('Олена Тестова');
  await expect(preview).not.toContainText('Конфіденційна група A');
  await expect(preview).not.toContainText('Конфіденційна група B');
  const stored = await readState(page);
  expect(stored.report.name).toBe('');
  expect(stored.report.dob).toBe('');

  await page.reload();
  await expect(page.locator('.nd-print-report')).not.toContainText('Олена Тестова');
});

test('v4 storage failure зберігає in-memory state, ховає success toast і дає recovery export', async ({ page }) => {
  const data = { ...demoData(), entries: {}, active: ['fatigue'] };
  await gotoWithBrokenStorage(page, { view: 'app', data });

  const alert = page.getByRole('alert');
  await expect(alert).toContainText('Не вдалося зберегти зміни в браузері');
  await page.getByRole('button', { name: 'Сьогодні не було жодного з відстежуваних симптомів' }).click();
  await expect(page.getByText('Завершено', { exact: true })).toBeVisible();
  await expect(page.getByRole('status')).toHaveCount(0);
  await expect(alert).toBeVisible();

  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Експортувати JSON для відновлення' }).click();
  await expect(download).resolves.toBeTruthy();
  await page.getByRole('button', { name: 'Повторити збереження' }).click();
  await expect(alert).toBeVisible();
});

test('v4 future dates недоступні; mood=5 crisis не має fake contact CTA в обох branches', async ({ page }) => {
  await gotoData(page, { active: ['mood'] });
  await page.getByRole('button', { name: 'Історія', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Наступний місяць' })).toBeDisabled();
  await expect(page.getByRole('button', { name: /16 січня/ })).toHaveCount(0);

  await page.getByRole('button', { name: 'Сьогодні', exact: true }).click();
  await page.getByRole('button', { name: 'Заповнити день', exact: true }).click();
  await page.getByRole('button', { name: 'Далі', exact: true }).click();
  await page.getByRole('button', { name: 'Пригніченість настрою', exact: true }).click();
  await page.getByRole('button', { name: 'Далі', exact: true }).click();
  await page.getByRole('button', { name: 'Інтенсивність Пригніченість настрою: 5 із 5, дуже сильно, суттєво заважає' }).click();
  await page.getByRole('button', { name: 'Потрібна підтримка зараз' }).click();
  await expect(page.getByText('Звʼязатися з довіреною людиною')).toHaveCount(0);

  await page.getByRole('button', { name: 'Так', exact: true }).click();
  await expect(page.getByRole('link', { name: 'Подзвонити 112' })).toHaveAttribute('href', 'tel:112');
  await expect(page.getByText('Звʼязатися з довіреною людиною')).toHaveCount(0);
  await page.getByRole('button', { name: 'Назад', exact: true }).click();
  await page.getByRole('button', { name: 'Потрібна підтримка зараз' }).click();
  await page.getByRole('button', { name: 'Ні', exact: true }).click();
  await expect(page.getByText('Звʼязатися з довіреною людиною')).toHaveCount(0);
});

test('v4 delete scope: group/cycle delete зберігають observations, full delete очищає все', async ({ page }) => {
  await gotoData(page, {
    entries: { '2026-01-14': done({ fatigue: { int: 4 } }, ['armWeak']) },
    cycleStarts: ['2026-01-03'],
    active: ['fatigue', 'armWeak'],
    groups: [
      { id: 'a', name: 'Група A', archived: false },
      { id: 'b', name: 'Група B', archived: false }
    ],
    symptomGroupIds: { fatigue: ['a', 'b'], armWeak: ['a'] }
  });

  await page.getByRole('button', { name: 'Налаштування', exact: true }).click();
  await page.getByRole('button', { name: /Групи спостереження/ }).click();
  await page.getByRole('button', { name: 'Видалити групу «Група A»' }).click();
  await expect.poll(() => page.evaluate(() => document.activeElement?.textContent?.trim())).toBe('Скасувати');
  await page.getByRole('dialog').getByRole('button', { name: 'Видалити групу' }).click();

  let stored = await readState(page);
  expect(stored.data.groups.map((group: { id: string }) => group.id)).toEqual(['b']);
  expect(stored.data.symptomGroupIds).toEqual({ fatigue: ['b'] });
  expect(stored.data.entries['2026-01-14'].sym.fatigue.int).toBe(4);

  await page.getByRole('button', { name: 'Назад до налаштувань' }).click();
  await page.getByRole('button', { name: 'Видалення даних' }).click();
  await expect.poll(() => page.evaluate(() => document.activeElement?.textContent?.trim())).toBe('Закрити');
  await page.getByRole('button', { name: 'Видалити лише дані циклу' }).click();
  stored = await readState(page);
  expect(stored.data.cycleStarts).toEqual([]);
  expect(stored.data.groups).toHaveLength(1);
  expect(stored.data.entries['2026-01-14']).toBeTruthy();

  await page.getByRole('button', { name: 'Видалення даних' }).click();
  await page.getByRole('button', { name: 'Видалити всі дані' }).click();
  stored = await readState(page);
  expect(stored.data.entries).toEqual({});
  expect(stored.data.groups).toEqual([]);
  expect(stored.data.symptomGroupIds).toEqual({});
  expect(stored.data.active).toEqual([]);
  expect(stored.checkin).toBeNull();
  expect(stored.report.syms).toEqual([]);
  expect(stored.report.groupIds).toEqual([]);
});
