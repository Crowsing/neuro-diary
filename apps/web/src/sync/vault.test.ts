// Наскрізні тести сесії сейфа.
//
// Жоден із них не конструює тіло запису руками. Кожен іде тим самим шляхом, що
// й застосунок: домен → `toRecords` → шифрування → чанки → «сервер» → pull →
// розшифрування → merge → патч домену. Саме на цьому обпеклася попередня сесія:
// тести, що перевіряють реалізацію самою реалізацією, пропустили два дефекти
// рівня «зупинка».
//
// «Сервер» тут — `FakeVaultServer`: маленька реалізація протоколу §9.1–9.2 з
// монотонними ревізіями, per-key конфліктом, горизонтом компакшну і CAS
// конверта, а не набір заздалегідь записаних відповідей.

import { describe, expect, it } from 'vitest';
import { fromBase64 } from '../crypto/bytes';
import { payloadDigest } from '../crypto/manifest';
import { emptyCtx } from '../lib/checkin';
import type { AppData, DoneEntry } from '../lib/types';
import { emptyData } from '../state/persist';
import type { GrantBody } from './client';
import type { BrowserEnv } from './initdata';
import { resetInitDataForTests } from './initdata';
import { FakeVaultServer, FakeVaultTransport } from './testing/fakeVault';
import { VaultError, VaultSession } from './vault';

const T0 = 1_768_435_200_000;
const MINUTE = 60_000;
const PASSPHRASE = 'сіль вітер камінь дорога вечір поле';

const GRANT: GrantBody = {
  kind: 'health_sync',
  text_version: '0.9',
  text_sha256: 'a'.repeat(64)
};

function done(note = 'нотатка'): DoneEntry {
  return {
    status: 'done',
    wb: 3,
    sym: { fatigue: { int: 2 } },
    absent: ['nausea'],
    ctx: emptyCtx(),
    note,
    flare: null,
    noSymptoms: false,
    filledLater: false
  };
}

function sample(): AppData {
  return {
    ...emptyData(),
    entries: { '2026-01-15': done(), '2026-01-16': done('друга') },
    cycleStarts: ['2026-01-02'],
    active: ['fatigue', 'nausea'],
    archived: ['tremor'],
    custom: [{ id: 'own', name: 'Власний', type: 'bool' }],
    groups: [{ id: 'g1', name: 'Мігрень', archived: false }],
    symptomGroupIds: { fatigue: ['g1'] },
    cycleOn: true,
    lock: true
  };
}

class MemoryStorage {
  private readonly map = new Map<string, string>();
  getItem(key: string): string | null {
    return this.map.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.map.set(key, value);
  }
  removeItem(key: string): void {
    this.map.delete(key);
  }
}

/**
 * Один «профіль браузера»: власне сховище і власний initData.
 *
 * initData у модулі кешується один раз за життя вкладки, тож між пристроями
 * кеш скидається — це і є модель другої вкладки, а не обхід гігієни §8.
 */
function newDevice(server: FakeVaultServer, initData: string) {
  resetInitDataForTests();
  const browser: BrowserEnv = {
    location: { hash: `#tgWebAppData=${initData}`, href: 'https://example.test/' },
    history: { replaceState: () => undefined },
    sessionStorage: { getItem: () => null, removeItem: () => undefined }
  };
  const transport = new FakeVaultTransport(server, initData);
  const storage = new MemoryStorage();
  return {
    transport,
    storage,
    session: VaultSession.create({ transport, storage, browser })
  };
}

const CYCLE_GRANT: GrantBody = {
  kind: 'cycle_sync',
  text_version: '0.9',
  text_sha256: 'b'.repeat(64)
};

/**
 * Пара пристроїв зі увімкненою синхронізацією.
 *
 * `cycleSync` за замовчуванням вимкнена — це штатний стан §4.3, і саме тому
 * записи циклу за замовчуванням не покидають пристрій (§9.7).
 */
async function enabledPair(data: AppData = sample(), options: { cycleSync?: boolean } = {}) {
  const server = new FakeVaultServer();
  const a = newDevice(server, 'init-a');
  await a.session.enable({ grant: GRANT, passphrase: PASSPHRASE, data, nowMs: T0 });
  if (options.cycleSync === true) {
    await a.session.grantCycleSync(CYCLE_GRANT);
    await a.session.push({ data, nowMs: T0 + 1000 });
  }
  const b = newDevice(server, 'init-b');
  return { server, a, b };
}

