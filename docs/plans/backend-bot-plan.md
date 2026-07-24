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
- Health-sync consent і Telegram-reminder consent — незалежні; згода на дані циклу (`cycle_sync`) — третя незалежна згода.
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
- `DiaryAccount` (root): `id`, `status ∈ {active, erasing, erased}`. Інваріанти: акаунт існує лише разом із ≥1 згодою (атомарне створення з першою згодою, §8); акаунт без жодної активної згоди (edge-case збою) — auto-erasure за TTL 24 год; `erased` — термінальний стан.
- `Consent`: `kind ∈ {health_sync, telegram_reminders, cycle_sync}`, `granted_at`, `revoked_at`, `text_version`. Інваріанти: згоди незалежні; ≤1 активна per (account, kind); legacy-поля ніколи не мапляться у згоду. Запис згоди зберігає щонайменше версію тексту, час надання та час відкликання.
- `TelegramIdentity`: з initData персистується лише `telegram_user_id`.

**Sync/Vault**
- `DiaryVault` (root, 1:1 з account): `current_revision`, `compacted_up_to`, `reset_revision`.
- `VaultRecord`: `record_key` (HMAC-похідний на клієнті), `payload` (AES-GCM ciphertext), `revision`, `deleted`, `client_ts` (індексна копія; авторитетний `client_ts` — усередині шифрованого payload).
- Інваріанти: ревізія строго монотонна і видима у порядку зростання; сервер ніколи не парсить payload; tombstone перемагає застарілий update; push гейтиться `reset_revision` і `compacted_up_to`; історія версій не зберігається (last-write only — свідомо, заради чесного видалення).

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
- `ConsentGranted(account_id, 'telegram_reminders')` → провізія reminders-рядка **upsert-ом** роллю `api_rw`: `INSERT … ON CONFLICT (account_id) DO UPDATE SET telegram_chat_id=…, enabled=true, disabled_reason=NULL, …`.
- `ConsentRevoked(account_id, kind)` → `health_sync`: серіалізована erasure сейфа (§9.8); `telegram_reminders`: `reminder_schedule.enabled=false` **у тій самій транзакції**, що і `revoked_at` (роль `api_rw` має права на обидві схеми) — відкликання атомарно вимикає майбутні доставки.
- `AccountErasureRequested(account_id)` → повний purge + запис у erasure-журнал (§6.4).
- **Зворотний міст reminders→identity**: воркер не пише в схему `diary`. При 403 він ставить `enabled=false, disabled_reason='bot_blocked'` у своїй схемі; періодичний reconciler-крок у `outbox_dispatcher` (роль `api_rw`) переносить це у `consent.revoked_at` + подію `ConsentRevoked`, після чого очищає `disabled_reason`. Отже міст grant-напрямку — події; зворотний — reconciler.

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
                    granted_at timestamptz NOT NULL, revoked_at timestamptz, text_version text NOT NULL);
  -- UNIQUE INDEX ux_consent_active ON consent(account_id, kind) WHERE revoked_at IS NULL;
session_token      (id uuid PK, account_id uuid NOT NULL REFERENCES account, token_hash bytea NOT NULL,
                    created_at, expires_at, last_used_at, revoked_at);  -- INDEX (token_hash)
auth_replay        (initdata_hash bytea PK, seen_at timestamptz);       -- одноразовість initData; TTL-чистка
vault_key          (account_id uuid PK, wrapped_dek bytea, kdf text, kdf_params jsonb, key_version int NOT NULL DEFAULT 1);
vault_revision     (account_id uuid PK, current_revision bigint NOT NULL DEFAULT 0,
                    compacted_up_to bigint NOT NULL DEFAULT 0,
                    reset_revision bigint NOT NULL DEFAULT 0);          -- живуть увесь життєвий цикл акаунта, не компактяться
