"""Telegram entry point for opening Neuro Diary."""

import os
import sys
from collections.abc import Mapping
from functools import partial

from aiogram import Dispatcher, Router
from aiogram.enums import ChatType
from aiogram.filters import CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

WELCOME_TEXT = "Вітаємо! Відкрийте щоденник, щоб продовжити."
OPEN_DIARY_LABEL = "Відкрити щоденник"


def build_keyboard(webapp_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=OPEN_DIARY_LABEL,
                    web_app=WebAppInfo(url=webapp_url),
                )
            ]
        ]
    )


async def on_start(message: Message, webapp_url: str) -> None:
    if message.chat.type != ChatType.PRIVATE:
        return

    await message.answer(WELCOME_TEXT, reply_markup=build_keyboard(webapp_url))


def build_router(webapp_url: str) -> Router:
    router = Router()
    router.message.register(
        partial(on_start, webapp_url=webapp_url),
        CommandStart(),
    )
    return router


def load_config(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    env = os.environ if env is None else env
    names = ("BOT_TOKEN", "WEBAPP_URL")
    values = (env.get(names[0], "").strip(), env.get(names[1], "").strip())
    missing = [name for name, value in zip(names, values, strict=True) if not value]
    if missing:
        raise ValueError(f"Не задано в env: {', '.join(missing)}")
    return values


def main() -> None:
    try:
        token, webapp_url = load_config()
    except ValueError as error:
        sys.exit(str(error))

    from aiogram import Bot

    bot = Bot(token=token)
    dp = Dispatcher()
    dp.include_router(build_router(webapp_url))
    dp.run_polling(bot)


if __name__ == "__main__":
    main()
