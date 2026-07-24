# Neuro Diary Bot

Мінімальний Telegram launcher для Neuro Diary на aiogram v3.

Бот не доставляє фонові нагадування: scheduler, snooze та browser/system push
не реалізовані. Команда `/start` працює лише в приватному чаті й повертає
статичне нейтральне привітання з WebApp-кнопкою «Відкрити щоденник».

Відповідь не інтерполює імена, групи, симптоми, оцінки, цикл, нотатки або
дати. Бот не зберігає стан щоденника.

## Запуск

```bash
uv sync
BOT_TOKEN=... WEBAPP_URL=https://example.com uv run python -m bot.main
```

`BOT_TOKEN` і `WEBAPP_URL` обов’язкові. Якщо будь-якого значення немає,
конфігурація завершується з помилкою до запуску polling.

## Перевірка

```bash
uv run pytest
uv run ruff check .
```
