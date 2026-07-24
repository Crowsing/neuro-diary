# Neuro Diary API

Серверна частина: ідентичність, згоди й автентифікація Telegram Mini App.

## Стан справ (чесно)

Веб-застосунок (`apps/web`) лишається **повністю offline-first**: усі дані живуть
у localStorage і нікуди не надсилаються. Синхронізації немає — у Фазі 1 немає
жодного медичного ендпоінта, і це перевіряється тестом, а не обіцянкою.

Що вже працює на сервері: створення облікового запису **виключно разом із першою
згодою**, надання й відкликання згод, сесії та стирання акаунта. Без явної згоди
сервер не зберігає нічого.

## Що тут є

- `app/api/v1/` — тонкі роутери: `/health`, `/v1/auth/telegram`, `/v1/sessions`,
  `/v1/sessions/revoke-others`, `/v1/consents`, `/v1/consents/revoke`,
  `/v1/account/delete`. Назва згоди ніколи не з'являється в шляху.
- `app/services/` — вся бізнес-логіка; залежить лише від Protocol-портів.
- `app/domain/` — чисті значення: три згоди, таблиця step-up §8, quiet hours,
  строки зберігання. Нуль I/O.
- `app/infra/` — конфіг, лог-процесор з allowlist, валідація initData (Ed25519),
  реєстр текстів згод, БД (SQLAlchemy + Alembic).
- `app/workers/erasure_worker.py` — строк 30 діб за `revoke_reason` і TTL
  службових таблиць.
- `consent-copy/` (у корені репозиторію) — тексти згод і їхні хеші, спільні
  з `apps/web`.

## Запуск

```bash
uv sync --locked
```

```bash
uv run --locked uvicorn app.main:app_factory --factory --reload --no-access-log
```

Змінні середовища — у кореневому `.env.example`. `BOT_TOKEN` серед них немає
свідомо: процес **відмовляється стартувати**, якщо бачить його в оточенні.

## Тести

```bash
uv run --locked pytest
```

```bash
uv run --locked ruff check . && uv run --locked ruff format --check . && uv run --locked mypy --strict app && uv run --locked lint-imports
```

Інтеграційні тести піднімають реальну PostgreSQL 16 через testcontainers і
підключаються роллю `api_rw`, а не адміном. На macOS із Docker Desktop Ryuk не
може змонтувати сокет, тому локально:

```bash
TESTCONTAINERS_RYUK_DISABLED=true uv run --locked pytest
```

Окремий gate — 100% branch coverage валідатора initData:

```bash
uv run --locked pytest tests/unit/test_initdata_validator.py --cov=app.infra.telegram.initdata --cov-branch --cov-fail-under=100
```