describe('два пристрої сходяться через справжній сервер', () => {
  it('другий пристрій відновлює щоденник першого', async () => {
    const data = sample();
    // Обидві згоди — інакше цикл законно не покидає пристрій (§9.7), і тест
    // перевіряв би менше, ніж заявляє в назві.
    const { b } = await enabledPair(data, { cycleSync: true });

    await b.session.open({ passphrase: PASSPHRASE, nowMs: T0 + MINUTE });
    const outcome = await b.session.pull({ data: emptyData(), nowMs: T0 + MINUTE });

    expect(outcome.patch.entries).toEqual(data.entries);
    expect(outcome.patch.cycleStarts).toEqual(data.cycleStarts);
    expect(outcome.patch.groups).toEqual(data.groups);
    expect(outcome.patch.custom).toEqual(data.custom);
    expect(outcome.patch.cycleOn).toBe(true);
    // `lock` — локальна демо-властивість і сейфу не належить (§6.1).
    expect('lock' in outcome.patch).toBe(false);
  });

  it('правка другого пристрою повертається на перший', async () => {
    const { a, b } = await enabledPair();

    await b.session.open({ passphrase: PASSPHRASE, nowMs: T0 + MINUTE });
    const first = await b.session.pull({ data: emptyData(), nowMs: T0 + MINUTE });

    const edited: AppData = {
      ...emptyData(),
      ...first.patch,
      entries: { ...first.patch.entries, '2026-01-15': done('виправлено на B') }
    };
    await b.session.push({ data: edited, nowMs: T0 + 2 * MINUTE });

    const back = await a.session.pull({ data: sample(), nowMs: T0 + 3 * MINUTE });
    expect(back.patch.entries?.['2026-01-15']).toMatchObject({ note: 'виправлено на B' });
  });

  it('видалення на першому пристрої не воскресає на другому', async () => {
    const { a, b } = await enabledPair();

    await b.session.open({ passphrase: PASSPHRASE, nowMs: T0 + MINUTE });
    await b.session.pull({ data: emptyData(), nowMs: T0 + MINUTE });

    const withoutDay: AppData = { ...sample(), entries: { '2026-01-16': done('друга') } };
    await a.session.push({ data: withoutDay, nowMs: T0 + 2 * MINUTE });

    const seen = await b.session.pull({ data: sample(), nowMs: T0 + 3 * MINUTE });
    expect(Object.keys(seen.patch.entries ?? {})).toEqual(['2026-01-16']);
  });
});

