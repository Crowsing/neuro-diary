// Друга знахідка незалежного review Фази 2: поелементний merge не мав
// tiebreaker'а.
//
// `mergeStamped` вирішував `a.at >= b.at ? a : b`, тобто при РІВНИХ мітках і
// різному вмісті результат залежав від порядку аргументів. На рівні запису
// tiebreaker за `device_id` був, на рівні елемента — ні, і два пристрої з
// однаковим годинником розходилися назавжди.
//
// Наявний `merge.test.ts` такого випадку не конструює: усі його сценарії мають
// різні мітки. Тому тут — і реальний шлях (домен → `toRecords` → merge), і
// property-тест саме на рівних мітках.

import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import type { AppData } from '../lib/types';
import { emptyData } from '../state/persist';
import { mergeCatalog, mergeGroups } from './merge';
import { snapshotOf, toRecords } from './serialize';
import { EMPTY_JOURNALS, type CatalogBody, type GroupsBody } from './types';

const T0 = 1_768_435_200_000;
const DEVICE_A = 'aaaa0000';
const DEVICE_B = 'bbbb1111';

function bodyOf<T>(data: AppData, path: string, device: string, at: number, previous = T0): T {
  // Знімок від того самого попереднього стану: саме так два пристрої, які
  // синхронізувалися раніше, отримують ОДНАКОВІ мітки появи елементів.
  const shared = snapshotOf({ ...emptyData(), active: ['fatigue'], custom: [], groups: [] }, null, previous);
  const record = toRecords(data, EMPTY_JOURNALS, device, at, shared).find(
    (item) => item.path === path
  );
  if (record === undefined) throw new Error(`немає запису ${path}`);
  return record.body as T;
}

describe('рівні мітки й різний вміст не розводять пристрої', () => {
  it('каталог: перейменований власний симптом сходиться в обидва боки', () => {
    const onA: AppData = {
      ...emptyData(),
      active: ['fatigue'],
      custom: [{ id: 'own', name: 'Назва з A', type: 'bool' }]
    };
    const onB: AppData = {
      ...emptyData(),
      active: ['fatigue'],
      custom: [{ id: 'own', name: 'Назва з B', type: 'bool' }]
    };

    // Той самий момент серіалізації — тобто рівні `at` у поелементних записах.
    const fromA = bodyOf<CatalogBody>(onA, 'catalog', DEVICE_A, T0);
    const fromB = bodyOf<CatalogBody>(onB, 'catalog', DEVICE_B, T0);
    expect(fromA.custom.own.at).toBe(fromB.custom.own.at);

    expect(mergeCatalog(fromA, fromB)).toEqual(mergeCatalog(fromB, fromA));
  });

  it('групи: перейменована група сходиться в обидва боки', () => {
    const onA: AppData = {
      ...emptyData(),
      active: ['fatigue'],
      groups: [{ id: 'g1', name: 'Мігрень', archived: false }]
    };
    const onB: AppData = {
      ...emptyData(),
      active: ['fatigue'],
      groups: [{ id: 'g1', name: 'Головний біль', archived: false }]
    };

    const fromA = bodyOf<GroupsBody>(onA, 'groups', DEVICE_A, T0);
    const fromB = bodyOf<GroupsBody>(onB, 'groups', DEVICE_B, T0);
    expect(fromA.groups.g1.at).toBe(fromB.groups.g1.at);

    expect(mergeGroups(fromA, fromB)).toEqual(mergeGroups(fromB, fromA));
  });

  it('порядок відображення при рівному orderAt теж сходиться', () => {
    const left: CatalogBody = {
      kind: 'catalog',
      places: { a: { place: 'active', at: T0 }, b: { place: 'active', at: T0 } },
      custom: {},
      order: ['a', 'b'],
      orderAt: T0,
      journal: []
    };
    const right: CatalogBody = { ...left, order: ['b', 'a'] };

    expect(mergeCatalog(left, right).order).toEqual(mergeCatalog(right, left).order);
  });
});

/** Генератор поелементної мапи з навмисно вузьким простором міток. */
const stamped = fc.dictionary(
  fc.constantFrom('a', 'b', 'c'),
  fc.record({
    place: fc.constantFrom('active' as const, 'archived' as const),
    // Рівно дві можливі мітки: саме так сценарій «рівні мітки» перестає бути
    // рідкісним і починає траплятися майже в кожному прогоні.
    at: fc.constantFrom(T0, T0 + 1)
  })
);

const catalogArb = fc
  .record({
    places: stamped,
    order: fc.subarray(['a', 'b', 'c']),
    orderAt: fc.constantFrom(T0, T0 + 1)
  })
  .map(
    (value): CatalogBody => ({
      kind: 'catalog',
      places: value.places,
      custom: {},
      order: value.order,
      orderAt: value.orderAt,
      journal: []
    })
  );

describe('властивості merge каталогу', () => {
  it('комутативний', () => {
    fc.assert(
      fc.property(catalogArb, catalogArb, (left, right) => {
        expect(mergeCatalog(left, right)).toEqual(mergeCatalog(right, left));
      }),
      { numRuns: 500 }
    );
  });

  it('ідемпотентний', () => {
    fc.assert(
      fc.property(catalogArb, catalogArb, (left, right) => {
        const once = mergeCatalog(left, right);
        expect(mergeCatalog(once, once)).toEqual(once);
      }),
      { numRuns: 300 }
    );
  });

  it('асоціативний на трьох версіях', () => {
    fc.assert(
      fc.property(catalogArb, catalogArb, catalogArb, (a, b, c) => {
        expect(mergeCatalog(mergeCatalog(a, b), c)).toEqual(
          mergeCatalog(a, mergeCatalog(b, c))
        );
      }),
      { numRuns: 500 }
    );
  });
});
