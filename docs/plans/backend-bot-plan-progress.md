# Прогрес реалізації backend і Telegram-доставки

Джерело критеріїв: [backend-bot-plan.md](backend-bot-plan.md), §12.

- Базовий стан: `779c112` (`initial: capture current local-only prototype`).
- Поточна фаза: **Фаза 0 — реалізація й remote CI зелені; блокуючий
  branch protection очікує GitHub Pro**.
- Gate D: **не пройдено**. Фази 1–4 заблоковані до окремого
  privacy/security/clinical review дизайну.
- Позначки: `[ ]` — не підтверджено; `[x]` — підтверджено артефактом або тестом.

## Наскрізні gate-и кожної фази

- [x] Web unit-тести зелені. Evidence: `pnpm test` — 11 test files,
  182 tests passed.
- [x] Web production build зелений. Evidence: `pnpm build` — TypeScript і
  Vite production build passed.
- [x] Web Playwright зелений. Evidence: `pnpm e2e` — 56 tests passed
  (mobile + desktop).
- [x] API pytest і Ruff зелені. Evidence: `uv run --locked pytest` — 21
  passed; `ruff check .` — passed; `ruff format --check .` — 49 files
  formatted; `mypy --strict app` — 44 source files без помилок.
- [x] Bot pytest і Ruff зелені. Evidence: 5 tests passed; Ruff passed;
  `mypy --strict bot` — 2 source files без помилок.
- [x] Нова інфраструктура має інтеграційні, privacy та failure-mode тести.
  Evidence: 5 PostgreSQL 16 integration tests перевіряють
  upgrade→downgrade→upgrade, реальні логіни ролей, дозволені DML,
  `SQLSTATE 42501`, ownership, відсутність cross-schema FK і fail-closed
  default privileges; API validation-response не віддзеркалює вхідне
  значення.
- [x] UI та документація не оголошують майбутню функцію доступною до
  завершення всього delivery path. Evidence: README прямо фіксує, що web
  лишається local-only, а sync і Telegram-нагадування недоступні; web E2E
  unavailable-state зелений.

## Фаза 0 — Фундамент (локальна реалізація готова)

Scope: PostgreSQL 16, порожній `.env.example`, Alembic, запінені залежності,
CI, каркас шарів і контракти import-linter.

### DoD