vault_record       (account_id uuid, record_key bytea, payload bytea, payload_size int,
                    revision bigint NOT NULL, deleted boolean NOT NULL DEFAULT false,
                    client_ts timestamptz, updated_at timestamptz,
                    PRIMARY KEY (account_id, record_key));
  -- UNIQUE INDEX ux_vault_rev ON vault_record(account_id, revision);   -- курсор pull
erasure_job        (id uuid PK, account_id uuid, scope text, requested_at, completed_at);
outbox             (id bigserial PK, event_type text, payload jsonb /* без медичних даних */, created_at, processed_at);
```

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
```

### 6.3 Ролі та GRANT-матриця (DDL — лише `migrator`)

- `api_rw`: повні DML на схему `diary`; на `reminders`: SELECT/INSERT/UPDATE/DELETE `reminder_schedule` (провізія, деактивація, reconciler), SELECT/DELETE `reminder_delivery` (erasure-purge).
- `reminder_worker`: SELECT/UPDATE `reminders.reminder_schedule`; SELECT/INSERT/UPDATE `reminders.reminder_delivery`; **нуль привілеїв на `diary`** (permission denied — під тестом, позитивним і негативним).

### 6.4 Чесна модель видалення

- Видалення = hard DELETE. Для sync рядок живе як `deleted=true` (payload обнуляється негайно) протягом tombstone-TTL **180 днів**, потім фізично видаляється компактором. Компактор в одній транзакції з DELETE просуває `compacted_up_to = GREATEST(compacted_up_to, max(revision видалених))`.
- Історія версій не зберігається (last-write only); жодного event-sourcing медичного вмісту.
- Бекапи: WAL + base backups, retention **30 днів**. Обіцянка користувачці формулюється рівно так: «активні системи — негайно; відновлювані копії зникають щонайбільше через 30 днів; у копіях — лише шифротекст».
- **Erasure-журнал поза скоупом DB-restore** (append-only файл/object storage; лише `{account_id, erased_at}` — server-side UUID + timestamp, нуль медичного вмісту; retention ≥ 60 днів). Пишеться синхронно при завершенні erasure. **Обов'язковий runbook-крок після будь-якого restore**: повторна erasure кожного акаунта, чиє `erased_at` пізніше за точку відновлення. Це закриває PITR-відновлення з бекапа, знятого до запиту erasure; re-run відновлених `erasure_job` покриває лише in-flight кейс. Restore drill (§12, Фаза 3) перевіряє обидва.
- Crypto-erasure як підсилення: при відкликанні `health_sync` видаляється і `wrapped_dek` (додаток до TTL-обіцянки, не заміна — старі бекапи містять старий конверт).

## 7. Шифрування: client-side E2E