describe('§7 на шляху pull, а не лише в unit-тесті', () => {
  it('відхиляє запис, поданий під чужим record_key', async () => {
    const { server, b } = await enabledPair();
    const anyKey = [...server.records.keys()][0];
    server.faults.serveUnderForeignKey = { from: anyKey, to: 'f'.repeat(64) };

    await b.session.open({ passphrase: PASSPHRASE, nowMs: T0 + MINUTE });
    const local = sample();
    await expect(
      b.session.pull({ data: local, nowMs: T0 + MINUTE })
    ).rejects.toMatchObject({ failure: 'integrity' });
    expect(local).toEqual(sample());
  });

  it('відхиляє запис, чий client_ts не збігається з AAD', async () => {
    const { server, b } = await enabledPair();
    const entryKey = [...server.records.entries()].find(
      ([, record]) => record.payloadB64 !== null
    )![0];
    server.faults.tamperClientTsKeys = [entryKey];

    await b.session.open({ passphrase: PASSPHRASE, nowMs: T0 + MINUTE });
    await expect(
      b.session.pull({ data: emptyData(), nowMs: T0 + MINUTE })
    ).rejects.toMatchObject({ failure: 'integrity' });
  });

  it('відхиляє параметри KDF, нижчі за підлогу, ДО роботи з фразою', async () => {
    const { server, b } = await enabledPair();
    server.faults.weakenKdfParams = true;

    await expect(
      b.session.open({ passphrase: PASSPHRASE, nowMs: T0 + MINUTE })
    ).rejects.toMatchObject({ failure: 'kdf_params_rejected' });
  });

  it('відхиляє відповідь із нижчою ревізією, ніж уже бачив пристрій', async () => {
    const { server, a } = await enabledPair();
    server.faults.reportRevisionAs = 0;

    await expect(a.session.pull({ data: sample(), nowMs: T0 + MINUTE })).rejects.toMatchObject({
      failure: 'stale_server_copy'
    });
  });

  it('падає на manifest, коли сервер приховав живий запис, і не чіпає локальних даних', async () => {
    const { server, b } = await enabledPair();
    const liveKey = [...server.records.entries()].find(
      ([, record]) => record.payloadB64 !== null
    )![0];
    server.faults.withholdRecordKeys = [liveKey];

    await b.session.open({ passphrase: PASSPHRASE, nowMs: T0 + MINUTE });
    const local = sample();
    await expect(
      b.session.pull({ data: local, nowMs: T0 + MINUTE })
    ).rejects.toMatchObject({ failure: 'stale_server_copy' });
    expect(local).toEqual(sample());
  });

  it('проходить, коли зник саме скомпактований надгробок', async () => {
    const { server, a, b } = await enabledPair();

    const withoutDay: AppData = { ...sample(), entries: { '2026-01-16': done('друга') } };
    await a.session.push({ data: withoutDay, nowMs: T0 + MINUTE });
    server.compact();

    await b.session.open({ passphrase: PASSPHRASE, nowMs: T0 + 2 * MINUTE });
    const outcome = await b.session.pull({ data: emptyData(), nowMs: T0 + 2 * MINUTE });
    expect(Object.keys(outcome.patch.entries ?? {})).toEqual(['2026-01-16']);
  });
});

describe('§9.4 на реальному флоу', () => {
  it('не видаляє локальні дані циклу, коли згода cycle_sync неактивна', async () => {
    const server = new FakeVaultServer();
    const a = newDevice(server, 'init-a');
    await a.session.enable({
      grant: GRANT,
      passphrase: PASSPHRASE,
      data: sample(),
      nowMs: T0
    });
    // Згода дається справжнім шляхом: інакше A законно не надіслав би цикл, і
    // тест перевіряв би відсутність даних замість їхнього збереження.
    await a.session.grantCycleSync(CYCLE_GRANT);
    await a.session.push({ data: sample(), nowMs: T0 + 1000 });

    const b = newDevice(server, 'init-b');
    await b.session.open({ passphrase: PASSPHRASE, nowMs: T0 + MINUTE });
    const first = await b.session.pull({ data: emptyData(), nowMs: T0 + MINUTE });
    expect(first.patch.cycleStarts).toEqual(['2026-01-02']);

    // Згоду відкликано, і сервер видалив записи циклу власним hard-DELETE
    // (§9.7) — без надгробка, бо надгробок деанонімізував би цей record_key.
    server.consents.delete('cycle_sync');
    server.consentEpoch += 1;
    // A переписав увесь сейф (так буває після re-key), тож старих живих
    // ревізій більше немає і компакшн має право підняти горизонт вище за
    // ревізію, на якій зупинився B. Саме так B законно опиняється на 410 —
    // тобто на тому шляху, де §9.4 і стирав би локальні дані.
    const changed: AppData = { ...sample(), entries: { '2026-01-15': done('переписано') } };
    await a.session.push({ data: changed, nowMs: T0 + 90_000 });
    server.records.delete(await serverKeyOfPath(server, b.session, 'cycle'));
    server.raiseHorizonToLowestLive();

    const local: AppData = { ...emptyData(), ...first.patch } as AppData;
    const after = await b.session.pull({ data: local, nowMs: T0 + 2 * MINUTE });

    // Відкликання згоди на синхронізацію ніколи не видаляє локальних даних.
    expect(after.patch.cycleStarts).toEqual(['2026-01-02']);
    expect(after.prunePaths).not.toContain('cycle');
  });

  it('питає згоди сервера ПІСЛЯ pull, а не до нього', async () => {
    const { server, b } = await enabledPair();
    await b.session.open({ passphrase: PASSPHRASE, nowMs: T0 + MINUTE });
    server.calls.length = 0;
    await b.session.pull({ data: emptyData(), nowMs: T0 + MINUTE });

    const firstPull = server.calls.indexOf('pull');
    const consents = server.calls.indexOf('listConsents');
    expect(firstPull).toBeGreaterThanOrEqual(0);
    expect(consents).toBeGreaterThan(firstPull);
  });

  it('масове видалення потребує підтвердження на пристрої', async () => {
    const many: AppData = {
      ...emptyData(),
      entries: Object.fromEntries(
        Array.from({ length: 30 }, (_, index) => [
          `2026-03-${String(index + 1).padStart(2, '0')}`,
          done(`день ${index}`)
        ])
      )
    };
    const server = new FakeVaultServer();
    const a = newDevice(server, 'init-a');
    await a.session.enable({ grant: GRANT, passphrase: PASSPHRASE, data: many, nowMs: T0 });

    const b = newDevice(server, 'init-b');
    await b.session.open({ passphrase: PASSPHRASE, nowMs: T0 + MINUTE });
    await b.session.pull({ data: emptyData(), nowMs: T0 + MINUTE });

    const kept: AppData = {
      ...many,
      entries: Object.fromEntries(Object.entries(many.entries).slice(0, 10))
    };
    await a.session.push({ data: kept, nowMs: T0 + 2 * MINUTE });

    const outcome = await b.session.pull({ data: many, nowMs: T0 + 3 * MINUTE });
    expect(outcome.massDelete).toBe(true);
    expect(Object.keys(outcome.patch.entries ?? {})).toHaveLength(30);
    expect(Object.keys(outcome.confirmedPatch.entries ?? {})).toHaveLength(10);
  });
});

