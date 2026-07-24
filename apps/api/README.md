# Neuro Diary API

Плейсхолдер під майбутню опційну синхронізацію.

## Стан справ (чесно)

Застосунок зараз **повністю offline-first**: усі дані живуть у localStorage
веб-застосунку (`apps/web`) і нікуди не надсилаються. Цей сервіс — каркас
на майбутнє, коли (і якщо) з'явиться **опційна** синхронізація з **окремою
явною згодою** користувачки. Без такої згоди жодні дані на сервер не підуть.

## Що тут є

- `app/main.py` — FastAPI-застосунок з єдиним ендпоінтом `GET /health`.
- `app/schemas.py` — Pydantic-моделі активного local JSON-контракту та явні
  legacy input adapters, які ігнорують retired reminder-поля (без ендпоінтів).
- `tests/` — health-check і контрактні тести схем.

## Запуск

```bash
uv sync
uv run uvicorn app.main:app --reload
```

## Тести

```bash
uv run pytest
uv run ruff check .
```
