import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.enums import ChatType

from bot.main import load_config, on_start

WEBAPP_URL = "https://diary.example"


@pytest.mark.parametrize(
    ("env", "missing"),
    [
        ({"WEBAPP_URL": WEBAPP_URL}, "BOT_TOKEN"),
        ({"BOT_TOKEN": "token"}, "WEBAPP_URL"),
        ({"BOT_TOKEN": " ", "WEBAPP_URL": ""}, "BOT_TOKEN, WEBAPP_URL"),
    ],
)
def test_config_fails_fast_when_required_value_is_missing(env, missing):
    with pytest.raises(ValueError, match=missing):
        load_config(env)


def test_start_is_silent_outside_private_chats():
    message = SimpleNamespace(
        chat=SimpleNamespace(type=ChatType.GROUP),
        answer=AsyncMock(),
    )

    asyncio.run(on_start(message, WEBAPP_URL))

    message.answer.assert_not_awaited()


def test_private_start_has_exact_neutral_outbound_allowlist():
    message = SimpleNamespace(
        chat=SimpleNamespace(type=ChatType.PRIVATE),
        answer=AsyncMock(),
        from_user=SimpleNamespace(full_name="PERSONAL_SENTINEL"),
    )

    asyncio.run(on_start(message, WEBAPP_URL))

    message.answer.assert_awaited_once()
    call = message.answer.await_args
    keyboard = call.kwargs["reply_markup"]
    buttons = tuple(
        button for row in keyboard.inline_keyboard for button in row
    )

    assert (call.args[0],) == (
        "Вітаємо! Відкрийте щоденник, щоб продовжити.",
    )
    assert tuple(button.text for button in buttons) == ("Відкрити щоденник",)
    assert tuple(
        button.callback_data
        for button in buttons
        if button.callback_data is not None
    ) == ()
    assert tuple(
        button.web_app.url for button in buttons if button.web_app is not None
    ) == (WEBAPP_URL,)
    assert "PERSONAL_SENTINEL" not in call.args[0]
