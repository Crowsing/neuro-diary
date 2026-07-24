# План майбутнього backend і Telegram-інтеграції

> Статус: не схвалена до реалізації чернетка (проєктний план, ревізія v2). Вона не описує наявні можливості продукту. Реалізація заблокована gate-ами цього плану та [review-gates.md](../review-gates.md). Попередня версія збережена дослівно в [archive/backend-bot-plan-v1.md](archive/backend-bot-plan-v1.md).

## 1. Фактичний стан

- `apps/web` працює local-only і не виконує мережевих запитів. Дані — під чинним ключем `localStorage["nd_demo_v3"]` (назва ключа не є номером активної схеми й не змінюється без окремої безвтратної міграції), `schemaVersion: 4`.
- Схема v4 розрізняє тристан спостереження: present = id у `DoneEntry.sym`; explicit absent = id у `DoneEntry.absent`; unknown = ні там, ні там (`apps/web/src/lib/entry.ts`). Persisted-полів згод у моделі немає; `report.name`/`dob` ніколи не персистяться.
- Reducer web має шість гранулярностей видалення: `ENTRY_DELETE` (один день), `MENSES_REMOVE` (одна позначка циклу), `DATA_DELETE{scope:'cycle'}` (лише `cycleStarts`), `DATA_DELETE{scope:'all'}` (усе), `GROUP_DELETE` (група; значення симптомів зберігаються), `FLARE_DELETE` (flare одного запису).
- `apps/api` має `/health` і Pydantic-схеми активного diary-контракту (`app/schemas.py`, strict, `extra="forbid"`) з legacy-адаптерами `LegacyAppDataInput`/`LegacyExportStateInput`, які мовчки відкидають retired reminder-поля. Синхронізації та серверного сховища немає.
- `apps/bot` (aiogram v3) обробляє приватний `/start` і показує кнопку відкриття щоденника; поза приватним чатом мовчить. Тест-allowlist забороняє інтерполяцію персональних даних і `callback_data`. Scheduler та доставка нагадувань відсутні.
- Демо-дані (`genDemo`) детерміновані й структурно нерозрізнювані від реальних; production `load()` ніколи їх не викликає (фолбек — `emptyData()`).
- `docs/prototype` — архівний snapshot, а не acceptance contract.
- Інфраструктури немає: ні БД, ні ORM, ні Docker, ні CI; версії залежностей api незапінені.

## 2. Незмінні межі

- Не додавати sync, scheduler, browser push або delivery affordance частинами. UI не показує toggle, час, permission CTA, snooze чи success-state, доки наскрізна функція не працює.
- Toast-и лишаються локальним неперсистентним UI feedback.
- Актуальні diary state, persistence, export і API output не містять reminder preferences.
- Legacy `remOn`, `remTime`, `obRemOn` і `obTime` можна прийняти лише на явній legacy input boundary та слід ігнорувати. `remOn: true` ніколи не створює consent.
- Health-sync consent і Telegram-reminder consent — незалежні. `cycle_sync` — третя згода з окремим наданням і окремим відкликанням. Незалежність означає саме це, а не можливість синхронізувати цикл без серверної копії: дані циклу лежать у тому самому сейфі, тому `cycle_sync` надається лише за активної `health_sync` і каскадно відкликається разом із нею (§4.3). Зворотної залежності немає: `health_sync` без `cycle_sync` — повноцінний штатний стан.
- Local-only режим web повністю робочий без жодної згоди; відсутність синхронізації нічого не ламає.
- Медичні значення (симптоми, оцінки, цикл, нотатки, підсумки) не потрапляють у URL, логи, аналітику, повідомлення про помилки або Telegram.
- API є єдиним власником майбутнього серверного стану.
- Сервер не робить медичного triage і не аналізує вміст записів.

## 3. Керівний принцип архітектури

Сервер — **zero-knowledge encrypted vault + канал нейтральних нагадувань**. Домен спостережень (тристан, цикл, flare, групи) живе і залишається в клієнті. Сервер не бачить plaintext медичних даних; конфіденційність і цілісність забезпечуються криптографічно (client-side E2E, AAD, manifest), а не політикою. Ізоляція каналу нагадувань від медичних даних — на рівні СУБД (окрема схема + роль), імпорт-контрактів і секретів.

## 4. Домен (DDD)

### 4.1 Ubiquitous language

| Українською | У коді | Зміст |
|---|---|---|
| Обліковий запис щоденника | `DiaryAccount` | Серверна ідентичність; створюється лише атомарно разом із першою згодою |
| Прив'язка Telegram | `TelegramIdentity` | `telegram_user_id` ↔ account (1:1); ім'я/username не персистяться |
| Згода | `Consent` | Окремий факт per kind; відкликання — подія, не видалення рядка |
| Сейф щоденника | `DiaryVault` | Сукупність шифрованих записів акаунта |
| Запис сейфа | `VaultRecord` | Одиниця синхронізації: opaque ciphertext + непрозорий ключ |
| Ревізія | `Revision` | Монотонний per-account лічильник версій сейфа |
| Надгробок | `Tombstone` | Маркер видалення запису сейфа з TTL |
| Горизонт компакшну | `compacted_up_to` | Ревізія, нижче якої tombstones фізично видалені |
| Маніфест сейфа | `manifest` | Шифрований синглтон: клієнтський лічильник + HMAC над станом сейфа (anti-rollback) |
| Розклад нагадувань | `ReminderSchedule` | IANA timezone + локальний час + enabled |
| Доставка нагадування | `ReminderDelivery` | Факт «щонайбільше одна на локальну добу» |
| Витирання | `Erasure` | Незворотне видалення з чесною моделлю бекапів і журналом витирань |
| Тристан | present / absent / unknown | Клієнтський інваріант; сервер його не інтерпретує |
| Група спостереження (стан/захворювання) | `TrackingGroup` + `symptomGroupIds` | Клієнтське поняття: користувацька мітка, в UI зазвичай іменована станом/захворюванням (онбординг: «Наприклад, назва стану»); many-to-many організація симптомів. Назва групи — чутливі медичні дані: на сервер потрапляє лише як E2E-ciphertext синглтона `groups` |

### 4.2 Bounded contexts

1. **Observation/Diary** — існує лише в `apps/web`. Сервер не реплікує цей домен; інваріанти спостережень виконує клієнт і контрактні тести, а сервер криптографічно позбавлений можливості їх порушити (не бачить plaintext).
2. **Identity & Consent** — акаунти, Telegram-прив'язка, згоди, сесії, erasure-пайплайн.
3. **Sync/Vault** — ревізії, записи, надгробки, manifest, push/pull.
4. **Reminders/Delivery** — розклад і доставка; фізично ізольований (окрема PostgreSQL-схема + окрема роль).
5. **Bot Entry** — наявний `apps/bot`: stateless `/start` + статична кнопка. Не розширюється.

### 4.3 Агрегати та інваріанти

**Identity & Consent**
- `DiaryAccount` (root): `id`, `status ∈ {active, erasing, erased}`. Інваріанти: акаунт існує лише разом із ≥1 згодою (атомарне створення з першою згодою, §8); `erased` — термінальний стан.
- **Акаунт без жодної активної згоди** (Gate D, скасовує попередній TTL 24 год): строк залежить від причини, з якої зникла остання згода — саме тому потрібна колонка `revoke_reason`. `revoke_reason='user'` (явна дія користувачки) → `erasure_job` **у тій самій транзакції**, SLO p95 ≤ 60 с, жорстка стеля 15 хв. `revoke_reason ∈ {bot_blocked_timeout, stale_text_timeout}` (акаунт втратив згоду не за рішенням користувачки) → 30 діб, за які вона може повернутися й відновити згоду. Мотив: єдиний TTL 24 год у поєднанні з автовідкликанням за 403 знищував би акаунт через звичайне блокування бота (§4.4).
- `Consent`: `kind ∈ {health_sync, telegram_reminders, cycle_sync}`, `granted_at`, `revoked_at`, `text_version`, `text_sha256`, `text_locale`, `revoke_reason`. Інваріанти: ≤1 активна per (account, kind); legacy-поля ніколи не мапляться у згоду; `cycle_sync` вимагає активної `health_sync` (grant без неї → 409 `consent_precondition`), і відкликання `health_sync` каскадно відкликає `cycle_sync` у тій самій транзакції. Запис згоди зберігає версію тексту, **хеш показаного тексту**, локаль, час надання, час відкликання і причину відкликання.
- **Retention рядків `consent`:** відкликані рядки зберігаються не довше **24 місяців** після `revoked_at` (доказ згоди за Art. 7(1) проти мінімізації), потім фізично видаляються нічним job-ом. Повна erasure акаунта видаляє їх негайно й безумовно.
- `TelegramIdentity`: з initData персистується лише `telegram_user_id`. Окрема (четверта) згода на його зберігання **не вводиться**: без ідентифікатора акаунт не існує як поняття, тож така згода була б несправжнім вибором.

**Sync/Vault**
- `DiaryVault` (root, 1:1 з account): `current_revision`, `compacted_up_to`, `reset_revision`.
- `VaultRecord`: `record_key` (HMAC-похідний на клієнті), `payload` (AES-GCM ciphertext), `revision`, `deleted`, `client_ts_ms` (`bigint`, мілісекунди UTC — індексна копія; авторитетний `client_ts` лежить усередині шифрованого payload). Тип свідомо не `timestamptz`: AAD (§7) прив'язується до десяткового цілого, а round-trip через `timestamptz` і ISO-8601 не побайтовий, тож конверт переставав би відкриватися.
- Інваріанти: ревізія строго монотонна і видима у порядку зростання; сервер ніколи не парсить payload; tombstone перемагає застарілий update; push гейтиться `reset_revision` і `compacted_up_to`; історія версій не зберігається (last-write only — свідомо, заради чесного видалення).
- **Лічильники сейфа переживають видалення записів і мусять бути скинуті разом із ними.** При відкликанні `health_sync` і при повній erasure в тій самій транзакції виконується `reset_revision = compacted_up_to = current_revision = current_revision + 1`. Без цього пристрій, що був офлайн до відкликання, після повторного grant проходить обидва гейти §9.1 і тихо заливає назад увесь щоденник, який користувачка щойно прибрала — без 409, без 410 і без діалогу. Тест: push із до-відкликального `base_revision` → 409 `vault_reset`.

**Клієнтські інваріанти, які серверний контракт не має права зламати** (закріплюються контрактними тестами web↔api, не рантаймом сервера):
- тристан: absent — лише явний масив; present перемагає absent (`sym ∩ absent = ∅`; додати `model_validator` у `DoneEntry` — зараз відсутній);
- пропущений день ≠ відсутність симптому;
- історична правда лише з запису, ніколи з поточних налаштувань;
- групи — лише організаційні мітки: нуль серверної логіки, ключованої групами; оскільки в UI групу зазвичай називають станом/захворюванням, її назва трактується як медичне значення в сенсі §2 (ніколи не в URL/логах/Telegram; сервер бачить лише ciphertext);
- draft ніколи не затирає done (правило клієнтського merge, §9.3);
- значення симптому з id без визначення в каталозі не відкидається мовчки — показується як «невідомий симптом».

**Reminders**
- `ReminderSchedule` (root): `telegram_chat_id`, `tz`, `local_time`, `enabled`, `disabled_reason`, `next_fire_at` (передобчислений UTC). Інваріанти: існує лише за активної згоди `telegram_reminders` (з урахуванням reconciler-лагу при `bot_blocked`); `local_time` відповідає політиці quiet hours (§10).
- `ReminderDelivery`: щонайбільше одна per (account_id, local_date) — первинний ключ у БД; це і є атомарний occurrence id.

### 4.4 Доменні події та мости між контекстами (transactional outbox)

