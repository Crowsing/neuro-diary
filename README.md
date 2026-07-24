# Неврологічний щоденник

Щоденник самоспостереження за неврологічними симптомами й контекстом, власною динамікою та даними для розмови з лікарем. Це **не** діагностичний сервіс — застосунок не встановлює діагноз, не визначає причину симптому, не підтверджує загострення і не призначений для невідкладної допомоги.

Реалізація дизайну «Неврологічний щоденник v2» (Claude Design, дизайн-система Organic).

## Структура monorepo

| Директорія | Що це |
|---|---|
| `apps/web` | React SPA (Vite + TypeScript). Дані щоденника зберігаються лише в `localStorage`; backend і фонова доставка відсутні. |
| `apps/api` | Каркас FastAPI під майбутню опційну синхронізацію. Зараз — лише `/health` і Pydantic-схеми активного формату даних. |
| `apps/bot` | Мінімальний Telegram-бот (aiogram v3): приватний `/start` із кнопкою відкриття щоденника. Нагадування не надсилає. |
| `docs/prototype` | Архівний дизайн-snapshot. Може містити застарілі стани й тексти та не є acceptance contract. |

## Запуск

### Локальна інфраструктура PostgreSQL 16

```bash
cp .env.example .env
# Заповніть POSTGRES_DB, POSTGRES_ADMIN_USER, POSTGRES_ADMIN_PASSWORD
# і MIGRATION_DATABASE_URL; файл .env не комітьте.
docker compose up -d postgres
(cd apps/api && uv sync --locked && uv run --locked --env-file ../../.env alembic upgrade head)
docker compose ps
```

Це піднімає лише локальну БД і застосовує міграції. Web і далі повністю
працює local-only; синхронізація та Telegram-нагадування ще недоступні.

### Застосунки

```bash
pnpm install
pnpm dev          # web на http://localhost:5173
pnpm test         # Vitest (unit)
pnpm e2e          # Playwright (acceptance-критерії)

(cd apps/api && uv sync --locked && uv run --locked pytest && uv run --locked ruff check . && uv run --locked uvicorn app.main:app --no-access-log)
(cd apps/bot && uv sync --locked && uv run --locked pytest && uv run --locked ruff check .)
```

Для детермінованих демо/тестів дату «сьогодні» можна зафіксувати параметром URL: `http://localhost:5173/?now=2026-01-15`.

## Примітки

- Локальна схема даних v4 розрізняє `present`, явно підтверджене `absent` і `unknown`. «Групи спостереження» є лише many-to-many організацією симптомів і не створюють медичних висновків.
- Міграція з v3 використовує той самий ключ `nd_demo_v3`: старі наявні значення зберігаються, а неоднозначні невибрані симптоми лишаються `unknown`.
- Шрифти Caprasimo/Figtree self-hosted через `@fontsource/*`; кирилиці в них немає, українські тексти падають у системний fallback — так само, як у прототипі.
- Теми `light` / `dark` / `contrast` перемикаються атрибутом `data-theme` на `.nd-frame` (перемикача в UI прототип свідомо не має).
- Demo-дані вигадані й генеруються детерміновано; застосунок не виконує network-запитів.
- Toast-и — локальний неперсистентний feedback про щойно виконану дію, а не канал нагадувань.
- Надійної фонової доставки, browser/system push і налаштувань розкладу зараз немає. Єдиний допустимий майбутній зовнішній канал — Telegram; він потребуватиме нового явного opt-in. Legacy-поля `remOn`/`remTime` ігноруються та не є згодою.
- Майбутній контракт Telegram-доставки винесено в [окрему специфікацію](docs/plans/future-telegram-reminders.md); жодна її можливість не вважається реалізованою.
- Перед будь-яким використанням поза прототипом потрібні окремі клінічний, локалізаційний, privacy та security review — див. [review-gates.md](docs/review-gates.md).
