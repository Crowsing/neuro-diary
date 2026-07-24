# Контракт шару стану (state/) для екранних агентів

Джерело правди: чинні `types.ts`, `actions.ts`, `reducer.ts` і `persist.ts`.
`docs/prototype/nd-v2.dc.html` — лише архівне джерело посилань на старі рядки, а не
acceptance contract. Reducer — чиста функція `appReducer(env)(state, action)`,
`env = { now: Date; newId: () => string }`.

## Доступ зі екранів

```tsx
import { useApp } from '../state/store';    // { state: AppState; dispatch: Dispatch<Action> }
import { useNow, useNewId, useEnv } from '../state/clock'; // useNow(): Date (фіксоване «сьогодні»)
```

- `useApp(): { state: AppState; dispatch: Dispatch<Action> }`
- `useNow(): Date` — фіксується один раз при старті (`?now=YYYY-MM-DD` в URL або поточна).
  Передавати у `isoOff(now, o)`, `model(...)`, `genDemo(now)` тощо.
- `useNewId(): () => string` — `'c' + Date.now()` (потрібен рідко: id генерує сам reducer).
- Обгортка в `App.tsx`: `<NowProvider><AppProvider>…</AppProvider></NowProvider>`.
- Тости й генерація id ЖИВУТЬ У REDUCER-і: екрани НЕ диспатчать `TOAST_SHOW` після
  доменних дій (тексти на кшталт «Збережено», «Запис видалено» reducer ставить сам).
  `TOAST_SHOW` — лише для окремих інформаційних повідомлень.
- Автоприховання тосту через 2600 мс — таймер у `store.tsx` (`TOAST_HIDE`).
- Персист: кожна зміна стану → `save(state)` у localStorage `nd_demo_v3`
  (без `dialog`/`toast`; `checkin` зберігається). Завантаження: `load(now)`.
- Схема лишається v4. Застарілі reminder-поля на вході ігноруються й не потрапляють
  у наступне збереження або JSON-експорт; старе значення не є згодою.
- Адаптивний mobile/desktop shell — презентаційний стан із `matchMedia`; він не входить
  до reducer і не зберігається. Обидва shell-и використовують ті самі `NAV_TAB` і outlet.

## Ключові інваріанти

- **udr / CHECKIN_PATCH** (рядки 1177–1183): патч чернетки атомарно оновлює
  `checkin.d` І дзеркальну копію `data.entries[date] = {status:'draft', d}` —
  але ЛИШЕ якщо запис за цю дату не `done` (AC2, draft persistence).
- **CHECKIN_EXIT{saveDraft:true}** ніколи не затирає done-запис.
- **MENSES_CONFIRM**: дубль дати старту → лише прапор `dup:true`, дата не додається.
- Онбординг має рівно пʼять кроків (0–4) і завершується після кроку циклу.
  Застарілий `obStep:5` мігрує на крок 4 без зміни симптомів, груп, циклу чи записів.
- Toast — локальний неперсистентний feedback відкритого інтерфейсу, а не канал доставки.
- Toggle-значення (`toggle(arr, v)` з `lib/utils`) обчислює ЕКРАН і передає готовий
  масив/значення у патч — так само, як у прототипі (обробник читає поточний стан із рендера).

## Повна мапа: рядок скрипта → обробник прототипу → Action