Мінімальний набір (без карго-культу; подій без споживачів не вводимо):
- **Провізія розкладу — не подія, а одна транзакція.** Grant `telegram_reminders` створює `consent` і рядок `reminders.reminder_schedule` в одній транзакції роллю `api_rw` (вона вже має права на обидві схеми, §6.3). Подія `ConsentGranted` **прибрана**: після переходу на одну транзакцію в неї не лишилося споживача, а план сам забороняє події без споживачів. Побічний ефект, заради якого це й зроблено: зникає вікно, у якому `GET /v1/reminders/settings` віддає 404 одразу після натискання «Увімкнути нагадування».
- `ConsentRevoked(account_id, kind)` → `health_sync`: серіалізована erasure сейфа (§9.8) + скидання лічильників (§4.3) + каскадне відкликання `cycle_sync`; `telegram_reminders`: **DELETE** рядка `reminder_schedule` і рядків `reminder_delivery` у тій самій транзакції, що і `revoked_at` (не `enabled=false` — рядка не має лишатися без згоди); `cycle_sync`: серверний DELETE записів свого домену (§9.7).
- `AccountErasureRequested(account_id)` → повний purge + запис у erasure-журнал (§6.4).
- **Зворотний міст reminders→identity.** Воркер не пише в схему `diary`. При 403 він ставить **лише** `enabled=false, disabled_reason='bot_blocked'` у своїй схемі й **не чіпає згоду**. Reconciler у `outbox_dispatcher` (роль `api_rw`) відкликає згоду лише після **14 діб поспіль** у цьому стані, виставляючи `revoke_reason='bot_blocked_timeout'`. Мотив порогу: блокування бота — частий і легко зворотний жест, а 403 повертається також при `user is deactivated` і `chat not found`; негайне відкликання разом із правилом «нуль згод → erasure» знищувало б акаунт за хвилину й робило б недосяжними самі банери, якими ми пояснюємо, що сталося.
- **Тільки HTTP 403** запускає цей шлях. Текст `description` від Bot API не парситься і не персиститься (логується лише `error_code`). Будь-який 400 і будь-який 5xx → `status='failed'` і алерт, без зміни розкладу і без зміни згоди.

### 4.5 Anti-corruption layers

- Legacy-клієнтський формат ↔ сервер: наявні `LegacyAppDataInput`/`LegacyExportStateInput` — канонічний ACL; будь-який вхідний формат проходить лише крізь них.
- Sync ↔ Observation: новий клієнтський модуль `apps/web/src/sync/` — єдине місце серіалізації домену у vault-записи і назад; тут-таки живуть журнали видалень (cycle/catalog/groups) на межі серіалізації — reducer домену не змінюється; тут-таки клієнтська гігієна initData (§8).
- Reminders ↔ Identity: reminders-схема отримує `telegram_chat_id` копією при grant; воркер не має доступу до identity/vault.

## 5. Шарування і механічне закріплення

### 5.1 Структура `apps/api/app/`

```
app/
  main.py                  # composition root: DI-wiring, app factory; session-фабрика → app.state
  api/v1/                  # тонкі роутери: parse → service → serialize
    auth.py sync.py consents.py account.py reminders.py health.py
    deps.py                # НЕ імпортує app.infra: типізується Protocol-ами з services/ports.py
    errors.py              # sanitized exception handlers
  schemas/                 # Pydantic DTO (contract.py = наявний schemas.py)
  services/                # application services — вся бізнес-логіка
    auth.py consent.py sync.py erasure.py reminder.py
    ports.py               # Protocol-інтерфейси репозиторіїв/шлюзів
  domain/                  # чисті сутності, VO, доменні помилки; нуль I/O
    identity.py vault.py reminders.py events.py
  infra/
    db/ (engine.py, models.py, repositories/, migrations/ — alembic)
    telegram/ (initdata.py — валідація; bot_api.py — sendMessage-клієнт воркера)
    clock.py
  workers/                 # власні composition roots своїх процесів
    reminder_worker.py erasure_worker.py outbox_dispatcher.py
```

Правила: ORM-моделі й сесії БД не піднімаються вище `infra/db/repositories`; жоден роутер не пише SQL/ORM-запитів; сервіси залежать від Protocol-портів, реалізації підставляє `main.py`.

### 5.2 Контракти import-linter (CI, блокуючі; **import-linter ≥ 2.0 запінений**)

```toml
[tool.importlinter]
root_package = "app"
include_external_packages = true    # інакше заборони fastapi/sqlalchemy не працюють

[[tool.importlinter.contracts]]
name = "Layers"
type = "layers"
layers = ["app.main", "app.api | app.workers", "app.services", "app.infra", "app.domain"]

[[tool.importlinter.contracts]]
name = "Domain is pure"
type = "forbidden"
source_modules = ["app.domain"]
forbidden_modules = ["fastapi", "sqlalchemy", "app.infra", "app.services", "app.api"]

[[tool.importlinter.contracts]]
name = "No ORM/DB above repositories"
type = "forbidden"
source_modules = ["app.api", "app.services"]
forbidden_modules = ["sqlalchemy", "app.infra.db"]   # увесь підпакет; заодно закриває «роутер → репозиторій повз сервіси»

[[tool.importlinter.contracts]]
name = "Workers do not import ORM models"
type = "forbidden"
source_modules = ["app.workers"]
forbidden_modules = ["app.infra.db.models"]

[[tool.importlinter.contracts]]
name = "Services are framework-free"
type = "forbidden"
source_modules = ["app.services"]
forbidden_modules = ["fastapi", "starlette"]

[[tool.importlinter.contracts]]
name = "Schemas are standalone"
type = "forbidden"
source_modules = ["app.schemas"]
forbidden_modules = ["app.api", "app.services", "app.infra"]

[[tool.importlinter.contracts]]
name = "Reminder worker never touches vault"
type = "forbidden"
source_modules = ["app.workers.reminder_worker", "app.services.reminder"]
forbidden_modules = ["app.infra.db.repositories.vault", "app.services.sync", "app.domain.vault"]
```

`allow_indirect_imports` не вмикати (ослаблення скасовує гарантію).

### 5.3 Межа довіри api ↔ bot

Боту не потрібно НІЧОГО медичного — навіть «чи заповнила сьогодні» є медичним сигналом. **Нагадування «сліпе»: надсилається за розкладом, незалежно від наявності запису.**

Defense in depth:
1. `apps/bot` незмінний: stateless `/start`, без `callback_data`, без доступу до БД.
2. Доставку виконує `reminder_worker` — окремий процес у складі api-кодобази, окремою PG-роллю (GRANT-матриця в §6.3); `SELECT` з `diary.*` → permission denied (під інтеграційним тестом).
3. Текст — статичний exact allowlist (§10) без інтерполяції; тест-allowlist як у наявному боті.
4. import-linter механічно забороняє воркеру імпорт vault-коду.
5. `BOT_TOKEN` — лише в env бота і воркера; api-процес його не має ніколи (§8, §11).
6. **Allowlist методів Bot API для воркера — рівно `{sendMessage, deleteMessages}`.** Жодного `getChat`, `getChatMember`, `getUserProfilePhotos`. `deleteMessages` присутній свідомо: це операція **на користь** приватності (§6.4, 48-годинне прибирання), і вона має бути архітектурно легальною, а не робитися в обхід. Механізм, який не порушує пункти 2 і 5: черга `reminders.message_cleanup(account_id, chat_id, message_id, expires_at)` з GRANT `INSERT` для `api_rw` і `SELECT + DELETE` для `reminder_worker`. Воркер прибирає повідомлення за чергою у своїй схемі; `BOT_TOKEN` так і не наближається до схеми `diary`. Альтернатива «дати `BOT_TOKEN` процесу `erasure_worker`» відхилена: вона дала б повний DML на `diary` процесу з токеном бота.

Окрема БД не потрібна: окрема схема + роль дає ту саму СУБД-ізоляцію дешевше (один інстанс, спільні бекапи, єдина retention-політика).

## 6. Дані: PostgreSQL

### 6.1 Одиниці зберігання: record-level ciphertext

- Не структуровані колонки (plaintext медичних даних на сервері — конфлікт із межами §2); не один blob на акаунт (ламає гранулярність видалення, пагінацію; sync стає O(весь щоденник)).
- Одиниці: `entry:<iso-date>` (один `DoneEntry|DraftEntry`); синглтони `cycle` (весь `cycleStarts` + журнал видалень одним записом — ключі виду `cycle:<date>` злили б дати в server-visible простір), `catalog` (`active/archived/custom` + журнал), `groups` (`groups + symptomGroupIds` + журнал), `settings` (`cycleOn`; `lock` не синхронізується — локальна демо-властивість), `manifest` (§7).
- `record_key = HMAC-SHA256(k_index, канонічний шлях)[:32]` — детермінований між пристроями, непрозорий для сервера (сервер не знає навіть дат записів). Субключі кореневого ключа через HKDF: `k_index` (info="index"), `k_auth` (info="auth").

### 6.2 DDL

Схема `diary`:
```sql
account            (id uuid PK, created_at timestamptz, status text CHECK (status IN ('active','erasing','erased')));
telegram_identity  (telegram_user_id bigint PK, account_id uuid UNIQUE NOT NULL REFERENCES account,
                    first_seen_at timestamptz, last_auth_at timestamptz);
consent            (id uuid PK, account_id uuid REFERENCES account,
                    kind text CHECK (kind IN ('health_sync','telegram_reminders','cycle_sync')),
                    granted_at timestamptz NOT NULL, revoked_at timestamptz, text_version text NOT NULL,
                    text_sha256 bytea NOT NULL,                         -- хеш саме показаного тексту
                    text_locale text NOT NULL DEFAULT 'uk',
                    revoke_reason text NULL
                      CHECK (revoke_reason IN ('user','bot_blocked_timeout','stale_text_timeout')));
  -- UNIQUE INDEX ux_consent_active ON consent(account_id, kind) WHERE revoked_at IS NULL;
session_token      (id uuid PK, account_id uuid NOT NULL REFERENCES account, token_hash bytea NOT NULL,
                    created_at, expires_at, last_used_at, revoked_at);  -- INDEX (token_hash)
auth_replay        (initdata_hash bytea PK, seen_at timestamptz);       -- одноразовість initData; TTL-чистка
vault_key          (account_id uuid PK, wrapped_dek bytea, kdf text, kdf_params jsonb,
                    key_version int NOT NULL DEFAULT 1,
                    wrap_version int NOT NULL DEFAULT 1,                -- CAS для re-wrap, див. §7
                    wrapped_dek_prev bytea NULL, wrap_version_prev int NULL, prev_written_at timestamptz NULL);
vault_revision     (account_id uuid PK, current_revision bigint NOT NULL DEFAULT 0,
                    compacted_up_to bigint NOT NULL DEFAULT 0,
                    reset_revision bigint NOT NULL DEFAULT 0);          -- не компактяться; скидаються при revoke health_sync (§4.3)
vault_record       (account_id uuid, record_key bytea, payload bytea, payload_size int,
                    revision bigint NOT NULL, deleted boolean NOT NULL DEFAULT false,
                    client_ts_ms bigint, updated_at timestamptz,        -- саме bigint: AAD прив'язується до мс UTC (§7)
                    PRIMARY KEY (account_id, record_key));
  -- UNIQUE INDEX ux_vault_rev ON vault_record(account_id, revision);   -- курсор pull
erasure_job        (id uuid PK, account_id uuid, scope text, requested_at, completed_at,
                    deletion_copy_version text NOT NULL);               -- редакція обіцянки §6.4, чинна на момент запиту
outbox             (id bigserial PK, event_type text, payload jsonb /* без медичних даних */, created_at, processed_at);
```

**Ця дельта реалізується міграцією `0002`, а не правкою `0001`.** `0001` уже застосована й
змерджена в `main` під strict branch protection; переписування застосованої міграції
зруйнувало б відтворюваність, яку доводить downgrade→upgrade-тест Фази 0.