describe('§9.4: правило авторитетності присутності не видаляє без підтвердження', () => {
  /**
   * Найтяжчий сценарій: запис acked і clean, на сервері його немає, надгробка
   * теж немає (сервер переписали з нуля). Правило §9.4 називає це «видалено
   * віддалено» — але ЛИШЕ з підтвердженням на пристрої.
   */
  async function prunable() {
    const { server, a, b } = await enabledPair();
    await b.session.open({ passphrase: PASSPHRASE, nowMs: T0 + MINUTE });
    await b.session.pull({ data: emptyData(), nowMs: T0 + MINUTE });

    // A видаляє день (надгробок), потім переписує решту сейфа, і лише тоді
    // компактор має право прибрати надгробок і підняти горизонт. Так на сервері
    // законно з'являється стан «запису немає, і надгробка на нього теж» — рівно
    // той вхід, для якого §9.4 і вводить правило авторитетності присутності.
    const withoutDay: AppData = { ...sample(), entries: { '2026-01-15': done() } };
    await a.session.push({ data: withoutDay, nowMs: T0 + 2 * MINUTE });

    const allChanged: AppData = {
      ...withoutDay,
      entries: { '2026-01-15': done('переписано') },
      cycleStarts: ['2026-01-02', '2026-01-20'],
      active: ['fatigue', 'nausea', 'tremor'],
      archived: [],
      groups: [{ id: 'g1', name: 'Інша назва', archived: false }],
      cycleOn: false
    };
    await a.session.push({ data: allChanged, nowMs: T0 + 3 * MINUTE });
    server.compact();

    return { server, a, b };
  }

  it('безпечний патч ЛИШАЄ локальний запис, якого немає на сервері', async () => {
    const { b } = await prunable();
    const local = sample();
    const outcome = await b.session.pull({ data: local, nowMs: T0 + 3 * MINUTE });

    expect(outcome.prunePaths).toContain('entry:2026-01-16');
    // Патч застосовується одразу, до будь-якого діалогу, тож видалення тут —
    // це видалення без підтвердження.
    expect(Object.keys(outcome.patch.entries ?? {})).toContain('2026-01-16');
  });

  it('підтверджений патч видаляє його — і лише він', async () => {
    const { b } = await prunable();
    const outcome = await b.session.pull({ data: sample(), nowMs: T0 + 3 * MINUTE });

    expect(Object.keys(outcome.confirmedPatch.entries ?? {})).not.toContain('2026-01-16');
    expect(Object.keys(outcome.confirmedPatch.entries ?? {})).toContain('2026-01-15');
  });

  it('порожній серверний набір не витирає щоденник без підтвердження', async () => {
    const { server, b } = await enabledPair();
    await b.session.open({ passphrase: PASSPHRASE, nowMs: T0 + MINUTE });
    await b.session.pull({ data: emptyData(), nowMs: T0 + MINUTE });

    // Сервер після невдалого відновлення бекапа: записів немає, ревізія висока.
    server.records.clear();
    server.currentRevision += 1;
    server.compactedUpTo = server.currentRevision;

    const outcome = await b.session.pull({ data: sample(), nowMs: T0 + 2 * MINUTE });

    expect(Object.keys(outcome.patch.entries ?? {})).toEqual(['2026-01-15', '2026-01-16']);
    expect(outcome.prunePaths.length).toBeGreaterThan(0);
  });
});