- [ ] CI зелений і налаштований як блокуючий. Evidence:
  `.github/workflows/ci.yml` містить обов'язкові web/api/bot/gitleaks jobs
  без `continue-on-error`; remote run
  [30085202466](https://github.com/Crowsing/neuro-diary/actions/runs/30085202466)
  зелений для `web`, `api`, `bot` і повного history scan `gitleaks`.
  Увімкнення required checks `api`, `bot`, `gitleaks`, `web` для `main`
  повернуло GitHub API `403`: приватний репозиторій на поточному plan
  потребує GitHub Pro. Репозиторій не зроблено public через privacy-ризик,
  тому blocking-частина DoD чесно лишається відкритою.
- [x] Усі контракти import-linter з §5.2 активні. Evidence: 7 kept,
  0 broken; `allow_indirect_imports` відсутній.
- [x] `GET /health` покритий тестом. Evidence:
  `tests/test_health.py::test_health` → 200 `{"status":"ok"}`.
- [x] Міграція `0001` створює схеми `diary`/`reminders`, три ролі та
  GRANT-матрицю §6.3. Evidence: PostgreSQL 16 integration suite — 5 passed;
  Compose smoke підтвердив `PostgreSQL 16.14`; тимчасові container, network
  і volume після тесту видалені.

### Додаткові артефакти й TDD-evidence

- `.env.example` має лише порожні значення, включно з
  `TELEGRAM_BOT_ID`; `BOT_TOKEN` відсутній.
- Runtime і dev-залежності API/Bot запінені; обидва `uv.lock` оновлені,
  `pnpm@11.11.0` зафіксований у `packageManager`.
- Каркас §5.1 створено без бізнес-логіки майбутніх фаз; єдиний production
  route — `GET /health`.
- `ExportState.sub` більше не приймає `"states"` і звірений з web `Sub`;
  валідатор `sym ∩ absent = ∅` свідомо лишився Фазі 2.
- Red-крок: до реалізації нові schema/foundation contract tests дали 6
  очікуваних failures. Незалежний review додав окремі red-регресії для
  raw-query access-log, generic PostgreSQL DSN і membership усіх трьох
  migration-ролей в обох напрямках. Green-крок: повний API suite — 21
  passed.
- Незалежний code review знайшов і закрив чотири дефекти: Uvicorn access-log
  із raw query вимкнено й перевірено unit + реальним smoke; role-membership
  guard став fail-closed; `postgresql://` нормалізується на встановлений
  psycopg 3; API README актуалізовано.

## Gate D — дизайн-review (не пройдено)

- [ ] Криптомодель і модель втрати парольної фрази (§7) погоджені.
- [ ] Retention, tombstone TTL, бекапи та формулювання для користувачки (§6.4)
  погоджені.
- [ ] Модель незалежних згод (§4.3, §9.7) і consent copy погоджені.
- [ ] Data residency погоджено.
- [ ] Текст нагадування та політика quiet hours (§10) погоджені.

### Пакет для Gate D

Статус усіх текстів нижче: **чернетка, не погоджено**. Це матеріал для
privacy/security/clinical/product review, а не дозвіл починати Фази 1–4.

#### 1. Криптомодель і втрата парольної фрази (§7)

Потрібне рішення review:

- AES-256-GCM per record; новий випадковий 12-byte nonce на кожне
  шифрування; AAD прив'язує ciphertext до канонічного шляху,
  `client_ts` і `deleted`.
- Непрозорий `record_key` через HMAC, HKDF-subkeys і зашифрований manifest
  із монотонним `vault_seq` як anti-rollback механізм.
- Argon2id/WASM як основний KDF; PBKDF2-SHA256 ≥600k — лише fallback після
  бенчмарку в Telegram WebView.
- Звичайна зміна фрази робить re-wrap DEK, але не відкликає доступ того,
  хто вже знав стару фразу й має відповідний старий wrapped DEK; один із
  таких шляхів — відновлювані копії віком до 30 днів. При підозрі на
  компрометацію потрібні повний re-key + vault reset + ревокація сесій.

Чернетка тексту:

> Парольна фраза не надсилається на сервер. Вона захищає ключ до
> зашифрованої серверної копії, і ми не можемо її відновити. Якщо ви
> забудете фразу, локальні дані на цьому пристрої залишаться, але наявну
> серверну копію відкрити на іншому пристрої буде неможливо. Поки локальна
> копія доступна, з неї можна створити нову зашифровану копію після reset.
> Якщо втрачено і фразу, і локальну копію, дані відновити неможливо.

Окремий текст для компрометації:

> Звичайна зміна фрази не відкликає доступ людини, яка вже має стару фразу
> та відповідний старий загорнутий ключ; резервна копія віком до 30 днів —
> один із можливих шляхів такого доступу. Якщо ви підозрюєте компрометацію,
> оберіть повну заміну ключа й відкликання інших сесій: застосунок локально
> перешифрує серверну копію, а іншим пристроям знадобиться повна
> синхронізація.

#### 2. Retention, tombstones, backups і erasure (§6.4)

Потрібно погодити:

- hard DELETE в активних системах; tombstone без payload живе 180 днів;
- WAL + base backups із retention 30 днів;
- erasure-журнал поза DB restore, лише `{account_id, erased_at}`, retention
  щонайменше 60 днів; після кожного restore — reconciliation і повторне
  стирання;
- crypto-erasure видаляє wrapped DEK, але не замінює backup-обіцянку.

Обов'язкове формулювання для користувачки:

> активні системи — негайно; відновлювані копії зникають щонайбільше через
> 30 днів; у копіях — лише шифротекст

Review має окремо підтвердити, що 180/30/≥60 днів відповідають privacy,
clinical, legal і operational вимогам.

#### 3. Незалежні згоди (§4.3, §9.7)

Потрібно погодити модель:

- `health_sync`, `telegram_reminders` і `cycle_sync` — три незалежні
  opt-in; legacy-поля ніколи не стають згодою;
- для кожного `(account, kind)` існує щонайбільше одна активна згода;
  зберігаються `text_version`, час надання й час відкликання;
- новий account створюється атомарно лише разом із першим явним grant;
- account без жодної активної згоди через edge-case збою auto-erase-иться
  за 24 години; `erased` — термінальний стан;
- `ConsentGranted(..., 'telegram_reminders')` провізіонує розклад;
  `ConsentRevoked(..., 'health_sync')` запускає erasure, а
  `ConsentRevoked(..., 'telegram_reminders')` атомарно вимикає розклад;
- активні види згод самі є слабким health-inference і мають увійти до
  threat model;
- `cycle_sync` enforce-иться клієнтом: E2E не дає серверу розпізнати запис
  `cycle`. Якщо пристрій після revoke не повернеться online, цей ciphertext
  лишиться до revoke `health_sync`/повного erasure.

Чернетка `health_sync`:

> Дозволити зашифровану серверну копію щоденника для синхронізації між
> вашими пристроями? Це необов'язково: щоденник повністю працює локально
> без цієї згоди. Сервер зберігатиме ciphertext і технічні метадані, але не
> матиме парольної фрази для читання записів. Ви можете відкликати згоду
> окремо від нагадувань і даних циклу.

Чернетка `telegram_reminders`:

> Дозволити не більше одного нейтрального Telegram-нагадування щодня у
> вибраний час? Воно надсилається у приватний чат незалежно від того, чи є
> запис у щоденнику, і ніколи не містить симптомів, оцінок, циклу, нотаток
> або статусу заповнення. Цю згоду можна відкликати окремо від
> синхронізації. Якщо ви заблокуєте бота, розклад буде вимкнено, а згода на
> нагадування — відкликана автоматично.

Чернетка `cycle_sync`:

> Дозволити включати зашифровані дані циклу до серверної копії? Ця згода
> окрема від решти щоденника. Сервер не може відрізнити зашифрований запис
> циклу від інших записів, тому відкликання виконує клієнт під час
> наступного підключення. Якщо пристрій із цими даними більше не
> підключиться, ciphertext може залишатися до вимкнення всієї
> синхронізації або повного видалення акаунта.

#### 4. Нагадування і quiet hours (§10)

Exact allowlist для review:

- повідомлення: `Час зробити короткий запис`;
- кнопка: `Відкрити щоденник`;
- URL: лише `WEBAPP_URL`, без query-параметрів;
- жодної інтерполяції, `callback_data` чи сигналу стану щоденника.

Кандидат політики quiet hours: **[22:00, 08:00)** у локальній timezone
користувачки; `08:00` дозволено. Після погодження діапазон живе лише в API:
settings відхиляє заборонений `local_time`, а старий розклад, що потрапив у
нову політику, дає `skipped_quiet`. Review також має підтвердити:

- catch-up лише ≤4 год, у ту саму локальну добу і поза quiet hours;
- spring-forward → перший валідний час після gap; fall-back → перше
  входження (`fold=0`);
- at-most-once: після невизначеного send/confirm нагадування не
  відправляється повторно цієї доби.
- Telegram 403 вимикає розклад із `disabled_reason='bot_blocked'`;
  reconciler роллю `api_rw` ставить `consent.revoked_at`, створює
  `ConsentRevoked` і очищає `disabled_reason`. Воркер не отримує доступу
  до `diary`.

#### 5. Data residency — відкрите рішення

До проходження Gate D потрібно зафіксувати країну/регіон і провайдера для
primary DB, replica, WAL/base backups, erasure-журналу та observability;
місця обробки support/incident-командою; subprocessors; механізм
транскордонної передачі; шифрування й ownership ключів; строки видалення
на кожному носії. Без цього пункту Gate D не може бути пройдено.

## Фаза 1 — Identity, Consent, Auth (заблокована Gate D)

Scope: Ed25519 initData, anti-replay, атомарний auth+grant, opaque-сесії,
step-up, consents і auto-erasure сирітських акаунтів.

### DoD

- [ ] Валідатор initData має 100% branch coverage. Evidence: _pending_.
- [ ] Прострочений `auth_date` повертає `auth_stale`. Evidence: _pending_.
- [ ] Повторний initData повертає 401. Evidence: _pending_.
- [ ] Скасування діалогу згоди залишає нуль рядків у БД. Evidence: _pending_.
- [ ] Конфіг API не містить `BOT_TOKEN`. Evidence: _pending_.
- [ ] Репозиторії мають інтеграційні тести на реальній PostgreSQL через
  testcontainers. Evidence: _pending_.
- [ ] Log allowlist під тестом; initData відсутній у логах. Evidence: _pending_.
- [ ] Медичних ендпоінтів у Фазі 1 немає. Evidence: _pending_.

## Фаза 2 — Vault і Sync (заблокована Gate D)

Scope: клієнтське E2E, push/pull, ревізії, tombstones, компакшн, 409/410/reset,
chunked upload і key CAS.

### DoD

- [ ] Два конкурентні push одного `record_key` з однаковим `base_revision`
  дають рівно один 200 і один 409 на реальній PostgreSQL. Evidence: _pending_.
- [ ] Property-тест на реальній PostgreSQL підтверджує, що tombstone не
  перезаписується мовчки. Evidence: _pending_.
- [ ] Property-тест на реальній PostgreSQL підтверджує монотонність ревізій.
  Evidence: _pending_.
- [ ] Застарілий pull після компакшну повертає 410. Evidence: _pending_.
- [ ] Застарілий push після компакшну повертає 410. Evidence: _pending_.
- [ ] Після повного ресинку видалений ключ не воскресає. Evidence: _pending_.
- [ ] Клієнт відхиляє payload під чужим `record_key`. Evidence: _pending_.
- [ ] Повторне шифрування дає різні nonce і ciphertext. Evidence: _pending_.
- [ ] Спільні JSON-фікстури web↔api перевіряють контракт, включно з
  `sym ∩ absent = ∅`. Evidence: _pending_.
- [ ] Cycle merge для delete/offline-add/re-add збігається за ≤2 раунди.
  Evidence: _pending_.
- [ ] Playwright синхронізує два браузерні профілі через локальний API.
  Evidence: _pending_.
- [ ] Дамп БД не містить plaintext контрольних нотаток. Evidence: _pending_.
- [ ] `location.hash` і `sessionStorage` очищені від initData. Evidence: _pending_.

## Фаза 3 — Erasure і відкликання згод (заблокована Gate D)

Scope: outbox, erasure worker, vault reset, crypto-erasure, erasure-журнал і
retention/backup runbook.

### DoD

- [ ] Revoke `health_sync` залишає нуль рядків `vault_record`/`vault_key`.
  Evidence: _pending_.
- [ ] Гонка erasure проти in-flight push перевірена на реальній PostgreSQL.
  Evidence: _pending_.
- [ ] Restore бекапа з in-flight job повторно виконує erasure. Evidence: _pending_.
- [ ] Restore бекапа до запиту erasure запускає reconciliation журналу й
  повторне стирання. Evidence: _pending_.
- [ ] 30-денну модель бекапів відрепетирувано. Evidence: _pending_.

## Фаза 4 — Нагадування (заблокована Gate D)

Scope: reminders schema, worker, DST, quiet hours, ідемпотентність, reconciler
і settings endpoints.

### DoD

- [ ] DST-тести Europe/Kyiv покривають обидва переходи, неіснуючий і подвійний
  локальний час. Evidence: _pending_.
- [ ] Роль `reminder_worker` отримує permission denied на `diary.*`.
  Evidence: _pending_.
- [ ] Позитивний GRANT-тест покриває повний цикл insert-before-send під
  `reminder_worker` і провізію/деактивацію під `api_rw`. Evidence: _pending_.
- [ ] Збій між send і confirm не створює дубль; повторна доставка перевірена.
  Evidence: _pending_.
- [ ] Mock 403 встановлює `enabled=false` і `disabled_reason='bot_blocked'`,
  а reconciler ставить `revoked_at` і створює `ConsentRevoked`.
  Evidence: _pending_.
- [ ] Усі outbound тексти й labels мають exact-allowlist тести.
  Evidence: _pending_.
- [ ] Telegram API 429 і 403 покриті mock-тестами. Evidence: _pending_.
- [ ] Private-chat binding та opt-in/revoke покриті інтеграційними тестами.
  Evidence: _pending_.

## Фаза 5 — Hardening і gates (не розпочата)

Scope: rate limits, threat model і перевірка реалізації на відповідність
погодженому дизайну.

### DoD

- [ ] Review-gates пройдені й підписані. Evidence: _pending_.
- [ ] Schemathesis пройшов по OpenAPI. Evidence: _pending_.
- [ ] Initial upload пройшов навантажувальний smoke. Evidence: _pending_.