- **Рівень: app-level на клієнті (E2E).** WebCrypto **AES-256-GCM** per record. Чому не pgcrypto і не лише диск: ключ у сервера означав би, що «сервер не аналізує вміст» тримається на чесному слові; E2E робить це криптографічним фактом. Disk encryption — додатковий шар хостингу.
- **Nonce:** 12 байт свіжої випадковості `crypto.getRandomValues` на кожну операцію шифрування; заборонено виводити nonce з `record_key`/revision/лічильника/часу. Ротація DEK через nonce-бюджет не потрібна (бюджет NIST 2^32 операцій недосяжний для щоденника однієї користувачки — зафіксовано свідомо).
- **AAD = канонічний логічний шлях ("entry:2026-07-23", "cycle", …) ‖ client_ts ‖ deleted.** Шлях додатково лежить у автентифікованому plaintext-заголовку запису; на pull клієнт перевіряє `HMAC(k_index, шлях) == record_key`, під яким сервер віддав запис; розбіжність → запис відхиляється, не мерджиться, нейтральна помилка цілісності. Це блокує підміну/перестановку записів сервером і замикає мапування record_key → логічна одиниця на pull. Server-assigned `revision` в AAD не входить (невідома при шифруванні).
- **Anti-rollback: шифрований синглтон `manifest`** (під тим самим DEK), оновлюється в тому ж push: клієнтський монотонний `vault_seq` + HMAC(`k_auth`) над відсортованою множиною `(record_key, client_ts, deleted)`. Повний pull — включно з онбордингом нового пристрою — верифікується проти manifest; кожен пристрій персистить highest-seen `(revision, vault_seq)` і відхиляє відповіді з нижчими значеннями (сумісно з vault-reset: ревізія там монотонна). Провал перевірки → нейтральна помилка «серверна копія виглядає застарілою», локальні дані первинні.
- **Ключі:** sync-парольна фраза → KEK = Argon2id (WASM; fallback PBKDF2-SHA256 ≥600k ітерацій — рішення після бенчмарку в Telegram WebView) → KEK обгортає випадковий DEK (envelope). `wrapped_dek + kdf_params + key_version` — на сервері (`vault_key`): зміна фрази без перешифрування, онбординг нового пристрою.
- **Дві різні операції з фразою:** «зміна фрази» = re-wrap DEK (зручність; не захищає дані від скомпрометованої старої фрази у поєднанні зі старим wrapped_dek із бекапів ≤30 днів — чесно документується в UX-тексті); «re-key при компрометації» = клієнт генерує новий DEK, локально перешифровує все і атомарно виконує vault-reset (§9.5) + chunked upload + новий конверт із `key_version+1`; інші пристрої детектують re-key за `key_version` → повний ресинк.
- Втрата фрази = втрата серверної копії, **не даних** (локальна копія первинна). Чесно документується; текст — предмет Gate D.

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
- **Step-up для деструктивних операцій:** `vault-reset`, відкликання `health_sync` (з erasure) і повний account erasure вимагають «свіжої» сесії (валідація initData ≤10 хв тому); інакше 403 з кодом, що веде на повторне відкриття Mini App (природний step-up, нуль нової криптографії).
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
POST /v1/sync/push   {base_revision, changes:[{record_key, payload_b64 | tombstone:true, client_ts}]}
                     → 200 {new_revision} | 409 {reason:"conflict", conflict_keys} | 409 {reason:"vault_reset"}
                     | 410 (за горизонтом компакшну) | 413 | 429
GET  /v1/sync/pull   ?since=<rev>&limit=500
                     → 200 {records:[…], next_since, current_revision, reset: bool}
                     | 410 Gone (since < compacted_up_to → повний ресинк; reset-стан повертається першим)
GET  /v1/sync/key    → {wrapped_dek, kdf, kdf_params, key_version}
POST /v1/sync/key    {mode:"rewrap"|"rekey", expected_key_version, wrapped_dek, kdf, kdf_params}
                     → 200 | 409 {current_key_version}   (CAS по key_version)
