# Спільні контрактні фікстури web ↔ api

Той самий аргумент, що й у [`consent-copy/`](../consent-copy/README.md): обидва
застосунки — рівноправні споживачі одного джерела, тож воно лежить у корені, а
не всередині одного з них.

Кожен файл читають обидві сторони:

- `apps/api/tests/contract/test_shared_fixtures.py`,
- `apps/web/src/sync/fixtures.test.ts`.

Винятків більше немає: Фаза 6 дала `quiet-hours.json` web-споживача. Політика
приходить у бандл віртуальним модулем `virtual:quiet-hours` (плагін
`nd-quiet-hours` у `apps/web/vite.config.ts`) і ніде в `apps/web/src` не
записана числами — саме цього вимагає §10, і саме тому фікстуру завели раніше
за екран.

| Файл | Що фіксує |
|---|---|
| `done-entry.valid.json` | тристан present / absent / unknown у чинній схемі v4 |
| `done-entry.sym-absent-overlap.json` | значення, яке **обидві** сторони мають відхилити (§13.13) |
| `app-data.v4.min.json` | мінімальний валідний `AppData` |
| `merge/cycle-reconvergence.json` | delete / offline-add / re-add і очікуваний результат (§9.3) |
| `quiet-hours.json` | політика quiet hours §10 і її межі; єдина константа живе в `app/domain/reminders.py`, цей файл — її експорт |

Фікстури описують контракт, а не приклад даних: якщо одна зі сторін почне
приймати те, що інша відхиляє, тест упаде саме тут, а не в проді.