describe('§9.4: надгробок синглтона й очищення сейфа', () => {
  it('надгробок cycle не змагається з оновленням того самого ключа', async () => {
    const { a, b } = await enabledPair(sample(), { cycleSync: true });

    // «Видалити лише дані циклу»: намір фіксується явно, бо порожня множина
    // виглядає так само, як прибрані по одній позначки.
    a.session.markTombstone('cycle');
    const withoutCycle: AppData = { ...sample(), cycleStarts: [] };
    await a.session.push({ data: withoutCycle, nowMs: T0 + MINUTE });

    // Manifest мусить лишитися узгодженим із сервером: інакше будь-який
    // повний ресинк давав би «серверна копія виглядає застарілою» назавжди.
    await b.session.open({ passphrase: PASSPHRASE, nowMs: T0 + 2 * MINUTE });
    const outcome = await b.session.pull({ data: emptyData(), nowMs: T0 + 2 * MINUTE });
    expect(outcome.patch.cycleStarts).toEqual([]);
  });

  it('очищення серверної копії не застосовується без підтвердження', async () => {
    const { server, a, b } = await enabledPair();
    await b.session.open({ passphrase: PASSPHRASE, nowMs: T0 + MINUTE });
    await b.session.pull({ data: emptyData(), nowMs: T0 + MINUTE });

    await a.session.resetVault();
    void server;

    const outcome = await b.session.pull({ data: sample(), nowMs: T0 + 2 * MINUTE });
    expect(outcome.vaultWasReset).toBe(true);
    expect(Object.keys(outcome.patch.entries ?? {})).toEqual(['2026-01-15', '2026-01-16']);
  });
});

describe('крайові стани домену', () => {
  it('порожній щоденник теж вмикає синхронізацію', async () => {
    // Онбординг не вимагає обрати жоден симптом, тож це не екзотика. Мітка
    // «нічого не втрачати» не має бути нулем: AAD §7 вимагає додатного
    // `client_ts_ms`, і нуль ламав би шифрування ще до першого запиту — вже
    // ПІСЛЯ того, як конверт записано на сервер.
    const server = new FakeVaultServer();
    const a = newDevice(server, 'init-empty');

    await a.session.enable({
      grant: GRANT,
      passphrase: PASSPHRASE,
      data: emptyData(),
      nowMs: T0
    });

    expect(server.liveCount()).toBeGreaterThan(0);
    expect(a.session.unlocked).toBe(true);
  });

  it('порожній щоденник не перемагає серверну копію в LWW', async () => {
    const { b } = await enabledPair(sample(), { cycleSync: true });
    await b.session.open({ passphrase: PASSPHRASE, nowMs: T0 + MINUTE });
    const outcome = await b.session.pull({ data: emptyData(), nowMs: T0 + MINUTE });
    expect(outcome.patch.cycleOn).toBe(true);
  });
});