| Рядок | Обробник | Action (dispatch) |
|---|---|---|
| 1005 | `tset(t)` | `TOAST_SHOW {text}`; авто-`TOAST_HIDE` через 2600 мс (store.tsx) |
| 966–972 | constructor (load) | `load(now)` у persist.ts (не action) |
| 974 | `componentDidUpdate` (save) | `save(state)` у persist.ts (не action) |
| 1079 | `goTab(t)` | `NAV_TAB {tab}` (скидає sub/selDay/crisisAns) |
| 1082 | `finishOb()` | `OB_FINISH` |
| 1121 | `obSymList` chip | `OB_SET {patch:{obSyms: toggle(obSyms ?? SYM.map(s=>s.id), id)}}` |
| 1122 | `obCycleChips` | `OB_SET {patch:{obCycle: k==='on'}}` |
| 1123, 1126 | legacy reminder controls | Видалені без заміни: немає чинного state/action або доставки |
| 1127 | `obBack` | `OB_SET {patch:{obStep: obStep-1}}` |
| 1129 | `obNext` | step===4 → `OB_FINISH`, інакше `OB_SET {patch:{obStep: obStep+1}}` |
| 1130 | `obSkip` | `OB_FINISH` |
| 1152 | `todCta` | `CHECKIN_START {iso: isoOff(now,0)}` |
| 1154–1155 | `todNoSym` | `TODAY_NO_SYMPTOMS` (запис + тост у reducer) |
| 1157 | `todAddMoreOn` | `CHECKIN_START {iso: isoOff(now,0)}` |
| 1158 | `todMenses` | `DIALOG_OPEN {dialog:{type:'menses', sel:'today', custom: isoOff(now,0), dup:false}}` |
| 1161 | `safetyOpen` | `NAV_SUB {sub:'safety', safetyMore:false}` |
| 1164–1175 | `startCheckin(iso,opts)` | `CHECKIN_START {iso, back?}` (resolveDraft у reducer) |
| 1177–1183 | `udr(p)` | `CHECKIN_PATCH {patch}` — дзеркалить чернетку (див. інваріанти) |
| 1185 | `usym(id,p)` | `CHECKIN_SYM_PATCH {id, patch}` |
| 1186 | `uctx(p)` | `CHECKIN_CTX_PATCH {patch}` |
| 1187–1196 | `fin()` | `CHECKIN_FINISH` (finalizeEntry + нав. за back + тост «Збережено») |
| 1198–1206 | `exitCheckin(saveDraft)` | `CHECKIN_EXIT {saveDraft}` |
| 1221 | desktop navigation | `NAV_TAB {tab}` — той самий маршрут, що й у mobile shell |
| 1257–1261 | `addFromCat(name,cat)` | `CAT_ADD_FROM_LIB {name, cat}` (id = env.newId(), тост у reducer) |
| 1265 | доданий пункт каталогу | `TOAST_SHOW {text:'Уже у вашому списку'}` (гілку added обчислює екран) |
| 1270–1271 | `move(i,dir)` | `CAT_MOVE {index, dir}` (межі перевіряє reducer) |
| 1273 | `openCat` | `NAV_SUB {sub:'catalog', catFrom:'set'}` |
| 1274 | `cycleTg2` | `DATA_PATCH {patch:{cycleOn: !data.cycleOn}}` |
| 1275, 1277 | legacy reminder settings | Видалені без заміни: немає чинного state/action або доставки |
| 1278 | `openPrivacy` | `NAV_SUB {sub:'privacy'}` |
| 1280 | `openDel` | `DIALOG_OPEN {dialog:{type:'delData'}}` |
| 1281 | `catBack` | `CAT_BACK` (розгалуження за catFrom у reducer, скидає catQ) |
| 1282 | `catQOn` | `CAT_SET {patch:{catQ: value}}` |
| 1286 | `arch` | `CAT_ARCHIVE {id}` (тост у reducer) |
| 1290 | `restore` | `CAT_RESTORE {id}` (без тосту) |
| 1292 | `newNameOn` | `CAT_SET {patch:{newName: value}}` |
| 1293 | `newTypeChips` | `CAT_SET {patch:{newType: k}}` |
| 1294–1297 | `addCustom` | `CAT_ADD_CUSTOM` (trim, порожньо → тост «Введіть назву симптому») |
| 1298 | `pvBack` | `NAV_SUB {sub:null, tab:'set'}` |
| 1299 | `pvLockTg` | Не показується: системне блокування застосунку не реалізоване |
| 1301 | `sfMoreOn` | `SAFETY_MORE` |
| 1304 | `crBack` | `CRISIS_BACK` (розгалуження за checkin у reducer) |
| 1306 | `crYes` / `crNo` | `CRISIS_ANSWER {ans:'yes'}` / `{ans:'no'}` |
| 1336 | `goTo(q)` | `CHECKIN_PATCH {patch: q.s===3 ? {step:3, symIdx:q.i} : {step:q.s}}` |
| 1341 | `scaleOpts` | `CHECKIN_SYM_PATCH {id, patch:{int:n}}` |
| 1343 | `sideOpts` | `CHECKIN_SYM_PATCH {id, patch:{side: sv.side===t?null:t}}` |
| 1345 | `extraOpts` | `CHECKIN_SYM_PATCH {id, patch:{extra: toggle(sv.extra??[], t)}}` |
| 1347 | `epM` / `epP` | `CHECKIN_SYM_PATCH {id, patch:{ep: max(0,(sv.ep??0)-1)}}` / `{ep:(sv.ep??0)+1}` |
| 1349 | `impactOpts` | `CHECKIN_SYM_PATCH {id, patch:{impact: sv.impact===t?null:t}}` |
| 1350 | `onComment` | `CHECKIN_SYM_PATCH {id, patch:{comment: value}}` |
| 1351 | `moreOn` | `CHECKIN_SYM_PATCH {id, patch:{more:true}}` |
| 1358–1373 | `secs` кнопки «Змінити/Додати» | `CHECKIN_PATCH {patch:{step:1|2|4|5}}` (через goTo для 1/2) |
| 1381 | `ciClose` | `CHECKIN_EXIT {saveDraft:true}` |
| 1383 | `ciBack` | sat → `CHECKIN_PATCH {patch:{step:6}}`; інакше goTo(seq[idx-1]) |
| 1385 | `ciNext` | sat → `{step:6}`; step 6 → `CHECKIN_FINISH`; інакше goTo(seq[idx+1]) |
| 1388 | `ciSaveDraft` | `CHECKIN_EXIT {saveDraft:true}` |
| 1390 | `wbOpts` | `CHECKIN_PATCH {patch:{wb:n, wbSkip:false}}` |
| 1392 | `wbSkipChip` | `CHECKIN_PATCH {patch:{wbSkip:!d.wbSkip, wb:null}}` |
| 1393 | `ciSymList` | `CHECKIN_PATCH {patch:{sel: toggle(d.sel,id), noSymptoms:false, symIdx:0}}` |
| 1395 | `ciNoneBtn` | `CHECKIN_PATCH {patch:{sel:[], sym:{}, noSymptoms:true, step:6}}` |
| 1396 | `ciOpenCat` | `NAV_SUB {sub:'catalog', catFrom:'checkin'}` |
| 1398 | `stressOpts` | `CHECKIN_CTX_PATCH {patch:{stress:n}}` |
| 1399 | `stressSkip` | `CHECKIN_CTX_PATCH {patch:{stress:null}}` |
| 1400 | `sleepQOpts` | `CHECKIN_CTX_PATCH {patch:{sleepQ:n}}` |
| 1401 | `sleepSkip` | `CHECKIN_CTX_PATCH {patch:{sleepQ:null}}` |
| 1403–1404 | `slHM` / `slHP` | `CHECKIN_CTX_PATCH {patch:{sleepH: max(0,(h??7)-0.5)}}` / `{min(14,(h??7)+0.5)}` |
| 1405 | `actTg` | `CHECKIN_CTX_PATCH {patch:{activity: !cx.activity}}` |
| 1406 | `actChips` | `CHECKIN_CTX_PATCH {patch:{actType: t}}` |
| 1407 | `heatTg` | `CHECKIN_CTX_PATCH {patch:{heat: !cx.heat}}` |
| 1408 | `ctxMoreOn` | `CHECKIN_PATCH {patch:{ctxMore:true}}` |
| 1409 | `extraChips` | `CHECKIN_CTX_PATCH {patch:{extras: toggle(cx.extras??[], t)}}` |
| 1410 | `ciNoteOn` | `CHECKIN_PATCH {patch:{note: value}}` |
| 1411 | `flareTg` | `CHECKIN_PATCH {patch:{flare: fl?null:{isNew:false,dur24:false,temp:false,note:''}}}` |
| 1413 | `flChips` | `CHECKIN_PATCH {patch:{flare:{...fl,[k]:!fl[k]}}}` |
| 1418 | `rvConfOn` | `CHECKIN_PATCH {patch:{confirmed:!d.confirmed}}` |
| 1426 | `crisisOpen` | `NAV_SUB {sub:'crisis', crisisAns:''}` |
| 1445 | клітинка календаря | `NAV_SUB {sub:'day', selDay: iso}` |
| 1469 | пункт списку історії | `NAV_SUB {sub:'day', selDay: iso}` |
| 1487 | `calPrev` | `HIST_CAL_PREV` |
| 1488 | `calNext` | `HIST_CAL_NEXT` (clamp ≤0 у reducer) |
| 1490 | `histChips` | `HIST_FILTER {filter}` |
| 1492 | `dayBack` | `NAV_SUB {sub:null, selDay:null, tab:'history'}` |
| 1506 | `dayEdit` | `CHECKIN_START {iso: selDay, back:'day'}` |
| 1508 | `dayFlareBtn` | `DIALOG_OPEN {dialog:{type:'flare', iso: selDay, f:{...(e.flare ?? {isNew:false,dur24:false,temp:false,note:''})}, had: !!e.flare}}` |
| 1509 | `dayDelete` | `DIALOG_OPEN {dialog:{type:'delEntry', iso: selDay}}` |
| 1510 | `dayDelMenses` | `MENSES_REMOVE {iso: selDay}` (тост у reducer) |
| 1520–1521 | `expCsv` / `expJson` | `downloadCsv(data)` / `downloadJson(state)` через браузерний Blob download |
| 1524 | `close` | `DIALOG_CLOSE` |
| 1528–1529 | `mensChips` | `MENSES_SET {sel:'today'}` / `{sel:'yest'}` (dup:false у reducer) |
| 1531 | `mensDateOn` | `MENSES_SET {custom: value}` (sel:'custom', dup:false у reducer) |
| 1534–1540 | `mensConfirm` | `MENSES_CONFIRM` (дата/дубль/sort/cycleOn/тост у reducer) |
| 1545 | `delEC` | `ENTRY_DELETE {iso: dialog.iso}` (закриває діалог, тост у reducer) |
| 1549 | `delCycle` | `DATA_DELETE {scope:'cycle'}` |
| 1550 | `delAll` | `DATA_DELETE {scope:'all'}` |
| 1551 | `restoreDemo` | Видалено з production shell; тестові fixtures завантажуються лише Playwright helpers |
| 1553–1554 | report actions | `window.print()` для друку/збереження PDF; CSV/JSON — реальні downloads; закриття → `DIALOG_CLOSE` |
| 1558 | `dfChips` | `FLARE_DLG_TOGGLE {flag}` |
| 1560 | `dfNoteOn` | `FLARE_DLG_NOTE {note}` |
| 1562–1564 | `dfSave` (put) | `FLARE_SAVE` (тост «Позначку збережено» у reducer) |
| 1565 | `dfDelOn` (put null) | `FLARE_DELETE` (тост «Позначку прибрано» у reducer) |
| 1580 | `trChips` | `TRENDS_SET {patch:{period:p}}` (sel:null автоматично у reducer) |
| 1592 | пункт списку трендів | `NAV_SUB {sub:'sym', trendsSym: id}` (trends.sym + sel:null у reducer) |
| 1623 | `syBack` | `NAV_SUB {sub:null, tab:'trends'}` |
| 1625 | `syModeChips` | `TRENDS_SET {patch:{mode:'chart'|'table'}}` |
| 1631 | `chHits` | `TRENDS_SET {patch:{sel:i}}` |
| 1636 | `chTipOpen` | `NAV_SUB {sub:'day', selDay: iso, tab:'history'}` |
| 1645 | рядок таблиці симптому | `NAV_SUB {sub:'day', selDay: iso, tab:'history'}` |
| 1685–1701 | `setR(p)` | `REPORT_SET {patch}` (period/step/syms=toggle/ctx/flares/cycle/notes/name/dob — обчислює екран) |
| 1717 | `pdfCreate` | `DIALOG_OPEN {dialog:{type:'pdf'}}` |

## Відомі свідомі відхилення від прототипу

1. Повторний показ ТОГО САМОГО тексту тосту в межах 2600 мс не подовжує таймер
   (у прототипі clearTimeout подовжував). Інший текст — подовжує, як у прототипі.
2. `FLARE_SAVE`/`FLARE_DELETE` пишуть flare лише в done-запис (у прототипі put()
   перевіряв тільки наявність запису); діалог загострення відкривається лише для
   done-записів, тож поведінка ідентична на всіх досяжних шляхах.
3. `CAT_MOVE` додатково перевіряє межі index (прототип покладався на валідний i з map).
4. Mobile/desktop перемикаються лише через CSS media query (`900px`) і не змінюють
   доменний стан. Застаріле поле `desk` ігнорується під час міграції localStorage.
5. Legacy reminder state/controls і внутрішній state gallery видалені. Нагадування
   чесно позначені недоступними; майбутній Telegram-канал вимагатиме нового opt-in.
   Системний App lock і відновлення demo-data також не мають production-контролів.