**TTL службових таблиць** (жодна з них не має його сьогодні, і кожна накопичує сліди):
`auth_replay` — 48 год; `session_token` — 30 днів після `expires_at`/`revoked_at`;
`reminder_delivery` — 14 днів; `outbox` — 30 днів після `processed_at`, причому `payload`
не має містити `kind` відкликаної згоди (це той самий health-inference, який §11 забороняє
виносити з БД). Прибирання виконує `outbox_dispatcher` роллю `api_rw` — саме тому, що
`reminder_worker` не має `DELETE` на `reminder_delivery` і не повинен його отримати.

Схема `reminders`:
```sql
reminder_schedule  (account_id uuid PK, telegram_chat_id bigint NOT NULL, tz text NOT NULL,
                    local_time time NOT NULL, enabled boolean NOT NULL,
                    disabled_reason text NULL CHECK (disabled_reason IN ('bot_blocked')),
                    next_fire_at timestamptz NOT NULL, updated_at timestamptz);
  -- INDEX (enabled, next_fire_at)
reminder_delivery  (account_id uuid, local_date date,
                    status text CHECK (status IN ('pending','sent','failed','skipped_stale','skipped_quiet')),
                    telegram_message_id bigint, created_at timestamptz,
                    PRIMARY KEY (account_id, local_date));
message_cleanup    (account_id uuid, chat_id bigint NOT NULL, message_id bigint NOT NULL,
                    expires_at timestamptz NOT NULL,                    -- жорсткий TTL 48 год
                    PRIMARY KEY (account_id, message_id));
  -- INDEX (expires_at)
```

`reminder_schedule` **не отримує** колонок підтвердження нічного часу: за рішенням Gate D
quiet hours — жорстка заборона на рівні валідації (§10), тож підтверджувати нема чого.

### 6.3 Ролі та GRANT-матриця (DDL — лише `migrator`)

- `api_rw`: повні DML на схему `diary`; на `reminders`: SELECT/INSERT/UPDATE/DELETE `reminder_schedule` (провізія, деактивація, reconciler), SELECT/DELETE `reminder_delivery` (erasure-purge і housekeeping), INSERT `message_cleanup`.
- `reminder_worker`: SELECT/UPDATE `reminders.reminder_schedule`; SELECT/INSERT/UPDATE `reminders.reminder_delivery`; SELECT/DELETE `reminders.message_cleanup`; **нуль привілеїв на `diary`** (permission denied — під тестом, позитивним і негативним).
- `reminder_worker` **не отримує `DELETE` на `reminder_delivery`**: housekeeping (TTL 14 днів) виконує `outbox_dispatcher` роллю `api_rw`. Це фіксується окремим негативним тестом — інакше при першій же спробі прибирання хтось «полагодить» GRANT і мовчки зруйнує тест ізоляції Фази 4.

### 6.4 Чесна модель видалення

- Видалення = hard DELETE. Для sync рядок живе як `deleted=true` (payload обнуляється негайно) протягом tombstone-TTL, потім фізично видаляється компактором. Компактор в одній транзакції з DELETE просуває `compacted_up_to = GREATEST(compacted_up_to, max(revision видалених))`.
- Історія версій не зберігається (last-write only); жодного event-sourcing медичного вмісту.

**Один горизонт, решта — похідні.** Три числа, що жили окремо, зводяться до одного
продуктового горизонту **H = 180 днів** — це єдине число, яке бачить користувачка:

| Величина | Значення | Роль |
|---|---|---|
| `H` | 180 днів | продуктовий горизонт; клієнтський детектор застарілості спрацьовує рівно на ньому |
| `T_tomb` | 187 днів | tombstone TTL = `H + Δ` |
| `T_journal` | 187 днів | обрізання журналів видалень усередині payload (§9.3) |
| `Δ` | 7 днів | період компактора + скіс годинників + лаг серіалізації |

Інваріант `T_journal ≥ T_tomb` обов'язковий. Мотив запасу `Δ`: якщо клієнтський guard
спрацьовує пізніше за серверний компактор, шлях 410 стає мертвим кодом, а межові тести
(`H−1` / `H+1`) провалюються незалежно від якості реалізації. У жодному тексті для
користувачки не може з'явитися друге число поруч зі 180.

**Бекапи — дві різні іменовані величини, а не одна.**
`BACKUP_PROMISE_DAYS = 30` — єдине джерело числа для цього розділу і для §7; будь-який
літерал «30 днів» поза ним заборонений (перевіряється grep-правилом у CI).
`BACKUP_CONFIG_MAX_AGE_DAYS = 28` — інфраструктурний інваріант зі статичним assert
`CONFIG_MAX_AGE < PROMISE`. Object Lock у режимі governance — 21 день, свідомо нижче за
точку експірації, щоб lock ніколи не блокував планове прибирання. Оскільки Object Lock
вмикає версіонування, обов'язкове правило `NoncurrentVersionExpiration = 28 днів` на обох
backup-бакетах: без нього delete-маркери лишають noncurrent-версії назавжди і обіцянка стає
неправдою. Доказом виконання є `ListObjectVersions`, а не звіт інструмента бекапів.

**Вміст бекапа не описується формулою «лише шифротекст».** У бекапі у відкритому вигляді
лежать `telegram_user_id`, `telegram_chat_id`, набір і час згод, розклад нагадувань і
`tz` (груба геолокація). Зашифровані там лише записи щоденника. Це і є причина, з якої
формулювання нижче переписане.

**Обов'язкове формулювання для користувачки** (єдина редакція, версіонується полем
`deletion_copy_version`; кожна поява числа — посилання на константу вище):

> Видаляємо так: з активних систем — одразу. Відновлювані резервні копії зникають
> щонайбільше через 30 днів; у них записи щоденника лежать лише зашифровано, але технічні
> дані облікового запису — у відкритому вигляді. Якщо ми колись відновлюватимемо систему з
> такої копії, ми повторно виконаємо ваше видалення протягом 24 годин. Окремо до 90 днів
> зберігається технічний запис про сам факт видалення — службовий номер і час; у ньому
> немає ані вашого Telegram, ані записів щоденника.

**Erasure-журнал поза скоупом DB-restore** — append-only object storage в **іншого
провайдера й іншої країни**, ніж БД і бекапи (інакше один інцидент забирає і дані, і доказ
того, що їх треба стерти).

- Формат рядка: `{erasure_ref = HMAC(k_erasure, account_id), at, code, prev_hash}`, де
  `at` округлюється **вгору** до години UTC, `prev_hash` = SHA-256 попереднього рядка
  (ланцюг цілісності), а денний head підписується HMAC ключем контролера.
  `code ∈ {full, sync_off, reminders_off, security_reset}`.
- Retention **J = 90 днів**. Формула мінімуму: `J_min = BACKUP_CONFIG_MAX_AGE + D_rec + M
  = 28 + 1 + 7 = 36`; 90 обрано з запасом і збігається з Object Lock журналу.
- Обрізання — **виключно** lifecycle-правилом бакета. Писач має лише `PUT`; принципала з
  правом `DELETE` не існує. «Активне обрізання застосунком» відкидається як таке, що
  суперечить append-only.
- **Журнал пишеться ПЕРЕД початком видалення**, з `at` = час запиту. Якщо запис не вдався,
  erasure не стартує (ретраї в межах 24 год + алерт). Зворотний порядок дає одноточкову
  відмову: акаунт стерто, запису немає, `erasure_job` зник разом з акаунтом — і після
  будь-якого пізнішого restore акаунт воскресає назавжди. Запис без завершеного стирання
  нешкідливий: він дає лише ідемпотентне повторне стирання.
- Покриття рахується як `min(now − service_start_at, J)`, а **не** як `now − min(at)` —
  інакше інтерлок назавжди заблокує restore на здоровому сервісі з порожнім журналом.
- Журнал класифікується як **псевдонімізовані персональні дані на весь строк**. Твердження
  «через 30 днів він стає анонімним» хибне: для `code ≠ full` акаунт живий і `account_id`
  лишається в `diary.account`.

**Runbook після будь-якого повного restore** (кроки блокуючі, порядок жорсткий): рішення й
авторизація — контролер даних; далі реконсиляція журналу для кожного запису з
**`at >= t_b`** (нестрога нерівність обов'язкова: точку відновлення майже завжди обирають
круглою, і `10:00 > 10:00` не спрацювало б):

| `code` | Дія |
|---|---|
| `full` | повторна повна erasure акаунта |
| `sync_off` | `DELETE vault_record + vault_key` + скидання лічильників (§4.3) |
| `reminders_off` | `DELETE reminder_schedule + reminder_delivery` |
| `security_reset` | `DELETE vault_key + vault_record`, `reset_revision = current_revision + 1` |

Крок `security_reset` закриває дірку, якої не бачив жоден окремий розділ: restore з копії,
знятої **до** re-key (§7), повертає в **живу** систему старий `wrapped_dek` і старий
шифротекст, тобто стара фраза знову працює — і це вже не «копія», а активний сервіс.

- Crypto-erasure як підсилення: при відкликанні `health_sync` видаляється і `wrapped_dek`
  (додаток до TTL-обіцянки, не заміна — старі бекапи містять старий конверт). Ключ шифрування
  бекапів при erasure одного акаунта **не змінюється і не знищується** — він кластерний;
  це фіксується явно, а не замовчується.
- **Telegram — окремий носій із найгіршою політикою з усіх.** Надіслані нагадування лишаються
  в чаті користувачки; бот може видалити власне повідомлення лише протягом 48 годин. Тому
  черга `message_cleanup` (§5.3, §6.2) із жорстким TTL 48 год і безумовним видаленням за TTL.
  Ціна, яку треба визнати в тексті екрана видалення: до 48 годин після видалення на сервері
  лишаються `chat_id` і номери повідомлень — саме щоб їх прибрати.
- **Матриця erasure іменована по кожній із 12 таблиць**, а не задана числом. Окремої уваги
  потребують дві, що не мають `account_id` у звичному місці: `outbox` (ідентифікатор лежить
  усередині `payload`) і `auth_replay` (PK — `initdata_hash`, колонки акаунта немає взагалі,
  тож твердження «нуль рядків для акаунта» там не виражається і замінюється TTL 48 год).

### 6.5 Data residency