describe('§9.7: згода на синхронізацію циклу', () => {
  /**
   * Фільтрація за згодою — клієнтська, і це не деталь реалізації: сервер не
   * бачить, який ciphertext є циклом, тож якщо клієнт включить `cycle` у push,
   * дані домену, згоди на синхронізацію якого не давали, опиняться в серверній
   * копії — і сервер навіть не матиме чим їх адресувати при відкликанні.
   */
  it('без згоди cycle_sync запис cycle не потрапляє на сервер', async () => {
    const { server, a } = await enabledPair();

    expect(server.consents.has('cycle_sync')).toBe(false);
    // Два дні, три синглтони (без `cycle`) і manifest.
    expect(server.liveCount()).toBe(6);
    expect(a.session.snapshotMeta.records.cycle).toBeUndefined();
  });

  it('після згоди cycle потрапляє на сервер наступним push', async () => {
    const { server, a } = await enabledPair();

    await a.session.grantCycleSync({
      kind: 'cycle_sync',
      text_version: '0.9',
      text_sha256: 'b'.repeat(64)
    });
    await a.session.push({ data: sample(), nowMs: T0 + MINUTE });

    expect(server.liveCount()).toBe(7);
    expect(a.session.snapshotMeta.records.cycle).toBeDefined();
  });

  it('надсилає record_key_cycle, і це саме HMAC(k_index, «cycle»)', async () => {
    const { server, a } = await enabledPair();

    await a.session.grantCycleSync({
      kind: 'cycle_sync',
      text_version: '0.9',
      text_sha256: 'b'.repeat(64)
    });
    await a.session.push({ data: sample(), nowMs: T0 + MINUTE });

    // Сервер отримав саме той ключ, під яким лежить запис `cycle`, — інакше
    // hard-DELETE Фази 3 видаляв би не те. Порівняння йде через дайджест
    // шифротексту, як і в решті тестів: шляхів сервер не знає.
    expect(server.recordKeyCycle).toBe(await serverKeyOfPath(server, a.session, 'cycle'));
    expect(server.recordKeyCycle).toMatch(/^[0-9a-f]{64}$/);
  });

  it('без відкритого сейфа ключ назвати нічим', async () => {
    const server = new FakeVaultServer();
    const b = newDevice(server, 'init-locked');
    await expect(
      b.session.grantCycleSync({
        kind: 'cycle_sync',
        text_version: '0.9',
        text_sha256: 'b'.repeat(64)
      })
    ).rejects.toMatchObject({ failure: 'locked' });
  });
});

describe('дзеркало чек-іна', () => {
  it('відкладає віддалений запис за дату, яку зараз заповнюють', async () => {
    const { a, b } = await enabledPair();
    await b.session.open({ passphrase: PASSPHRASE, nowMs: T0 + MINUTE });
    await b.session.pull({ data: emptyData(), nowMs: T0 + MINUTE });

    const edited: AppData = {
      ...sample(),
      entries: { ...sample().entries, '2026-01-15': done('правка з A') }
    };
    await a.session.push({ data: edited, nowMs: T0 + 2 * MINUTE });

    const outcome = await b.session.pull({
      data: sample(),
      nowMs: T0 + 3 * MINUTE,
      checkinDate: '2026-01-15'
    });

    expect(outcome.patch.entries?.['2026-01-15']).toMatchObject({ note: 'нотатка' });
    expect(outcome.deferredEntries['2026-01-15']).toMatchObject({ note: 'правка з A' });
  });
});

describe('§9.5: ретрай чанка після втраченої відповіді', () => {
  it('завершує вивантаження без дублів, коли відповідь на push загубилася', async () => {
    const server = new FakeVaultServer();
    const a = newDevice(server, 'init-a');
    server.faults.dropNextPushResponse = true;

    await a.session.enable({
      grant: GRANT,
      passphrase: PASSPHRASE,
      data: sample(),
      nowMs: T0
    });

    // Записів рівно стільки, скільки одиниць зберігання поїхало: два дні, три
    // синглтони (`cycle` лишився на пристрої без своєї згоди, §9.7) і manifest.
    // Дубля немає, бо чанк розпізнано як застосований.
    expect(server.liveCount()).toBe(6);
  });
});

