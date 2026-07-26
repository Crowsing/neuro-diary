"""§10: the metadata a stranger reads off a lock screen, checked for real.

Everything else in this suite asserts what the system *sends*. This module
asserts what it *is called*, which is the larger disclosure: the chat title
appears in the notification shade, in the chat list and on the lock screen of a
phone lying face up on a table, and the Mini App domain appears in the button
and — permanently — in the public certificate transparency logs.

**This test is allowed to be red, and being red is its whole job.** The bot is
named `Neuronium` and answers to `@neuronium_bot`; both contain `neuro`, which
§10 lists among the tokens that must not appear. Neither can be changed from
here — `setMyName` exists but `@username` and the avatar live in BotFather — so
the fix is a line for a person, and weakening the assertion to get a green run
would delete the only thing that remembers it.

**Without a token it skips; with one it does not forgive.** CI has no bot and
will not get one, so a test that required the token would either never run or
turn every pull request red for a reason nobody can act on. A test that quietly
passed without it would be worse: it would report a surface it never looked at.

The four read methods used here are deliberately **not** in the adapter's
allowlist (§5.3 pt. 6 fixes that at `sendMessage` and `deleteMessages`). They
are called with a plain HTTP client from a test, which is the only place they
belong: the running worker must never be able to ask Telegram anything.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

from app.domain.reminders import FORBIDDEN_SURFACE_TOKENS, forbidden_tokens_in

REPO_ROOT = Path(__file__).resolve().parents[4]
BOT_ENV = REPO_ROOT / "apps" / "bot" / ".env"
ROOT_ENV = REPO_ROOT / ".env"

API_ROOT = "https://api.telegram.org"
TIMEOUT = 10.0

#: `getMe` carries the name and the username; the other three carry the text a
#: person sees on the bot's profile card.
SURFACE_METHODS = ("getMe", "getMyName", "getMyDescription", "getMyShortDescription")


def _from_env_file(path: Path, key: str) -> str | None:
    if not path.is_file():
        return None
    for line in path.read_text("utf-8").splitlines():
        name, _, value = line.partition("=")
        if name.strip() == key:
            return value.strip() or None
    return None


def _setting(key: str) -> str | None:
    """The environment first, then the files the local stand writes."""
    return (
        os.environ.get(key, "").strip()
        or _from_env_file(BOT_ENV, key)
        or _from_env_file(ROOT_ENV, key)
    )


@pytest.fixture(scope="module")
def bot_token() -> str:
    token = _setting("BOT_TOKEN")
    if not token:
        pytest.skip("no BOT_TOKEN: the §10 surface cannot be read without one")
    return token


@pytest.fixture(scope="module")
def surface(bot_token: str) -> dict[str, object]:
    """One call per method, merged into the flat set of strings §10 governs."""
    values: dict[str, object] = {}
    with httpx.Client(timeout=TIMEOUT) as client:
        for method in SURFACE_METHODS:
            response = client.post(f"{API_ROOT}/bot{bot_token}/{method}")
            if response.status_code != httpx.codes.OK:
                pytest.fail(f"{method} answered {response.status_code}")
            result = response.json().get("result")
            if isinstance(result, dict):
                values.update(result)
    return values


def test_the_token_list_is_the_one_the_plan_names() -> None:
    """Runs without a bot: a list that quietly shrank would pass everything."""
    for token in ("невро", "neuro", "симптом", "мігрен", "епілепс", "склероз"):
        assert token in FORBIDDEN_SURFACE_TOKENS
    assert forbidden_tokens_in("Neuronium") == ["neuro"]
    assert forbidden_tokens_in("Щоденник") == []


@pytest.mark.parametrize(
    "field",
    ["first_name", "username", "name", "description", "short_description"],
)
def test_no_visible_bot_field_carries_a_medical_token(
    surface: dict[str, object],
    field: str,
) -> None:
    """§10, and the reason it is not decorative: this is what a stranger sees."""
    value = surface.get(field)
    if not isinstance(value, str) or not value:
        pytest.skip(f"{field} is not set on this bot")

    assert forbidden_tokens_in(value) == [], f"{field} carries a §10 token"


def test_the_mini_app_domain_carries_no_medical_token() -> None:
    """The domain is irreversible after the first certificate (§10).

    It runs with or without a token, because the URL is a deployment decision
    rather than something Telegram holds — and because it is cheap to fix now
    and impossible to fix later: `http://localhost:4174` is not yet in any
    public log, so the name can still be chosen.
    """
    webapp_url = _setting("WEBAPP_URL")
    if not webapp_url:
        pytest.skip("WEBAPP_URL is not configured in this environment")

    host = urlsplit(webapp_url).hostname or ""

    assert forbidden_tokens_in(host) == [], "the Mini App domain carries a §10 token"


def test_the_description_fields_are_empty_rather_than_helpful(
    surface: dict[str, object],
) -> None:
    """The safest profile card is the one that says nothing.

    §10 governs which words may appear; it does not require any to. An empty
    `description` cannot drift into naming a condition, and the copy that would
    fill it has no reviewer — the localisation review is still open.

    `can_join_groups` is **not** asserted here. It is `true` on this bot, and
    §10 does not forbid that: the code answers nothing outside a private chat.
    It is a wider surface than «private chats only» needs, switched off in
    BotFather rather than in code, so it belongs in the progress file as an
    operational line and not in a red test about a rule it does not break.
    """
    for field in ("description", "short_description"):
        assert not surface.get(field), f"{field} is set and has no reviewer"