Рішення Gate D. Увесь серверний набір даних класифікується як **особлива категорія**
(GDPR Art. 9, дані про здоров'я) попри client-side E2E: сам факт існування акаунта в цьому
сервісі, `telegram_user_id`, набір активних згод і `tz` є даними про здоров'я незалежно від
того, що записи зашифровані. Правова база — Art. 6(1)(a) + Art. 9(2)(a) (явна згода);
для української юрисдикції — ст. 11 і ст. 7 ЗУ 2297-VI.

**Усі носії з персональними даними — виключно в ЄЕЗ.** Нуль production-даних і нуль
резервних копій в Україні. Єдиний неминучий компонент поза ЄЕЗ — Telegram (нижче).

| # | Носій | Країна | Що зберігається | Retention | Субпроцесор |
|---|---|---|---|---|---|
| 1 | Primary DB (PostgreSQL 16) | DE | схеми `diary` + `reminders` | до erasure | так |
| 2 | Base backups + WAL, репо 1 | FI | шифрований дамп кластера | ≤28 днів | так |
| 3 | Base backups + WAL, репо 2 | FR | те саме, інший провайдер | ≤28 днів | так |
| 4 | Erasure-журнал | FR | `erasure_ref`, час, код | 90 днів | так |
| 5 | Логи застосунку | DE, той самий вузол | allowlist §11, `account_ref` | 7 днів | ні |
| 6 | Метрики | DE, той самий вузол | лише агрегати, нуль per-account міток | 90 днів | ні |
| 7 | Статика Mini App | DE, той самий вузол | публічний бандл | — | ні |
| 8 | CI | США | публічний код, синтетичні фікстури | — | даних користувачок немає |
| 9 | Telegram | поза ЄЕЗ | `telegram_user_id`, факт і час повідомлень | поза нашим контролем | **ні — окремий контролер** |

Репліка у Фазах 1–4 **не розгортається**: RPO ≤ 60 с, RTO ≤ 4 год, жодного SLA доступності
не обіцяємо. Обґрунтування — щоденник повністю робочий локально без сервера (§2), тому
простій не є ризиком для здоров'я. Error tracking (серверний і клієнтський) у v1
**не розгортається**; браузерний Sentry заборонений назавжди.

**Telegram — незалежний контролер, а не наш процесор.** Договір за Art. 28 з ним укласти
неможливо: Bot Platform ToS не містить умов Art. 28 і перекладає визначення застосовності
права на розробника. Отже це розкриття «контролер → контролер», запобіжник — Art. 49(1)(a)
(явна поінформована згода), інструмент Art. 46 недоступний. Для української аудиторії
паралельно застосовується ст. 29 ч. 3 п. 1 ЗУ 2297-VI. **Наслідок, обов'язковий до
виконання:** текст згоди `telegram_reminders` мусить дослівно містити відсутність рішення
про адекватність і відсутність інструменту Art. 46 — інакше єдиний заявлений механізм
передачі юридично не встановлений.

**Будь-яке додавання носія, провайдера, регіону або субпроцесора — включно з тимчасовим
(моніторинг на тиждень, CDN на час навантаження, хостований лог-сервіс для дебагу) —
автоматично відкриває повторний Gate D** і не може бути зроблене оператором самостійно.

**Операційна частина резидентності не живе в цьому репозиторії.** Репозиторій публічний,
тому імена бакетів, хости, облікові записи, модель кустодії ключів і процедура їх ротації
зберігаються поза git. Тут — лише таблиця вище.

## 7. Шифрування: client-side E2E

- **Рівень: app-level на клієнті (E2E).** WebCrypto **AES-256-GCM** per record. Чому не pgcrypto і не лише диск: ключ у сервера означав би, що «сервер не аналізує вміст» тримається на чесному слові; E2E робить це криптографічним фактом. Disk encryption — додатковий шар хостингу.
- **Формат payload:** `0x01 ‖ nonce(12 B) ‖ ciphertext ‖ tag(16 B)`. Версійний байт мусить збігатися з версійним префіксом AAD.
- **Nonce:** 12 байт свіжої випадковості `crypto.getRandomValues` на кожну операцію шифрування; заборонено виводити nonce з `record_key`/revision/лічильника/часу. Ротація DEK через nonce-бюджет не потрібна (бюджет NIST 2^32 операцій недосяжний для щоденника однієї користувачки — зафіксовано свідомо).
- **Реальний ризик тут — не математика, а реалізація**, тому фіксуються три механічні правила: (1) публічний API крипто-модуля має вигляд `encrypt(plaintext, aad)` — параметра `nonce` немає і не з'явиться; (2) nonce генерується всередині виклику і ніколи не зберігається окремо від свого ciphertext; (3) при ретраї чанка (§9.5) клієнт надсилає **байт-у-байт ідентичний** раніше зашифрований payload і ніколи не перешифровує його наново.
- **AAD = `"ndv1" ‖ 0x1F ‖ шлях ‖ 0x1F ‖ client_ts_ms ‖ 0x1F ‖ ("0"|"1")`** — однозначне кодування замість простої конкатенації (інакше межі полів неоднозначні). `client_ts_ms` — десяткове ціле, мілісекунди UTC. `шлях` — закритий перелік, що валідується `^(entry:\d{4}-\d{2}-\d{2}|cycle|catalog|groups|settings|manifest)$`. Server-assigned `revision` в AAD не входить (невідома при шифруванні).
- **Plaintext-заголовка шляху немає.** Попередня редакція клала логічний шлях у автентифікований plaintext-заголовок запису — і цим прямо суперечила §6.1 («сервер не знає навіть дат записів»), а заразом робила неправдивими тексти для користувачки одразу в трьох пунктах Gate D, включно з обіцянкою про вміст резервних копій. Заголовок прибрано. Клієнт володіє `k_index`, тож мапування `record_key → шлях` відновлюється офлайн: скінченний простір (усі дати діапазону щоденника + п'ять синглтонів) обчислюється в HMAC-таблицю один раз. На pull клієнт перевіряє `HMAC(k_index, шлях) == record_key`, під яким сервер віддав запис; розбіжність → запис відхиляється, не мерджиться, нейтральна помилка цілісності. Гарантія та сама, розкриття — нульове.
- **Anti-rollback: шифрований синглтон `manifest`** (під тим самим DEK), оновлюється в тому ж push: клієнтський монотонний `vault_seq` + HMAC(`k_auth`) над станом сейфа, розширений **дайджестом вмісту**. Канонічний рядок: `"ndv1-manifest" ‖ 0x1F ‖ vault_seq ‖ 0x1F ‖ key_version ‖ 0x1F ‖ n_live ‖ 0x1E`, далі для кожного запису в порядку зростання `record_key` — `record_key_hex ‖ 0x1F ‖ client_ts_ms ‖ 0x1F ‖ sha256(payload) ‖ 0x1E`.
- **Manifest визначається над живими записами (`deleted = false`); сам запис `manifest` до нього не входить** — ані до переліку, ані до `n_live`. Це не деталь: якби manifest покривав tombstone, штатний компактор (§6.4) щоразу робив би серверний набір строгою підмножиною зафіксованого, і користувачка бачила б «помилку цілісності» без жодної атаки — рівно тоді, коли приходить на повний ресинк після 410. Приховування tombstone сервером уже закрите правилом авторитетності присутності (§9.4) і push-гейтом `base_revision < compacted_up_to`. DoD Фази 2 перевіряє обидві гілки: «вилучено живий запис → перевірка падає» **і** «вилучено compacted tombstone → перевірка проходить».
- Повний pull — включно з онбордингом нового пристрою — верифікується проти manifest; кожен пристрій персистить highest-seen `(revision, vault_seq)` і відхиляє відповіді з нижчими значеннями. Провал перевірки → нейтральна помилка «серверна копія виглядає застарілою», локальні дані первинні.
- **Ієрархія ключів.** Кореневий секрет `R` = 32 випадкові байти; саме `R` обгортається KEK і лежить на сервері у `vault_key.wrapped_dek`. З `R` через HKDF-SHA256 виводяться три незалежні підключі з фіксованими info-рядками: `k_enc` (`"ndv1:enc"`, вміст записів), `k_index` (`"ndv1:index"`, HMAC для `record_key`), `k_auth` (`"ndv1:auth"`, HMAC для manifest).
- **KDF — Argon2id (WASM)**, параметри: `m = 65536 KiB (64 MiB)`, `t = 3`, `p = 1`, сіль 16 байтів, вихід 32 байти. `p = 1` свідомо замість `p = 4` з RFC 9106: у WebView паралелізм не дає виграшу, а пам'ять — дає.
- **Fallback — PBKDF2-HMAC-SHA256** через WebCrypto (нативний, нульова вартість бандла), ціль 1 000 000 ітерацій, підлога 600 000 (чинна рекомендація OWASP).
- **Правило прийняття рішення після бенчмарку фіксується наперед**, щоб бенчмарк не перетворився на переговори. Еталонні пристрої: A — low-end Android (Android 11+, 4×Cortex-A53, 2–3 ГБ RAM), вимір саме у вбудованому WebView Telegram, не в окремому Chrome; B — iPhone SE (2020); C — desktop Chrome як контроль. Поріг: **p95 ≤ 3000 мс** на пристрої A з обов'язковим індикатором прогресу. Не проходить → спускатися щаблями `m` (64 → 47 МіБ), і лише вичерпавши їх — переходити на PBKDF2 з калібруванням у межах [600 000; 1 200 000].
- **Клієнтська перевірка підлоги параметрів.** `kdf_params` приходять із сервера (це необхідно для онбордингу нового пристрою і не є секретом), але клієнт **відхиляє** отримані параметри, якщо вони нижчі за підлогу: `argon2id` з `m_kib < 47104`, або `t < 1`, або довжина солі < 16. Без цієї перевірки скомпрометований сервер міг би віддати параметри рівня «одна ітерація» і перетворити фразу на майже відкритий текст.
- **`kdf_params` в AAD конверта бінди явним 0x1F-кодуванням, а не канонічним JSON.** Колонка має тип `jsonb`, який не зберігає порядок ключів і нормалізує числа й пробіли, тож JSON-серіалізація недетерміновано ламала б розпакування конверта. Тип колонки при цьому не змінюється.
- **CAS для конверта — за `wrap_version`, а не за `key_version`.** Re-wrap не змінює `key_version`, тому два конкурентні re-wrap із різних пристроїв обидва проходили б CAS, і одна з нових фраз мовчки губилася б — користувачка вважала б встановленою фразу, якою конверт уже не відкривається. `key_version` змінює тільки re-key.
- **Step-up обов'язковий на будь-який запис у `/v1/sync/key`** — обидва режими. Аргумент «re-wrap безпечний, бо `R` не зберігається на пристрої» описує властивість *нашого* клієнта: зловмисник із Bearer-токеном надішле довільні байти як `wrapped_dek`, а сервер за визначенням zero-knowledge і перевірити нічого не може. Оскільки `R` не персиститься ніде, серверна копія після такого перезапису непоправна назавжди. Додатково `vault_key` тримає `wrapped_dek_prev` із TTL 7 днів (перезаписується не частіше ніж раз на 24 год, віддається лише при step-up) — це нічого не послаблює, але робить помилковий або зловмисний перезапис оборотним.
- **Дві різні операції з фразою.** «Зміна фрази» = нова сіль → новий KEK → той самий `R` переобгортається; сейф не чіпається, `wrap_version + 1`. Обов'язкова передумова: користувачка вводить **поточну** фразу — операція неможлива з кешованих підключів, бо `R` на пристрої не зберігається. «Re-key при компрометації» = **окрема явна дія** в інтерфейсі («Замінити ключ»), а не автоматичний наслідок зміни фрази: новий `R` → нові підключі → локальне перешифрування → step-up → vault-reset → чанковане перезавантаження → конверт із `key_version + 1` → ревокація інших сесій.
- **Політика фрази.** Основний і попередньо обраний шлях — **фраза, згенерована застосунком**: 6 слів із вбудованого українського словника на 4096 слів, тобто рівно 72 біти ентропії. Власна фраза дозволена як другий варіант із жорсткими мінімумами: ≥12 символів **і** ≥3 слова; якщо слово одне — ≥16 символів. Числовий або кольоровий індикатор сили **не показується** (він створює хибне заспокоєння замість твердого правила допуску). Підказка до фрази (hint) **не реалізується** — ні серверна, ні локальна. Підтвердження введення обов'язкове: згенеровану фразу треба ввести повністю на окремому екрані.
- **Recovery-файл (§13.2) відхилено** в усіх криптографічних формах: ні файлу з ключем, ні другого конверта під кодом відновлення, ні «ключа відновлення» як другого KEK. Кожен із них — це другий, слабший шлях до тих самих даних, який знецінює всю модель. Штатне відновлення при втраті фрази — **створити нову серверну копію з локальних даних**: задати нову фразу, згенерувати новий `R`, виконати vault-reset і завантажити щоденник заново.
- Втрата фрази = втрата серверної копії, **не даних** (локальна копія первинна).

## 8. Автентифікація Telegram Mini App

За офіційною документацією Telegram (core.telegram.org/bots/webapps, «Validating data received via the Mini App»):

- **Обраний механізм: третьосторонній Ed25519-варіант** — перевірка поля `signature` (base64url) з data-check-string `"<bot_id>:WebAppData\n" + поля initData (без hash і signature, алфавітно)` проти публічного ключа Telegram (production `e7bf03a2fa4602af4580703d88dda5bb59f32ed8b02a56c187fe7d34caed242d`). Мотивація: api не мусить володіти `BOT_TOKEN` — токен лишається лише в бота й воркера. `bot_id` надходить в api окремою **несекретною** змінною `TELEGRAM_BOT_ID` (числовий префікс токена; сам токен api заборонений).
- **Fallback за feature-flag** (вимкнений за замовчуванням): якщо реальні 401-рейти покажуть частку клієнтів без `signature`, api отримує лише **похідний** `WEBAPP_HMAC_SECRET = HMAC_SHA256(BOT_TOKEN, key="WebAppData")`, обчислений одноразово поза api при деплої; `BOT_TOKEN` в env api не потрапляє ніколи (похідний ключ не дає доступу до Bot API). Тест: конфіг api не містить `BOT_TOKEN` незалежно від стану флага.
- **TTL: `auth_date` ≤ 24 год.** initData видається один раз при відкритті Mini App і не оновлюється без перезапуску, тому короткий TTL (хвилини) несумісний із флоу «згода — явна пізня дія». Прострочений → 401 `auth_stale`; клієнт показує «перезапустіть застосунок через кнопку бота».
- **Anti-replay:** сесія видається щонайбільше один раз на конкретний initData — сервер тримає SHA-256-хеш валідованого initData (`auth_replay`) до спливу TTL; повторна подача → 401. Хеш — не initData і не медичні дані; у лог-allowlist не входить.
- **Атомарний перший вхід** (замикає «акаунт лише зі згодою»): `POST /v1/auth/telegram {init_data, grant?: {kind, text_version}}`:
  1. `telegram_identity` існує → сесія + список consents;
  2. акаунт відсутній і `grant` відсутній → 403 `no_account`, **нуль рядків у БД**, initData не персистується — сервер і далі «не знає нічого»;
  3. акаунт відсутній + `grant` присутній → одна транзакція: INSERT account + telegram_identity + consent + session_token.
  Клієнтський флоу: діалог згоди — локальний і до будь-якого мережевого виклику; скасування → жодного запиту → жодного акаунта.
- **Ідентичність:** з initData береться лише `user.id`.
- **Сесії: opaque tokens** (випадкові 256-bit; у БД лише SHA-256-хеш), TTL 7 днів, sliding ≤30 днів; передача лише `Authorization: Bearer`, ніколи в URL. Не JWT: потрібна миттєва ревокація при відкликанні згоди/erasure. `GET /v1/sessions` + `POST /v1/sessions/revoke-others`.
- **Step-up — точний перелік (рішення Gate D).** «Свіжа» сесія = валідація initData ≤10 хв тому; інакше 403 з кодом, що веде на повторне відкриття Mini App (природний step-up, нуль нової криптографії).

  | Операція | Step-up | Підстава |
  |---|---|---|
  | Відкликання будь-якої згоди | **Ні** | Art. 7(3): відкликати має бути так само легко, як надати |
  | Повне видалення акаунта | **Так** | Art. 12(6) — підтвердження особи за обґрунтованих сумнівів; це Art. 17, а не відкликання згоди |
  | `vault-reset`, `re-key` | **Так** | незворотна втрата серверної копії |
  | Будь-який запис у `/v1/sync/key` | **Так** | §7: сервер не може перевірити конверт, помилковий запис незворотний |

  Компенсаційний захід замість step-up на відкликання `health_sync` — **серверний**, а не «rate limit 1/хв» (останній фіктивний: руйнівний ефект досягається першим викликом). Якщо `max(last_acked_revision) < current_revision`, тобто на сервері є дані, яких немає на цьому пристрої, revoke повертає 409 `confirm_required` і потребує повторного виклику з `acknowledge_incomplete=true`. Сервер тут дивиться лише на ревізії й не аналізує вміст.
- **`min_auth_date` — конфігураційний прапорець валідатора initData.** Після відновлення з бекапа таблиця `auth_replay` теж відкочується, тож раніше використані initData знову стають «новими». Прапорець відсікає все, що старше за момент відновлення, і є обов'язковим кроком runbook (§6.4).
- **Клієнтська гігієна initData** (вимоги до `apps/web/src/sync/`): initData читається один раз, тримається в пам'яті модуля (не в state/persist/localStorage); одразу після читання — `history.replaceState` для зачистки `#tgWebAppData` та видалення `__telegram__initParams` із sessionStorage; клієнтська лог-дисципліна дзеркалить серверний allowlist (заборона `location.href`/hash/initData у будь-якому логері/error-handler).
- initData не логується й не персистується.

## 9. Синхронізація: offline-first протокол

### 9.1 Push-транзакція (жорсткий порядок; єдине джерело ревізій)

1. `SELECT … FOR NO KEY UPDATE` на рядку `account` — фіксований перший лок (бар'єр проти erasure, §9.8).
2. `INSERT INTO vault_revision(account_id, current_revision) VALUES ($1, 0) ON CONFLICT DO NOTHING` (гонка першого пуша).
3. `SELECT current_revision, compacted_up_to, reset_revision … FOR UPDATE` — per-account лок до будь-яких читань/записів `vault_record`.
4. У тій самій транзакції: повторна перевірка `account.status='active'` + активної `consent(health_sync)`; інакше 403 без запису.
5. Guards: `base_revision < reset_revision` → 409 `vault_reset`; `base_revision < compacted_up_to` → 410.
6. Per-key conflict-check **під локом** (поза локом результат недійсний): будь-який record_key із серверною revision > base_revision → 409 зі списком ключів.
7. UPSERT записів; наостанок `UPDATE current_revision += N`.

Per-account `FOR UPDATE` повністю серіалізує пуші акаунта (усуває TOCTOU lost update, deadlock на порядку UPSERT, гонку першого пуша); retry при 40001/40P01 — захисний пояс. Свідомо не `txid_current()`/sequence: комміти завершуються не по порядку, курсор губив би ревізії. Для 1–3 пристроїв ціна серіалізації нульова.

### 9.2 Ендпоінти

```
POST /v1/sync/push   {base_revision, changes:[{record_key, payload_b64 | tombstone:true, client_ts_ms}]}
                     → 200 {new_revision} | 409 {reason:"conflict", conflict_keys} | 409 {reason:"vault_reset"}
                     | 409 {reason:"consent_precondition"} (запис домену з неактивною згодою; нуль записів)
                     | 410 (за горизонтом компакшну) | 413 | 429
GET  /v1/sync/pull   ?since=<rev>&limit=500
                     → 200 {records:[…], next_since, current_revision, reset: bool,
                            consent_state_changed: bool}   -- нейтральне, без назви kind
                     | 410 Gone (since < compacted_up_to → повний ресинк; reset-стан повертається першим)
GET  /v1/sync/key    → {wrapped_dek, kdf, kdf_params, key_version, wrap_version}
POST /v1/sync/key    {mode:"rewrap"|"rekey", expected_wrap_version, wrapped_dek, kdf, kdf_params}
                     → 200 | 409 {current_wrap_version}   (CAS по wrap_version; step-up обов'язковий)
POST /v1/account/vault-reset    (step-up; атомарно: DELETE всіх vault_record + reset_revision = нова current_revision)
GET  /v1/consents               (без параметрів)
POST /v1/consents               {kind, text_version, text_sha256, settings?}
POST /v1/consents/revoke        {kind}   -- kind у ТІЛІ, ніколи в URL
```

**Назва згоди не з'являється в URL — це вимога §2, а не стилістика.** Шлях виду
`DELETE /v1/consents/cycle_sync` потрапляє в request line reverse proxy, а набір активних
згод сам по собі є health-inference (§13.12). CI-тест по OpenAPI: множина path-шаблонів не
перетинається з множиною значень `kind`.

**Grant — один контракт для всіх трьох згод.** Поле `settings` обов'язкове тоді і тільки
тоді, коли `kind == 'telegram_reminders'`, і дорівнює `{time, timezone}`. Це заразом дає
провізії розкладу (§4.4) джерело для `local_time` і `tz`, якого в попередній редакції не
було взагалі. `text_sha256` перевіряється сервером проти його власного реєстру текстів:
збігу немає → 409, згода не створюється.
Усі — за dependency `require_consent("health_sync")` + повторною перевіркою у транзакції (§9.1).

### 9.3 Конфлікти: pull-merge-push (optimistic concurrency)

Сервер не вміє merge (ciphertext) → 409 → клієнт робить pull, merge локально в plaintext, повторний push. Правила merge (детерміновані на всіх пристроях):
- per-record LWW за **автентифікованим** `client_ts` (зсередини payload; серверна колонка — лише індексна копія); при рівних ts — стабільний tiebreaker: per-device випадковий `device_id` усередині шифрованого payload, лексикографічно. Пріоритетніші правила: `DraftEntry` ніколи не перемагає `DoneEntry` тієї ж дати; tombstone ≥ update.
- Синглтони `cycle`, `catalog`, `groups` — **поелементний merge** (2P/OR-set): множина елементів з `added_at`/`updated_at` + журнал видалених/архівованих `{id|date, removed_at}` **у складі шифрованого payload** (синхронізується як ciphertext; сервер нічого не бачить). Merge per елемент: перемагає операція з максимальним timestamp; tie → remove. Для `cycle`: `{date, added_at}` + журнал `{date, removed_at}`; повторне додавання (MENSES_CONFIRM після видалення) пише новий `added_at`. Retention журналів: записи, старші за `T_journal` (§6.4), обрізаються при серіалізації. Обґрунтування — саме інваріант `T_journal ≥ T_tomb`, а не «довший офлайн → 410»: останнє хибне, бо поелементне видалення всередині синглтона **не** створює `deleted=true` на рівні `vault_record`, тож 410 тут не спрацьовує і сам по собі нічого не гарантує.
- `settings` — whole-record LWW (tie закриває tiebreaker).
- Клієнтський sync-стан per record: `last_acked_revision`/прапорець `dirty` — основа пост-410 merge (§9.4).

CRDT повного профілю не виправданий (одна користувачка, 1–3 пристрої).

### 9.4 Tombstones, компакшн, захист від воскресіння

Відповідність шести гранулярностям видалення web:

| Дія у web | Sync-подія |
|---|---|
| `ENTRY_DELETE` | tombstone `entry:<date>` |
| `MENSES_REMOVE` | нова версія `cycle` з оновленим журналом видалень у payload |
| `DATA_DELETE {scope:'cycle'}` | tombstone `cycle` (чистить і множину, і журнал) |
| `GROUP_DELETE` | нова версія `groups` (журнал видалених id; значення симптомів не чіпаються) |
| `FLARE_DELETE` | нова версія `entry:<date>` |
| `DATA_DELETE {scope:'all'}` | `POST /v1/account/vault-reset` (step-up; guard нижче) |

Повний захист від воскресіння видаленого:
- активне вікно: tombstone тримає монотонну revision `T_tomb` (§6.4); push застарілого пристрою → 409 → pull → tombstone застосовується. Клієнт паралельно тримає `last_successful_sync_at` і застосовує власний детектор застарілості з порогом `H` — рівно тим числом, яке бачить користувачка;
- push-гейт: `base_revision < compacted_up_to` → 410 — застарілий пристрій не може пушити, доки не зробить повний ресинк (закриває push-без-pull після компакшну);
- пост-410 merge (правило авторитетності присутності): локальний запис, який був **clean** (має `last_acked_revision`) і відсутній у повному серверному наборі → трактується як видалений віддалено → видаляється локально **з підтвердженням на пристрої**; **dirty** та ніколи-не-синхронізовані записи мерджаться і пушаться; крайовий кейс «acked, локально відредагований, на сервері відсутній» → явне підтвердження користувачки. Локальний кеш tombstone-ів не є альтернативою: пристрій, що не бачив видалення, tombstone не має;
- vault-reset: push-guard `base_revision < reset_revision` → 409 `vault_reset` до per-key перевірки; клієнт зобов'язаний зробити pull, показати підтвердження очищення на пристрої і лише після рішення користувачки пушити з актуальним base_revision. `reset_revision` не компактиться; pull зі `since < reset_revision` містить `reset:true`; 410-ресинк повертає reset-стан першим;
- захист від масового видалення вкраденою сесією: якщо один pull-батч приносить tombstones для >20% і ≥10 локальних записів → трактується як reset-еквівалент → те саме підтвердження на пристрої (правило клієнтське; сервер вміст не аналізує);
- **виняток із правила авторитетності присутності для неактивних згод.** Запис, логічний шлях якого належить домену з **неактивною** згодою, з правила виключається, і pruning узагалі не виконується без свіжої відповіді `GET /v1/consents`, не старшої за цей самий pull. Без винятку відкликання `cycle_sync` призводило б до найтяжчого можливого дефекту: сервер видаляє записи циклу (§9.7), пристрій B із активною `health_sync` рано чи пізно отримує 410, робить повний ресинк, не знаходить їх у серверному наборі — і **стирає локальні `cycleStarts`**. Відкликання згоди на синхронізацію ніколи не має видаляти локальні дані.

### 9.5 Initial upload / re-key / backpressure

Перший push після згоди: повний снапшот чанками ≤200 records / ≤1 MiB, `base_revision=0`, послідовно; обрив → retry чанка (ідемпотентно по record_key+вміст; часткова ревізія, видима іншому пристрою, — коректний стан optimistic-протоколу). **`manifest` оновлюється в кожному чанку, а не один раз наприкінці**: інакше посилений manifest (§7) оголошує проміжний стан помилкою цілісності — а це саме те вікно, у яке інші пристрої гарантовано потрапляють після re-key, бо вони вже змушені на повний ресинк. Клієнт персистить `sha256(payload)` per record разом із `last_acked_revision`: в інкрементальному режимі він тримає plaintext, а не байти шифротексту, і без збережених дайджестів не може перерахувати manifest, не перешифрувавши все (що заборонено правилом nonce, §7). Ліміт payload per record 64 KiB → 413; per-account rate limit → 429 + `Retry-After`; експоненційний backoff. Re-key (§7) = vault-reset + повторний upload під новим DEK в одному клієнтському флоу.

### 9.6 Демо-дані при першому синку

Серверна евристика неможлива і заборонена (аналіз вмісту порушував би §2; дані структурно нерозрізнювані). У production `load()` → `emptyData()`, `genDemo` викликається лише з тестів/e2e → ризик існує тільки в тест-збірках. Рішення: (а) sync-модуль вимкнений build-флагом у test/e2e-збірках, окрім спеціальної sync-e2e збірки, що ходить лише на локальний тестовий api; (б) CI-перевірка, що prod-бандл не містить `genDemo`. Залишковий ризик документується (§13).

### 9.7 Згода `cycle_sync` при E2E

Сервер не бачить, який запис є `cycle` (ключі непрозорі) → фільтрація за згодою — клієнтська: без активної `cycle_sync` клієнт не включає `cycle` у push. **Відкликання, однак, виконується сервером, а не клієнтським tombstone.**

- Клієнт надсилає при grant `cycle_sync` значення `record_key_cycle = HMAC(k_index,'cycle')`, яке зберігається в рядку згоди. Тегувати кожен запис колонкою `domain` не потрібно: витік той самий (один біт), але вартість нульова — нуль змін AAD, нуль змін DTO push, нуль `CHECK`, який довелося б мігрувати при появі другого домену.
- При відкликанні — **hard DELETE під локом §9.1 з інкрементом ревізії, у тій самій транзакції, що й `consent.revoked_at`, без tombstone.** Одиничний tombstone на синглтон `cycle` саме тоді, коли сервер бачить свіжий `revoked_at('cycle_sync')`, детерміновано деанонімізує цей `record_key` — і ретроспективно всю історію його оновлень. Це рівно та проблема, якої вся схема непрозорих ключів мала уникнути.
- У відповідь pull додається нейтральне `consent_state_changed: true` (без назви kind), яке зобов'язує клієнт перечитати `GET /v1/consents` **перед** будь-яким pruning (§9.4).
- Серверний гейт на push: якщо серед `changes` є запис домену, згода на який неактивна → 409 `consent_precondition`, нуль записів. Без нього пристрій B, який ще не знає про відкликання, тихо воскрешає дані циклу.

Чесно документується (включно з user-facing текстом згоди): повний серверний enforcement за E2E неможливий — сервер не може відрізнити ciphertext циклу від будь-якого іншого, доки клієнт не назве ключ. Гарантія — клієнтський код, контрактні тести і серверне видалення за названим ключем. Опція на майбутнє — окремий крипто-домен/DEK для cycle — §13.

### 9.8 Серіалізація erasure з in-flight push

Erasure-транзакція воркера (DELETE `vault_record` + `vault_key`) починається з того самого лока рядка `account` (`FOR NO KEY UPDATE`), що і push (§9.1, крок 1) — бар'єр: будь-який push або закомітився до бар'єра (його рядки видаляє свіжий снапшот DELETE), або стартує після і падає на in-txn перевірці згоди. `status='erasing'` — лише для повного `AccountErasureRequested`, не для часткового revoke `health_sync`. Тест гонки — у DoD Фази 3.

## 10. Нагадування (Telegram-доставка)

Повний продуктовий контракт живе лише в [future-telegram-reminders.md](future-telegram-reminders.md) і є невід'ємною частиною цього плану. Зокрема: fresh explicit opt-in, незалежний від health-sync; окремий ресурс `ReminderSettings` з `enabled`, валідованим `HH:mm` та IANA timezone, який не входить до `AppData` і не приймається/не повертається diary/export-схемами; API — єдине джерело правди для згоди, розкладу, timezone, due-рішення та dedupe; stateless доставка і private chats only; статичний exact allowlist; жодного browser/system push; snooze поза першою версією; quiet hours визначаються один раз після review і не дублюються між шарами.

Технічна реалізація контракту:

- **Мапінг ресурсу**: `ReminderSettings {enabled, time, timezone}` ↔ рядок `reminders.reminder_schedule` (+ службові `next_fire_at`, `disabled_reason`, `telegram_chat_id`). Ендпоінти `GET/PUT /v1/reminders/settings` за `require_consent("telegram_reminders")`; PUT повертає канонічний ресурс.
- **Мапінг «stateless bot» із контракту**: «мінімальна delivery-команда» = заклеймлений occurrence `(account_id → telegram_chat_id, local_date)` з reminders-схеми; виконавець — `reminder_worker` (процес api-кодобази з BOT_TOKEN), який надсилає allowlisted повідомлення і підтверджує результат статусом у `reminder_delivery`. Воркер не читає записи щоденника (GRANT + import-linter, §5.3); `apps/bot` як процес не розширюється і стану не має. Властивості контракту (bot не має стану, не читає щоденник, API — єдине джерело due/dedupe) виконані буквально.
- **Розклад**: `tz` — IANA (валідація `zoneinfo.ZoneInfo`), `local_time` — wall-clock; `next_fire_at` (UTC) передобчислюється при кожній зміні розкладу і після кожного спрацювання.
- **DST** (`zoneinfo`): неіснуючий локальний час (spring forward) → перший валідний інстант після gap; подвійний (fall back) → перше входження (`fold=0`). Обов'язковий алгоритм: взяти наступну календарну дату в `tz`, скомбінувати з `local_time`, розв'язати за цими правилами і **лише тоді** конвертувати в UTC. Обчислювати `next_fire_at` додаванням `timedelta` до попереднього значення заборонено — саме так DST-помилки й з'являються.
- **Фікстури DST для DoD Фази 4** (Europe/Kyiv): переходи `2026-03-29 01:00Z`, `2026-10-25 01:00Z`, `2027-03-28 01:00Z`, `2027-10-31 01:00Z`. Досяжний інтеграційний шлях для розкладу 20:00: `2026-03-28T18:00:00Z → 2026-03-29T17:00:00Z` (інтервал рівно 23 год) і зворотний перехід із інтервалом 25 год.
- **`tzdata` — явна запінена залежність `apps/api`**, а не tzdata хоста; версія фіксується в `uv.lock`, окремий тест стверджує очікувану версію. Оновлення tzdata — свідома зміна з перепрогоном фікстур.
- **Timezone валідується лише через `zoneinfo.ZoneInfo(value)` і зберігається дослівно**; автоматична канонізація застарілих аліасів не виконується. Невідома зона → 422 `unknown_timezone`.
- **Quiet hours — рішення Gate D: `[22:00, 08:00)`, жорстка заборона.** Предикат `in_quiet(t) := t >= 22:00 OR t < 08:00`. `08:00` дозволено, `07:59` — ні; `21:59` дозволено, `22:00` — ні. `PUT /v1/reminders/settings` із забороненим `local_time` → **422 `quiet_hours_violation`**; підтвердити нічний час не можна, поля-обходу не існує. Клінічна підстава: порушення сну — тригер мігрені та знижує судомний поріг, тобто нічне сповіщення від цього застосунку шкодило б саме тому, заради чого його ведуть. Ціна рішення визнається прямо: користувачка з нічним графіком не зможе поставити зручний час. Політика живе **єдиною константою в API** (`app/domain/reminders.py`) і експортується у спільну фікстуру web↔api; bot і web її не дублюють.
- **Наявні розклади при зміні політики** не вимикаються і не переносяться автоматично: воркер обчислює `may_send`, і якщо локальний час фактичної відправки потрапляє в заборонений діапазон — пише `reminder_delivery(status='skipped_quiet')` за заклеймлену добу, нічого не надсилає і перераховує `next_fire_at`.
- **Guard застосовується до фактичного часу відправки, а не до запланованого.** Наслідок, який має бути закріплений тестом: розклад 21:30, затриманий на 40 хв, дає `skipped_quiet` (фактичний час 22:10).
- **Час за замовчуванням — 20:00**, і це префіл у пікері web, а **не** серверний дефолт: API не має значення за замовчуванням для `time` і відхиляє провізію без явного значення.
- **Простій воркера**: catch-up лише якщо `now − next_fire_at ≤ 60 хв` І та сама локальна доба І поза quiet hours → надіслати; інакше `skipped_stale` + перерахунок на наступну добу. Поріг зменшено з 4 год: нагадування «зроби короткий запис» через чотири години після обраного часу вже не виконує своєї функції, зате гарантовано приходить у момент, якого користувачка не обирала. Жодних наздоганянь за минулі дні.
- **Ідемпотентність (atomic claim/ack/dedupe)**: **insert-before-send** — `INSERT reminder_delivery(status='pending')` (PK `(account_id, local_date)` атомарно клеймить добу) → `sendMessage` → `UPDATE status='sent'`. Збій між send і confirm → рядок лишився `pending`; політика **at-most-once**: pending старше 15 хв → `failed` без повторної відправки цієї доби (краще одне пропущене нейтральне нагадування, ніж дубль). Окремий outbox не потрібен: PK і є ідемпотентним ключем occurrence. **Жорсткий інваріант порядку:** сумарний бюджет однієї спроби (включно з очікуваннями за `retry_after` на 429) — **≤10 хв**, і воркер сам пише `failed` при його вичерпанні; sweeper із періодом ≤5 хв позначає `failed` лише рядки, старші за 15 хв, і **ніколи** не надсилає повторно. Умова несуперечності — `бюджет спроби < поріг sweeper`.
- **Ізоляція збоїв per-account**: кожен акаунт обробляється у власному `try/except`. Без цього одна зона, що не резолвиться, валить увесь прогін — і вся база втрачає нагадування за добу через одну користувачку.
- **Жоден статус доставки не показується користувачці** як історія і не експонується жодним ендпоінтом як список. `GET /v1/reminders/settings` повертає лише агреговані булеві прапорці поточного стану (`quiet_blocked`, `bot_blocked`). Історія доставок — це часовий ряд взаємодії з медичним застосунком; продукту вона не потрібна й не має існувати в API.
- **API нагадувань не повертає жодного українського тексту.** Помилки — стабільні ASCII-коди в полі `error`: `consent_required` (403), `quiet_hours_violation` (422), `unknown_timezone` (422), `bot_blocked` (409), `rate_limited` (429), `no_schedule` (404). Уся українська копія живе у web і мапиться на код.
- **Housekeeping `reminder_delivery`** — TTL 14 днів, виконує `outbox_dispatcher` роллю `api_rw` (§6.3), бо `reminder_worker` не має і не отримає `DELETE`.
- **Rate limits Telegram**: пейсинг ~25 msg/s токен-бакетом; honor `retry_after` при 429 (у межах вікна валідності тієї ж доби; send ще не відбувся — з at-most-once не конфліктує); **403 (бот заблоковано)** → `enabled=false, disabled_reason='bot_blocked'` у своїй схемі; reconciler (§4.4) переносить це у `consent.revoked_at` + подію `ConsentRevoked`.
- **Зміст**: точний allowlist зі специфікації підтверджено дослівно, без змін — текст `Час зробити короткий запис` (без кінцевої крапки), кнопка `Відкрити щоденник`, URL — лише конфігурований `WEBAPP_URL` без query-параметрів. Тест-allowlist (як у наявному боті): заборона інтерполяції, `callback_data`, будь-яких сигналів стану щоденника (навіть «ви ще не заповнили» — заборонений медичний сигнал; нагадування «сліпе»). Outbound-рядок лишається **рівно одним**: резервного «повідомлення про інцидент» у Telegram немає — сповіщення за Art. 34 йде банером у Mini App і публічною сторінкою статусу.
- **Правило «нуль інтерполяції» має різну силу на двох поверхнях, і це фіксується явно.** Telegram outbound — інтерполяція заборонена абсолютно, усі рядки є літералами. Web UI — інтерполяція дозволена **виключно** для власних значень розкладу користувачки (`time`, `timezone`) і заборонена для будь-якого вмісту щоденника; єдиний web-рядок з інтерполяцією — «Щодня о {time}».
- **Поверхня Telegram поза текстом повідомлення.** Display name бота, `@username`, `about`, `description`, аватар і домен `WEBAPP_URL` не повинні містити медичних або неврологічних токенів (`невро`/`neuro`, `симптом`, `мігрен`, `епілепс`, `склероз`, `здоров`, `діагн` та латинські відповідники). Причина конкретна: назва чату видно на екрані блокування й у списку чатів, а домен — у кнопці та в публічних CT-логах, незворотно після першого сертифіката. Це єдині два важелі, якими ми взагалі можемо вплинути на метадані, видимі і Telegram, і будь-кому, хто гляне на екран телефона. Репозиторій зараз називається `neuro-diary`, а `apps/web/index.html` містить «Неврологічний щоденник» — обидва потрапляють у прев'ю посилань і в перемикач задач Android, тож перевірка має бути реальною, а не декоративною.
- **`apps/bot` не отримує жодної нової команди.** Реєструється лише `CommandStart`; відповідь на `/start` лишається дослівно «Вітаємо! Відкрийте щоденник, щоб продовжити.» і не згадує нагадування. Єдиний спосіб припинити нагадування з боку Telegram — заблокувати бота; це свідомий наслідок інваріанта stateless-бота (§5.3).

## 11. Безпека

- **Згоди**: dependency `require_consent(kind)` на кожному ендпоінті з медичними даними + повторна перевірка всередині write-транзакції (§9.1); джерело істини — таблиця `consent` (partial unique index активності).
- **Rate limits**: reverse proxy per-IP (auth 10/хв); застосункові per-account (sync 60/хв, push-обсяг 5 MiB/хв, reminders-settings 20/хв, **`GET /v1/sync/key` 10/год**); вікна в PG (без Redis на старті). Ліміт на `/v1/sync/key` не косметичний: ендпоінт віддає `wrapped_dek`, сіль і `kdf_params`, тож без нього компрометація сесії миттєво перетворюється на офлайн-перебір фрази. Per-IP лічильник живе **виключно в пам'яті процесу** з вікном ≤60 с — IP не потрапляє ні в PostgreSQL, ні в логи, ні в метрики.
- **Логи білим списком** (structlog-процесор викидає все поза allowlist): `timestamp, level, event, request_id, route_template, method, status_code, duration_ms, account_ref, record_count, revision, error_code, retry_after`. `account_ref` = перші 16 hex від `HMAC(k_log, account_id)`, де `k_log` має епоху 7 днів і знищується при ротації; сирий `account_id` у лог не пишеться взагалі. Retention логів — 7 днів. Це строго сильніше за псевдонім із постійним ключем: без ротації епохи журнали за різні місяці join-яться між собою за часом активності. Заборонено назавжди: raw URL/query, initData, `telegram_user_id`, імена, payload/`record_key`, дати записів, echo вхідних значень у помилках валідації — кастомний exception handler віддає generic-повідомлення і вирізає `input` з деталей Pydantic ValidationError (інакше Pydantic повернув би/залогував медичний вміст). Клієнтське дзеркало: заборона `location.href`/initData у будь-якому web-логері.
- **CORS**: allowlist лише origin `WEBAPP_URL`; Bearer-only, без cookies → CSRF-поверхні немає.
- **Secrets**: `.env.example` без значень; `BOT_TOKEN` — лише bot і reminder_worker (незмінно); api — публічний Ed25519-ключ Telegram, несекретний `TELEGRAM_BOT_ID`, DSN своєї ролі та (лише за увімкненого флага) похідний `WEBAPP_HMAC_SECRET`; три DB-ролі (`api_rw`, `reminder_worker`, `migrator`); gitleaks у CI; запінити версії залежностей (fastapi, uvicorn, pydantic явно, import-linter ≥ 2.0).

## 12. Фази реалізації, критерії готовності, тестування

**Gate D (блокер початку Фаз 1–4).** Privacy/security/clinical review **дизайн-рівня** цього документа: криптомодель і модель втрати фрази (§7), retention/tombstone-TTL/бекапи та формулювання для користувачки (§6.4), модель згод (§4.3, §9.7), consent copy, data residency, текст нагадування і політика quiet hours (§10). Це перша половина двоетапної моделі, якою план задовольняє вимогу [review-gates.md](../review-gates.md) «перед реалізацією backend/sync/Telegram-нагадувань — нові reviews»; Фаза 5 — друга половина (перевірка реалізації на відповідність відрев'юваному дизайну).

**Порядок усередині Gate D має значення, і він не довільний.** Gate D лишається блокером
Фаз 1–4 як цілого, але його пункти закриваються в жорсткій послідовності:

```
residency (повністю) → згоди й consent copy (повністю)
   → дві дельти з нагадувань → чотири гачки з retention → один рядок §8 з криптомоделі
```

Причина саме такого порядку: consent copy заморожується у Фазі 1 разом із хешем тексту, а
текст мусить **дослівно** називати контролера й регіон зберігання — отже пункт про згоди
фізично не може бути погоджений раніше за пункт про резидентність, і зміна будь-якого з цих
полів пізніше є матеріальною зміною з повторною згодою від усіх користувачок.

| Пункт Gate D | Що з нього блокує Фазу 1 | Де основний обсяг |
|---|---|---|
| Data residency (§6.5) | усе — це передумова тексту згоди | Фаза 1 |
| Згоди і consent copy (§4.3, §9.7) | усе | Фаза 1 |
| Нагадування (§10) | лише `consent.revoke_reason` і grant із `settings` | Фаза 4 |
| Retention (§6.4) | лише `min_auth_date`, `deletion_copy_version`, «журнал пишеться до erasure», строк за `revoke_reason` | Фаза 3 |
| Криптомодель (§7) | **нічого, крім переліку step-up у §8** | Фаза 2 |

Криптомодель Фазу 1 не блокує: у Фазі 1 немає медичних ендпоінтів — це прямо в її DoD.
Блокувати на ній усе інше було б найдорожчою з можливих помилок планування.

- **Фаза 0 — Фундамент.** docker-compose (PostgreSQL 16), `.env.example` (включно з `TELEGRAM_BOT_ID`), alembic, pin залежностей, CI: ruff + mypy (strict) + pytest + `lint-imports` + gitleaks; каркас шарів §5.1.
  *DoD*: CI зелений і блокуючий; усі контракти import-linter активні; `/health` під тестом; міграція 0001 (схеми `diary`/`reminders`, ролі, GRANT-матриця §6.3).
- **Фаза 1 — Identity, Consent, Auth.** Ed25519-валідація initData (офіційні тест-вектори з `TELEGRAM_BOT_ID` тестового бота; негативні кейси: зіпсований підпис, прострочений auth_date), атомарний auth+grant (три гілки §8), anti-replay, opaque-сесії + step-up, consents-ендпоінти, auto-erasure сирітських акаунтів.
  *DoD*: unit-покриття валідатора 100% гілок; тести «прострочений auth_date → auth_stale», «повторний initData → 401», «скасування діалогу згоди → нуль рядків у БД», «конфіг api не містить BOT_TOKEN»; інтеграційні тести репозиторіїв на реальній PostgreSQL (testcontainers-python); лог-allowlist під тестом (нуль initData і нуль сирого `account_id` у капчурі логів); медичних ендпоінтів ще немає. **Додано після Gate D:** `text_sha256` показаного тексту збігається зі збереженим (снапшот-тест проти реєстру); grant `cycle_sync` без активної `health_sync` → 409 `consent_precondition`; grant `telegram_reminders` без `settings` → 422 і нуль рядків; CI-тест по OpenAPI «жоден path-шаблон не містить назви згоди»; сирітський акаунт стирається негайно при `revoke_reason='user'` і через 30 діб при `bot_blocked_timeout`.
- **Фаза 2 — Vault і Sync.** Крипто-модуль web (AAD, manifest, nonce, гігієна initData), push/pull із guards, ревізії, tombstones, компакшн, 409/410/reset, chunked upload, key CAS.
  *DoD*: property-тести на реальній PG: «два конкурентні push одного record_key з однаковим base_revision → рівно один 200 і рівно один 409», «tombstone не перезаписується мовчки», монотонність ревізій; тест компакшну: «застарілий pull → 410, застарілий push → 410, після повного ресинку видалений ключ не воскресає»; інтеграційний тест «payload під чужим record_key → клієнт відхиляє»; property-тест nonce (повторне шифрування → різні nonce/ciphertext); контрактні тести web↔api на спільних JSON-фікстурах (включно з новим валідатором `sym ∩ absent = ∅`); merge-тести cycle (delete-on-A / offline-add-on-B / re-add → збіжність за ≤2 раунди); e2e (Playwright): два браузерні профілі синхронізуються через локальний api; тест «нуль plaintext у дампі БД» (grep контрольних рядків нотаток). **Додано після Gate D:** тест приватності розширюється на `bytea` — дамп `vault_record` не містить рядка, що парситься як ISO-дата або як ім'я синглтона (це перевіряє саме усунення plaintext-заголовка, §7); manifest — обидві гілки («вилучено живий запис → падає», «вилучено compacted tombstone → проходить»); два конкурентні re-wrap → рівно один 200 і один 409 по `wrap_version`; запис у `/v1/sync/key` без step-up → 403; e2e-assert гігієни initData (location.hash і sessionStorage чисті).
- **Фаза 3 — Erasure і відкликання згод.** Outbox + erasure_worker, vault-reset, crypto-erasure, erasure-журнал + runbook, документ retention/backup.
  *DoD*: тест «revoke health_sync → нуль рядків vault_record/vault_key»; тест гонки erasure vs in-flight push (§9.8) на реальній PG; restore drill обох кейсів (бекап з in-flight job → re-run; бекап до запиту erasure → reconciliation по журналу re-erases); відрепетирувана 30-денна модель бекапів. **Додано після Gate D:** третій кейс drill — бекап, знятий **до** re-key, реконсилюється кодом `security_reset` (стара фраза не працює після відновлення); тест «erasure не стартує, якщо запис у журнал не вдався»; тест межі `at >= t_b` на круглій точці відновлення; доказ строку бекапів через `ListObjectVersions`, а не через звіт інструмента; тест «revoke `health_sync` → push із до-відкликального `base_revision` → 409 `vault_reset`».
- **Фаза 4 — Нагадування.** Схема reminders, воркер, DST/quiet hours/ідемпотентність, reconciler, ендпоінти settings.
  *DoD*: unit-тести DST Europe/Kyiv (обидва переходи, неіснуючий/подвійний час); негативний GRANT-тест (роль reminder_worker → permission denied на `diary.*`) і позитивний (повний цикл insert-before-send під реальною роллю reminder_worker; провізія/деактивація під api_rw); краш-тест ідемпотентності (обрив між send і confirm → без дубля, тест повторної доставки); тест «mock 403 → enabled=false + disabled_reason; reconciler → revoked_at + ConsentRevoked»; exact-allowlist-тести всіх outbound text/labels; mock Telegram API з 429/403; інтеграційні тести private-chat binding і opt-in/revoke. **Додано після Gate D:** `PUT` із `local_time` у `[22:00, 08:00)` → 422 `quiet_hours_violation` на всіх межах (07:59/08:00/21:59/22:00); розклад 21:30 із затримкою 40 хв → `skipped_quiet`; catch-up на 61-й хвилині → `skipped_stale`; `DELETE` на `reminder_delivery` під роллю `reminder_worker` → permission denied; 403 **не** відкликає згоду одразу, а лише після 14 діб; 400 і 5xx не змінюють ані розкладу, ані згоди; жоден ендпоінт не віддає список доставок.
- **Фаза 5 — Hardening і gates.** Rate limits, threat model (включно з localStorage-загрозами з review-gates.md і side-channel метаданих §13), перевірка реалізації на відповідність відрев'юваному дизайну — **блокер продакшену**.
  *DoD*: пройдені review-gates із підписами; schemathesis по OpenAPI; навантажувальний smoke initial upload.

**Піраміда тестів**: unit (domain, крипто, DST, валідатори) → інтеграційні з реальною PostgreSQL через testcontainers (репозиторії, конкурентність ревізій, GRANT-ізоляція, компакшн, гонки erasure) → контрактні (спільні фікстури web↔api, schemathesis) → e2e (compose: pg+api+web, Playwright; бот — смоук на aiogram-тестутілах).

**Наскрізні gate-и (успадковані, діють на кожній фазі):**
- web unit, production build і Playwright залишаються зеленими;
- API pytest і Ruff залишаються зеленими;
- bot pytest і Ruff залишаються зеленими;
- нова інфраструктура має власні інтеграційні, privacy й failure-mode тести;
- жодна майбутня функція не з'являється в UI або документації як доступна до завершення всього delivery path; чесний unavailable-state лишається в UI.

## 13. Відкриті питання і залишкові ризики (чесний перелік)

1. Argon2id (WASM; розмір/продуктивність у Telegram WebView) vs PBKDF2 — бенчмарк до Фази 2. **Рішення Gate D:** параметри, еталонні пристрої й правило прийняття (`p95 ≤ 3000 мс`) зафіксовані наперед у §7, тож лишається сам вимір, а не вибір.
2. UX втрати парольної фрази: серверна копія невідновна by design. **Закрито Gate D:** recovery-файл відхилено, штатний шлях — створення нової серверної копії з локальних даних (§7).
3. Демо-дані: залишковий ризик, якщо build-флаги зламаються; мітигація процесна (CI-перевірка бандла на відсутність `genDemo`).
4. Метадані sync (кількість записів, `payload_size`, часи активності) — side-channel; мінімізується (без дат у ключах; опційно padding-бакети розмірів), не усувається → threat model.
5. `cycle_sync` — **переформульовано після Gate D:** фільтрація на push лишається клієнтською, але відкликання виконує сервер hard-DELETE за `record_key_cycle`, який клієнт назвав при grant (§9.7). Залишковий ризик звужується до одного біта: сервер знає, який `record_key` є циклом, у акаунтів, що надали цю згоду. Опція на майбутнє — окремий крипто-домен/DEK.
6. Ненадійні годинники пристроїв → LWW може обрати «неправильного» переможця (client_ts автентифікований, але не «правильний»); прийнятний компроміс для 1–3 пристроїв, зафіксовано.
7. GCM-replay старої версії того самого ключа під новою revision — AAD не закриває (revision не в AAD). **Переформульовано:** після додавання `sha256(payload)` до manifest (§7) сам replay стає детектованим; залишок звужується до узгодженого відкату для пристрою, який **не має локального орієнтира** (`revision`, `vault_seq`) — саме тому §7 вимагає явного підтвердження свіжості перед першим злиттям.
8. Fork/split-view: сервер може показувати різним пристроям різні гілки в межах вікна між оновленнями manifest; без кросс-пристроєвого каналу не усувається; документується.
9. Ротація фрази (re-wrap) не відкликає доступ того, хто знав стару фразу; мітигація — документація + флоу re-key (§7) + ревокація сесій + крок runbook `security_reset` (§6.4), який закриває раніше не помічений випадок: restore з копії, знятої до re-key, повертає стару фразу в дію.
10. Хостинг і юрисдикція (data residency) — **закрито Gate D, §6.5.** Це виявилося не «поза кодом»: від нього залежить текст згоди, який заморожується у Фазі 1.
11. Покриття поля `signature` (Ed25519) у старих Telegram-клієнтах — моніторинг 401-рейтів; fallback за флагом (§8).
12. Набір активних згод сам по собі — слабкий health-інференс. **Рішення Gate D:** перейменування `kind` у нейтральні ідентифікатори (`domain_b`) **відхилено як security theater** — воно нічого не приховує від того, хто має доступ до БД, зате коштує міграції `CHECK`-обмеження і робить логи нечитними. Справжні мітигації інші й вони застосовані: назва згоди не потрапляє в URL, в `error_code` і в `outbox.payload` (§9.2, §11).
13. `apps/api/app/schemas.py`: додати валідатор `sym ∩ absent = ∅` (Фаза 2, контрактний рівень).
14. **Вік користувачок.** Продукт про неврологічні симптоми з циклом закономірно приваблює підлітків, а правова база — виключно згода: явна згода за Art. 9(2)(a) від неповнолітньої недійсна без представника. Рішенням власника віковий поріг **не вводиться**; ризик прийнято свідомо і винесено на юридичну перевірку. Local-only режим це не зачіпає — там немає ані згоди, ані передавання даних.
15. **Контролер — фізична особа, репозиторій публічний.** Ім'я й контакт контролера потрапляють у текст згоди, а отже в публічний git. Це усвідомлений наслідок вибору. Операційна частина резидентності (бакети, хости, кустодія ключів) у git не потрапляє (§6.5).
16. **`k_log` і ключ шифрування бекапів — на тому самому вузлі, що й `BOT_TOKEN`.** Компрометація єдиного вузла дає довірений канал у приватний чат кожної користувачки, тобто шлях до фішингу парольної фрази. §5.3 свідомо тримає `BOT_TOKEN` подалі від api, але однохостове розгортання це послаблення повертає. Фіксується у threat model Фази 5 як прийнятий на старті ризик.
17. **CSP vs Telegram SDK і WASM.** `script-src 'self'` несумісний із обов'язковим `telegram-web-app.js`, а Argon2id/WASM у Chromium потребує `'wasm-unsafe-eval'`. Як наслідок, надто строгий CSP тихо зіштовхнув би всіх на PBKDF2-fallback. Точна політика — Фаза 2, разом із бенчмарком KDF.
18. **Час життя сховища у Telegram WebView.** Посилка «фраза вводиться раз на пристрій», на якій побудований поріг KDF, не перевірена: ITP і очищення кешу Telegram можуть робити втрату підключів рутинною. Вимір — Фаза 2; якщо посилка хибна, поріг `p95` треба переглядати.

## Рішення ревізії

**Переписано з нуля** (попередня версія — дослівно в [archive/backend-bot-plan-v1.md](archive/backend-bot-plan-v1.md)). Критерій: blocker-розбіжностей між незалежним дизайном і v1 — нуль (суперечностей із privacy-контрактом чи межами v1 не виявлено), але структура v1 (продуктовий контракт на ~50 рядків: стан / межі / 5 кроків / gate) не здатна вмістити обов'язковий технічний обсяг (DDD-розбивка, import-linter-контракти, DDL, замкнений sync-протокол, криптомодель, семантика нагадувань, фази з DoD) без повної переорганізації.

Ключові відмінності від v1:
- **Збережено повністю**: всі межі v1 (розділ 2 — дослівно за змістом), статусна плашка, наскрізні gate-и, посилання на [future-telegram-reminders.md](future-telegram-reminders.md) як невід'ємну специфікацію доставки (жодне її положення не змінене: exact allowlist, ресурс `ReminderSettings`, незалежний opt-in, stateless доставка, snooze поза v1, quiet hours — після review, політика лише в API).
- **5-крокова «мінімальна послідовність» v1** розгорнута у Фази 0–5 з критеріями готовності та рівнями тестування; вимога v1 «починати лише після погодження scope/data residency/retention/threat model/consent copy» конкретизована як Gate D (дизайн-рівневе review перед Фазами 1–4) + Фаза 5 (review реалізації) — двоетапна модель, що задовольняє review-gates.md.
- **Додано те, чого v1 не містив**: доменна модель (bounded contexts, агрегати, інваріанти, події, ACL); шарування з механічним закріпленням import-linter; схема PostgreSQL із GRANT-ізоляцією каналу нагадувань; client-side E2E-криптомодель (AAD, manifest, envelope, re-wrap/re-key) із чесною моделлю видалення й бекапів (erasure-журнал, PITR-runbook); технічно замкнений sync-протокол (серіалізовані ревізії, tombstones + горизонт компакшну, reset-guard, детермінований merge, пост-410 правило, доля демо-даних); механізм автентифікації Mini App (Ed25519 без BOT_TOKEN в api, атомарний auth+grant, anti-replay, step-up); точна семантика доставки (DST, catch-up, at-most-once insert-before-send, 403-reconciler); білий список логів; явний перелік залишкових ризиків.
- **Єдина змістовна корекція відносно мого початкового незалежного дизайну на користь позиції v1/специфікації**: діапазон quiet hours не фіксується планом (лишається продуктовим/клінічним рішенням Gate D; у дизайні пропонувався фіксований 22:00–08:00).
- Дизайн пройшов адверсарну перевірку (4 незалежні лінзи: privacy/security, Telegram-специфіка з верифікацією за офіційною документацією, замкненість sync-протоколу, архітектурна цілісність; кожна знахідка — окремим суддею-скептиком): 30 підтверджених знахідок інтегровано до цього тексту (серед найважливіших: AAD-прив'язка ciphertext до логічного ключа, anti-rollback manifest, серіалізація push/erasure, push-side guards компакшну і reset, синхронізований журнал видалень циклу, атомарний auth+grant, reconciler 403-флоу, двоетапна модель gates).