describe('етап D: операції з ключем', () => {
  it('зміна фрази вимагає поточної', async () => {
    const { a } = await enabledPair();
    await expect(
      a.session.changePassphrase({ current: 'не та фраза зовсім інша тут', next: 'нова фраза три слова' })
    ).rejects.toMatchObject({ failure: 'bad_passphrase' });
  });

  it('зміна фрази піднімає wrap_version і лишає сейф на місці', async () => {
    const { server, a } = await enabledPair();
    const before = server.key!.wrapVersion;
    const records = server.records.size;

    await a.session.changePassphrase({ current: PASSPHRASE, next: 'нова фраза три слова' });

    expect(server.key!.wrapVersion).toBe(before + 1);
    expect(server.key!.keyVersion).toBe(1);
    expect(server.records.size).toBe(records);
  });

  it('другий пристрій, що спізнився зі зміною фрази, не псує конверт', async () => {
    const { server, a, b } = await enabledPair();
    await b.session.open({ passphrase: PASSPHRASE, nowMs: T0 + MINUTE });

    await a.session.changePassphrase({ current: PASSPHRASE, next: 'перша нова фраза тут' });
    const afterFirst = server.key!.wrapVersion;

    // B досі знає лише стару фразу: конверт нею вже не відкривається, і
    // операція зупиняється ДО запису. Саме тому CAS §7 і сторожить wrap_version.
    await expect(
      b.session.changePassphrase({ current: PASSPHRASE, next: 'друга нова фраза тут' })
    ).rejects.toMatchObject({ failure: 'bad_passphrase' });
    expect(server.key!.wrapVersion).toBe(afterFirst);
  });

  it('CAS по wrap_version не пропускає два конкурентні записи конверта', async () => {
    const { server, a } = await enabledPair();
    const stale = server.key!.wrapVersion;
    await a.session.changePassphrase({ current: PASSPHRASE, next: 'перша нова фраза тут' });

    await expect(
      a.transport.writeKey({
        mode: 'rewrap',
        expected_wrap_version: stale,
        wrapped_dek: 'AAAA',
        kdf: 'pbkdf2-sha256',
        kdf_params: { iterations: 1_000_000, salt_hex: '00'.repeat(16) }
      })
    ).rejects.toMatchObject({ code: 'conflict' });
  });

  it('re-key робить vault-reset ДО запису нового конверта і ревокує інші сесії', async () => {
    const { server, a } = await enabledPair();
    server.calls.length = 0;

    await a.session.rekey({ passphrase: 'нова фраза три слова', data: sample(), nowMs: T0 + MINUTE });

    const reset = server.calls.indexOf('vaultReset');
    const write = server.calls.indexOf('writeKey:rekey');
    const revoke = server.calls.indexOf('revokeOtherSessions');
    expect(reset).toBeGreaterThanOrEqual(0);
    expect(write).toBeGreaterThan(reset);
    expect(revoke).toBeGreaterThan(write);
    expect(server.key!.keyVersion).toBe(2);
  });

  it('після re-key сейф відкривається новою фразою на іншому пристрої', async () => {
    const { server, a } = await enabledPair();
    await a.session.rekey({
      passphrase: 'нова фраза три слова',
      data: sample(),
      nowMs: T0 + MINUTE
    });

    const c = newDevice(server, 'init-c');
    await c.session.open({ passphrase: 'нова фраза три слова', nowMs: T0 + 2 * MINUTE });
    const outcome = await c.session.pull({ data: emptyData(), nowMs: T0 + 2 * MINUTE });
    expect(Object.keys(outcome.patch.entries ?? {})).toEqual(['2026-01-15', '2026-01-16']);
  });
});

describe('відмови лишаються нейтральними', () => {
  it('другий пристрій без акаунта отримує власну причину, а не «немає згоди»', async () => {
    const server = new FakeVaultServer();
    const b = newDevice(server, 'init-b');
    await expect(
      b.session.open({ passphrase: PASSPHRASE, nowMs: T0 })
    ).rejects.toMatchObject({ failure: 'no_account' });
  });

  it('помилка не несе в собі ані шляху, ані шифротексту', async () => {
    const error = new VaultError('stale_server_copy');
    expect(error.message).toBe('stale_server_copy');
  });
});

/**
 * `record_key` серверного запису за логічним шляхом.
 *
 * Сервер шляхів не знає — і не має знати. Зіставлення робиться так само, як
 * його зробив би клієнт: за збереженим `sha256` шифротексту з метаданих сесії.
 */
async function serverKeyOfPath(
  server: FakeVaultServer,
  session: VaultSession,
  path: string
): Promise<string> {
  const known = session.snapshotMeta.records[path];
  if (known === undefined) throw new Error(`метадані не знають ${path}`);
  for (const [key, record] of server.records) {
    if (record.payloadB64 === null) continue;
    if ((await payloadDigest(fromBase64(record.payloadB64))) === known.sha256) return key;
  }
  throw new Error(`на сервері немає запису ${path}`);
}
