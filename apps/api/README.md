# Neuro Diary API

Плейсхолдер під майбутню опційну синхронізацію.

## Стан справ (чесно)

Застосунок зараз **повністю offline-first**: усі дані живуть у localStorage
веб-застосунку (`apps/web`) і нікуди не надсилаються. Цей сервіс — каркас
на майбутнє, коли (і якщо) з'явиться **опційна** синхронізація з **окремою
явною згодою** користувачки. Без такої згоди жодні дані на сервер не підуть.

## Що тут є

- `app/main.py` — FastAPI-застосунок з єдиним ендпоінтом `GET /health`.
- `app/schemas/contract.py` — Pydantic-моделі активного local JSON-контракту та явні
  legacy input adapters, які ігнорують retired reminder-поля (без ендпоінтів).
- `app/infra/db/migrations/` — Alembic-міграція PostgreSQL 16 з ізольованими
  ролями та GRANT-матрицею.
- `tests/` — health-check, контрактні тести схем та інтеграційні тести
  міграції на тимчасовій PostgreSQL 16.

## Запуск

```bash
uv sync --locked
uv run --locked uvicorn app.main:app --reload --no-access-log
```

## Тести

```bash
uv run --locked pytest
uv run --locked ruff check .
uv run --locked mypy --strict app
uv run --locked lint-imports
```