POST /v1/account/vault-reset    (step-up; атомарно: DELETE всіх vault_record + reset_revision = нова current_revision)
```
Усі — за dependency `require_consent("health_sync")` + повторною перевіркою у транзакції (§9.1).

### 9.3 Конфлікти: pull-merge-push (optimistic concurrency)

Сервер не вміє merge (ciphertext) → 409 → клієнт робить pull, merge локально в plaintext, повторний push. Правила merge (детерміновані на всіх пристроях):
- per-record LWW за **автентифікованим** `client_ts` (зсередини payload; серверна колонка — лише індексна копія); при рівних ts — стабільний tiebreaker: per-device випадковий `device_id` усередині шифрованого payload, лексикографічно. Пріоритетніші правила: `DraftEntry` ніколи не перемагає `DoneEntry` тієї ж дати; tombstone ≥ update.
- Синглтони `cycle`, `catalog`, `groups` — **поелементний merge** (2P/OR-set): множина елементів з `added_at`/`updated_at` + журнал видалених/архівованих `{id|date, removed_at}` **у складі шифрованого payload** (синхронізується як ciphertext; сервер нічого не бачить). Merge per елемент: перемагає операція з максимальним timestamp; tie → remove. Для `cycle`: `{date, added_at}` + журнал `{date, removed_at}`; повторне додавання (MENSES_CONFIRM після видалення) пише новий `added_at`. Retention журналів: записи, старші за 180 днів, обрізаються при серіалізації (безпечно: довший офлайн → 410 → повний ресинк).
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
- активне вікно: tombstone тримає монотонну revision 180 днів; push застарілого пристрою → 409 → pull → tombstone застосовується;
- push-гейт: `base_revision < compacted_up_to` → 410 — застарілий пристрій не може пушити, доки не зробить повний ресинк (закриває push-без-pull після компакшну);
- пост-410 merge (правило авторитетності присутності): локальний запис, який був **clean** (має `last_acked_revision`) і відсутній у повному серверному наборі → трактується як видалений віддалено → видаляється локально **з підтвердженням на пристрої**; **dirty** та ніколи-не-синхронізовані записи мерджаться і пушаться; крайовий кейс «acked, локально відредагований, на сервері відсутній» → явне підтвердження користувачки. Локальний кеш tombstone-ів не є альтернативою: пристрій, що не бачив видалення, tombstone не має;
- vault-reset: push-guard `base_revision < reset_revision` → 409 `vault_reset` до per-key перевірки; клієнт зобов'язаний зробити pull, показати підтвердження очищення на пристрої і лише після рішення користувачки пушити з актуальним base_revision. `reset_revision` не компактиться; pull зі `since < reset_revision` містить `reset:true`; 410-ресинк повертає reset-стан першим;
- захист від масового видалення вкраденою сесією: якщо один pull-батч приносить tombstones для >20% і ≥10 локальних записів → трактується як reset-еквівалент → те саме підтвердження на пристрої (правило клієнтське; сервер вміст не аналізує).

### 9.5 Initial upload / re-key / backpressure

Перший push після згоди: повний снапшот чанками ≤200 records / ≤1 MiB, `base_revision=0`, послідовно; обрив → retry чанка (ідемпотентно по record_key+вміст; часткова ревізія, видима іншому пристрою, — коректний стан optimistic-протоколу). Ліміт payload per record 64 KiB → 413; per-account rate limit → 429 + `Retry-After`; експоненційний backoff. Re-key (§7) = vault-reset + повторний upload під новим DEK в одному клієнтському флоу.

### 9.6 Демо-дані при першому синку

Серверна евристика неможлива і заборонена (аналіз вмісту порушував би §2; дані структурно нерозрізнювані). У production `load()` → `emptyData()`, `genDemo` викликається лише з тестів/e2e → ризик існує тільки в тест-збірках. Рішення: (а) sync-модуль вимкнений build-флагом у test/e2e-збірках, окрім спеціальної sync-e2e збірки, що ходить лише на локальний тестовий api; (б) CI-перевірка, що prod-бандл не містить `genDemo`. Залишковий ризик документується (§13).

### 9.7 Згода `cycle_sync` при E2E

Сервер не бачить, який запис є `cycle` (ключі непрозорі) → фільтрація за згодою — клієнтська: без активної `cycle_sync` клієнт не включає `cycle` у push; при відкликанні шле tombstone `cycle`. Чесно документується (включно з user-facing текстом згоди): серверний enforcement технічно неможливий за E2E; гарантія — клієнтський код + контрактні тести; якщо пристрій, що мав дані циклу, більше не вийде онлайн, ciphertext `cycle` лишається до відкликання `health_sync`/erasure. Опція на майбутнє — окремий крипто-домен/DEK для cycle (серверно стираний за revoke) — §13.

### 9.8 Серіалізація erasure з in-flight push

Erasure-транзакція воркера (DELETE `vault_record` + `vault_key`) починається з того самого лока рядка `account` (`FOR NO KEY UPDATE`), що і push (§9.1, крок 1) — бар'єр: будь-який push або закомітився до бар'єра (його рядки видаляє свіжий снапшот DELETE), або стартує після і падає на in-txn перевірці згоди. `status='erasing'` — лише для повного `AccountErasureRequested`, не для часткового revoke `health_sync`. Тест гонки — у DoD Фази 3.

## 10. Нагадування (Telegram-доставка)

Повний продуктовий контракт живе лише в [future-telegram-reminders.md](future-telegram-reminders.md) і є невід'ємною частиною цього плану. Зокрема: fresh explicit opt-in, незалежний від health-sync; окремий ресурс `ReminderSettings` з `enabled`, валідованим `HH:mm` та IANA timezone, який не входить до `AppData` і не приймається/не повертається diary/export-схемами; API — єдине джерело правди для згоди, розкладу, timezone, due-рішення та dedupe; stateless доставка і private chats only; статичний exact allowlist; жодного browser/system push; snooze поза першою версією; quiet hours визначаються один раз після review і не дублюються між шарами.

Технічна реалізація контракту:

- **Мапінг ресурсу**: `ReminderSettings {enabled, time, timezone}` ↔ рядок `reminders.reminder_schedule` (+ службові `next_fire_at`, `disabled_reason`, `telegram_chat_id`). Ендпоінти `GET/PUT /v1/reminders/settings` за `require_consent("telegram_reminders")`; PUT повертає канонічний ресурс.
- **Мапінг «stateless bot» із контракту**: «мінімальна delivery-команда» = заклеймлений occurrence `(account_id → telegram_chat_id, local_date)` з reminders-схеми; виконавець — `reminder_worker` (процес api-кодобази з BOT_TOKEN), який надсилає allowlisted повідомлення і підтверджує результат статусом у `reminder_delivery`. Воркер не читає записи щоденника (GRANT + import-linter, §5.3); `apps/bot` як процес не розширюється і стану не має. Властивості контракту (bot не має стану, не читає щоденник, API — єдине джерело due/dedupe) виконані буквально.
- **Розклад**: `tz` — IANA (валідація `zoneinfo.ZoneInfo`), `local_time` — wall-clock; `next_fire_at` (UTC) передобчислюється при кожній зміні розкладу і після кожного спрацювання.
- **DST** (`zoneinfo`): неіснуючий локальний час (spring forward) → перший валідний інстант після gap; подвійний (fall back) → перше входження (`fold=0`). Юніт-тести обох переходів Europe/Kyiv.
- **Quiet hours**: діапазон не фіксується цим планом — він визначається продуктовим/клінічним рішенням у Gate D (кандидат для обговорення: 22:00–08:00). Механізм: після погодження політика живе лише в API — заборона на рівні валідації `local_time` у PUT settings; рантайм-кейс (зміна політики при наявному розкладі) → статус `skipped_quiet`. Bot і web політику не дублюють.
- **Простій воркера**: catch-up лише якщо `now − next_fire_at ≤ 4 год` І та сама локальна доба І не в quiet hours → надіслати; інакше `skipped_stale` + перерахунок на наступну добу. Жодних наздоганянь за минулі дні.
- **Ідемпотентність (atomic claim/ack/dedupe)**: **insert-before-send** — `INSERT reminder_delivery(status='pending')` (PK `(account_id, local_date)` атомарно клеймить добу) → `sendMessage` → `UPDATE status='sent'`. Збій між send і confirm → рядок лишився `pending`; політика **at-most-once**: pending старше 15 хв → `failed` без повторної відправки цієї доби (краще одне пропущене нейтральне нагадування, ніж дубль). Окремий outbox не потрібен: PK і є ідемпотентним ключем occurrence.
- **Rate limits Telegram**: пейсинг ~25 msg/s токен-бакетом; honor `retry_after` при 429 (у межах вікна валідності тієї ж доби; send ще не відбувся — з at-most-once не конфліктує); **403 (бот заблоковано)** → `enabled=false, disabled_reason='bot_blocked'` у своїй схемі; reconciler (§4.4) переносить це у `consent.revoked_at` + подію `ConsentRevoked`.
- **Зміст**: точний allowlist зі специфікації — текст `Час зробити короткий запис`, кнопка `Відкрити щоденник`, URL — лише конфігурований `WEBAPP_URL` без query-параметрів. Тест-allowlist (як у наявному боті): заборона інтерполяції, `callback_data`, будь-яких сигналів стану щоденника (навіть «ви ще не заповнили» — заборонений медичний сигнал; нагадування «сліпе»).

## 11. Безпека

- **Згоди**: dependency `require_consent(kind)` на кожному ендпоінті з медичними даними + повторна перевірка всередині write-транзакції (§9.1); джерело істини — таблиця `consent` (partial unique index активності).
- **Rate limits**: reverse proxy per-IP (auth 10/хв); застосункові per-account (sync 60/хв, push-обсяг 5 MiB/хв); вікна в PG (без Redis на старті).
- **Логи білим списком** (structlog-процесор викидає все поза allowlist): `timestamp, level, event, request_id, route_template, method, status_code, duration_ms, account_id, record_count, revision, error_code, retry_after`. Заборонено назавжди: raw URL/query, initData, `telegram_user_id`, імена, payload/`record_key`, дати записів, echo вхідних значень у помилках валідації — кастомний exception handler віддає generic-повідомлення і вирізає `input` з деталей Pydantic ValidationError (інакше Pydantic повернув би/залогував медичний вміст). Клієнтське дзеркало: заборона `location.href`/initData у будь-якому web-логері.
- **CORS**: allowlist лише origin `WEBAPP_URL`; Bearer-only, без cookies → CSRF-поверхні немає.
- **Secrets**: `.env.example` без значень; `BOT_TOKEN` — лише bot і reminder_worker (незмінно); api — публічний Ed25519-ключ Telegram, несекретний `TELEGRAM_BOT_ID`, DSN своєї ролі та (лише за увімкненого флага) похідний `WEBAPP_HMAC_SECRET`; три DB-ролі (`api_rw`, `reminder_worker`, `migrator`); gitleaks у CI; запінити версії залежностей (fastapi, uvicorn, pydantic явно, import-linter ≥ 2.0).

## 12. Фази реалізації, критерії готовності, тестування

**Gate D (блокер початку Фаз 1–4).** Privacy/security/clinical review **дизайн-рівня** цього документа: криптомодель і модель втрати фрази (§7), retention/tombstone-TTL/бекапи та формулювання для користувачки (§6.4), модель згод (§4.3, §9.7), consent copy, data residency, текст нагадування і політика quiet hours (§10). Це перша половина двоетапної моделі, якою план задовольняє вимогу [review-gates.md](../review-gates.md) «перед реалізацією backend/sync/Telegram-нагадувань — нові reviews»; Фаза 5 — друга половина (перевірка реалізації на відповідність відрев'юваному дизайну).

- **Фаза 0 — Фундамент.** docker-compose (PostgreSQL 16), `.env.example` (включно з `TELEGRAM_BOT_ID`), alembic, pin залежностей, CI: ruff + mypy (strict) + pytest + `lint-imports` + gitleaks; каркас шарів §5.1.
  *DoD*: CI зелений і блокуючий; усі контракти import-linter активні; `/health` під тестом; міграція 0001 (схеми `diary`/`reminders`, ролі, GRANT-матриця §6.3).
- **Фаза 1 — Identity, Consent, Auth.** Ed25519-валідація initData (офіційні тест-вектори з `TELEGRAM_BOT_ID` тестового бота; негативні кейси: зіпсований підпис, прострочений auth_date), атомарний auth+grant (три гілки §8), anti-replay, opaque-сесії + step-up, consents-ендпоінти, auto-erasure сирітських акаунтів.
  *DoD*: unit-покриття валідатора 100% гілок; тести «прострочений auth_date → auth_stale», «повторний initData → 401», «скасування діалогу згоди → нуль рядків у БД», «конфіг api не містить BOT_TOKEN»; інтеграційні тести репозиторіїв на реальній PostgreSQL (testcontainers-python); лог-allowlist під тестом (нуль initData у капчурі логів); медичних ендпоінтів ще немає.
- **Фаза 2 — Vault і Sync.** Крипто-модуль web (AAD, manifest, nonce, гігієна initData), push/pull із guards, ревізії, tombstones, компакшн, 409/410/reset, chunked upload, key CAS.
  *DoD*: property-тести на реальній PG: «два конкурентні push одного record_key з однаковим base_revision → рівно один 200 і рівно один 409», «tombstone не перезаписується мовчки», монотонність ревізій; тест компакшну: «застарілий pull → 410, застарілий push → 410, після повного ресинку видалений ключ не воскресає»; інтеграційний тест «payload під чужим record_key → клієнт відхиляє»; property-тест nonce (повторне шифрування → різні nonce/ciphertext); контрактні тести web↔api на спільних JSON-фікстурах (включно з новим валідатором `sym ∩ absent = ∅`); merge-тести cycle (delete-on-A / offline-add-on-B / re-add → збіжність за ≤2 раунди); e2e (Playwright): два браузерні профілі синхронізуються через локальний api; тест «нуль plaintext у дампі БД» (grep контрольних рядків нотаток); e2e-assert гігієни initData (location.hash і sessionStorage чисті).
- **Фаза 3 — Erasure і відкликання згод.** Outbox + erasure_worker, vault-reset, crypto-erasure, erasure-журнал + runbook, документ retention/backup.
  *DoD*: тест «revoke health_sync → нуль рядків vault_record/vault_key»; тест гонки erasure vs in-flight push (§9.8) на реальній PG; restore drill обох кейсів (бекап з in-flight job → re-run; бекап до запиту erasure → reconciliation по журналу re-erases); відрепетирувана 30-денна модель бекапів.
- **Фаза 4 — Нагадування.** Схема reminders, воркер, DST/quiet hours/ідемпотентність, reconciler, ендпоінти settings.
  *DoD*: unit-тести DST Europe/Kyiv (обидва переходи, неіснуючий/подвійний час); негативний GRANT-тест (роль reminder_worker → permission denied на `diary.*`) і позитивний (повний цикл insert-before-send під реальною роллю reminder_worker; провізія/деактивація під api_rw); краш-тест ідемпотентності (обрив між send і confirm → без дубля, тест повторної доставки); тест «mock 403 → enabled=false + disabled_reason; reconciler → revoked_at + ConsentRevoked»; exact-allowlist-тести всіх outbound text/labels; mock Telegram API з 429/403; інтеграційні тести private-chat binding і opt-in/revoke.
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

1. Argon2id (WASM; розмір/продуктивність у Telegram WebView) vs PBKDF2-600k — бенчмарк до Фази 2.
2. UX втрати парольної фрази: серверна копія невідновна by design; recovery-файл локального експорту; формулювання — предмет Gate D.
3. Демо-дані: залишковий ризик, якщо build-флаги зламаються; мітигація процесна (CI-перевірка бандла на відсутність `genDemo`).
4. Метадані sync (кількість записів, `payload_size`, часи активності) — side-channel; мінімізується (без дат у ключах; опційно padding-бакети розмірів), не усувається → threat model.
5. `cycle_sync` — enforcement клієнтський (E2E не дозволяє серверний); опція на майбутнє: окремий крипто-домен/DEK для cycle, серверно стираний за revoke; поки що — чесний user-facing текст (§9.7).
6. Ненадійні годинники пристроїв → LWW може обрати «неправильного» переможця (client_ts автентифікований, але не «правильний»); прийнятний компроміс для 1–3 пристроїв, зафіксовано.
7. GCM-replay старої версії того самого ключа під новою revision сервером — AAD не закриває (revision не в AAD); частково закриває manifest/`vault_seq`; залишок документується.
8. Fork/split-view: сервер може показувати різним пристроям різні гілки в межах вікна між оновленнями manifest; без кросс-пристроєвого каналу не усувається; документується.
9. Ротація фрази (re-wrap) не відкликає доступ того, хто знав стару фразу; мітигація — документація + флоу re-key (§7) + ревокація сесій.
10. Хостинг і юрисдикція бекапів (data residency) — поза кодом; передумова Gate D.
11. Покриття поля `signature` (Ed25519) у старих Telegram-клієнтах — моніторинг 401-рейтів; fallback за флагом (§8).
12. Набір активних згод сам по собі — слабкий health-інференс (наявність `cycle_sync`); мінімізується неминуче назвами згод; фіксується у threat model.
13. `apps/api/app/schemas.py`: додати валідатор `sym ∩ absent = ∅` (Фаза 2, контрактний рівень).

## Рішення ревізії

**Переписано з нуля** (попередня версія — дослівно в [archive/backend-bot-plan-v1.md](archive/backend-bot-plan-v1.md)). Критерій: blocker-розбіжностей між незалежним дизайном і v1 — нуль (суперечностей із privacy-контрактом чи межами v1 не виявлено), але структура v1 (продуктовий контракт на ~50 рядків: стан / межі / 5 кроків / gate) не здатна вмістити обов'язковий технічний обсяг (DDD-розбивка, import-linter-контракти, DDL, замкнений sync-протокол, криптомодель, семантика нагадувань, фази з DoD) без повної переорганізації.

Ключові відмінності від v1:
- **Збережено повністю**: всі межі v1 (розділ 2 — дослівно за змістом), статусна плашка, наскрізні gate-и, посилання на [future-telegram-reminders.md](future-telegram-reminders.md) як невід'ємну специфікацію доставки (жодне її положення не змінене: exact allowlist, ресурс `ReminderSettings`, незалежний opt-in, stateless доставка, snooze поза v1, quiet hours — після review, політика лише в API).
- **5-крокова «мінімальна послідовність» v1** розгорнута у Фази 0–5 з критеріями готовності та рівнями тестування; вимога v1 «починати лише після погодження scope/data residency/retention/threat model/consent copy» конкретизована як Gate D (дизайн-рівневе review перед Фазами 1–4) + Фаза 5 (review реалізації) — двоетапна модель, що задовольняє review-gates.md.
- **Додано те, чого v1 не містив**: доменна модель (bounded contexts, агрегати, інваріанти, події, ACL); шарування з механічним закріпленням import-linter; схема PostgreSQL із GRANT-ізоляцією каналу нагадувань; client-side E2E-криптомодель (AAD, manifest, envelope, re-wrap/re-key) із чесною моделлю видалення й бекапів (erasure-журнал, PITR-runbook); технічно замкнений sync-протокол (серіалізовані ревізії, tombstones + горизонт компакшну, reset-guard, детермінований merge, пост-410 правило, доля демо-даних); механізм автентифікації Mini App (Ed25519 без BOT_TOKEN в api, атомарний auth+grant, anti-replay, step-up); точна семантика доставки (DST, catch-up, at-most-once insert-before-send, 403-reconciler); білий список логів; явний перелік залишкових ризиків.
- **Єдина змістовна корекція відносно мого початкового незалежного дизайну на користь позиції v1/специфікації**: діапазон quiet hours не фіксується планом (лишається продуктовим/клінічним рішенням Gate D; у дизайні пропонувався фіксований 22:00–08:00).
- Дизайн пройшов адверсарну перевірку (4 незалежні лінзи: privacy/security, Telegram-специфіка з верифікацією за офіційною документацією, замкненість sync-протоколу, архітектурна цілісність; кожна знахідка — окремим суддею-скептиком): 30 підтверджених знахідок інтегровано до цього тексту (серед найважливіших: AAD-прив'язка ciphertext до логічного ключа, anti-rollback manifest, серіалізація push/erasure, push-side guards компакшну і reset, синхронізований журнал видалень циклу, атомарний auth+grant, reconciler 403-флоу, двоетапна модель gates).
