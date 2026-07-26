"""The outbound surface of §10, asserted against the real adapter.

`httpx.MockTransport` is used rather than a fake port on purpose: the allowlist
lives in the request body this module builds, so a double that skipped the
building would leave the one thing worth testing untested. No socket is opened
and no token is needed — CI has neither.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.domain.reminders import (
    BUTTON_LABEL,
    MESSAGE_TEXT,
    SendOutcome,
)
from app.infra.telegram.bot_api import (
    ALLOWED_METHODS,
    TelegramBotApi,
    TelegramConfigurationError,
)

TOKEN = "1234567890:synthetic-token-never-a-real-one"
WEBAPP_URL = "https://diary.example.invalid/"
CHAT_ID = 4242424242
NOW = datetime(2026, 7, 24, 17, 0, tzinfo=UTC)


class Telegram:
    """A mock Bot API that records what was asked of it."""

    def __init__(self, *responses: httpx.Response) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            method = request.url.path.rsplit("/", 1)[-1]
            body: dict[str, Any] = json.loads(request.content)
            self.calls.append((method, body))
            if not self._responses:  # pragma: no cover - a test asked for too many
                raise AssertionError("no response left for " + method)
            return self._responses.pop(0)

        return httpx.MockTransport(handle)


def _ok(message_id: int = 555) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": {"message_id": message_id}})


def _throttled(retry_after: int) -> httpx.Response:
    return httpx.Response(
        429,
        json={
            "ok": False,
            "error_code": 429,
            "parameters": {"retry_after": retry_after},
        },
    )


def _build(
    telegram: Telegram,
    *,
    webapp_url: str = WEBAPP_URL,
    sleeps: list[float] | None = None,
    now: datetime = NOW,
) -> TelegramBotApi:
    ticks = iter(range(0, 10_000))
    return TelegramBotApi(
        token=TOKEN,
        webapp_url=webapp_url,
        client=httpx.Client(transport=telegram.transport()),
        clock=lambda: now,
        sleep=(sleeps.append if sleeps is not None else lambda _: None),
        monotonic=lambda: float(next(ticks)),
    )


# ------------------------------------------------------------- exact allowlist


def test_the_outbound_message_is_exactly_the_two_allowlisted_strings() -> None:
    telegram = Telegram(_ok())

    _build(telegram).send_reminder(
        chat_id=CHAT_ID,
        deadline=NOW + timedelta(minutes=10),
    )

    method, body = telegram.calls[0]
    assert method == "sendMessage"
    assert body["text"] == "Час зробити короткий запис"
    assert body["text"] == MESSAGE_TEXT
    keyboard = body["reply_markup"]["inline_keyboard"]
    assert keyboard == [[{"text": BUTTON_LABEL, "url": WEBAPP_URL}]]
    assert keyboard[0][0]["text"] == "Відкрити щоденник"


def test_the_body_carries_nothing_but_the_chat_and_the_constant() -> None:
    """Anything else in the body is a signal the reminder is not allowed to send."""
    telegram = Telegram(_ok())

    _build(telegram).send_reminder(
        chat_id=CHAT_ID,
        deadline=NOW + timedelta(minutes=10),
    )

    _, body = telegram.calls[0]
    assert set(body) == {"chat_id", "text", "reply_markup"}
    assert "callback_data" not in str(body)
    # Nothing about the diary, and nothing that varies between two users beyond
    # the address the message is going to.
    for leak in ("entry", "symptom", "запис у", "сьогодні", "streak", "count"):
        assert leak not in str(body).lower()


def test_the_message_never_ends_with_a_full_stop() -> None:
    """Gate D confirmed the string verbatim, and the missing stop is part of it."""
    assert not MESSAGE_TEXT.endswith(".")


def test_only_the_two_methods_of_section_five_three_are_reachable() -> None:
    assert ALLOWED_METHODS == {"sendMessage", "deleteMessages"}


def test_deletion_uses_the_allowlisted_method() -> None:
    telegram = Telegram(httpx.Response(200, json={"ok": True, "result": True}))

    deleted = _build(telegram).delete_message(chat_id=CHAT_ID, message_id=99)

    assert deleted is True
    method, body = telegram.calls[0]
    assert method == "deleteMessages"
    assert body == {"chat_id": CHAT_ID, "message_ids": [99]}


# ------------------------------------------------------------------ the button


@pytest.mark.parametrize(
    "url",
    [
        "https://diary.example.invalid/?utm=telegram",
        "https://diary.example.invalid/#today",
        "https://diary.example.invalid/?account=42",
    ],
)
def test_a_url_with_anything_appended_refuses_to_start(url: str) -> None:
    """§10 allows exactly the configured URL — a query is where a leak begins."""
    with pytest.raises(TelegramConfigurationError):
        _build(Telegram(), webapp_url=url)


@pytest.mark.parametrize("url", ["", "not-a-url", "ftp://example.invalid"])
def test_a_url_that_is_not_an_absolute_web_address_refuses_to_start(
    url: str,
) -> None:
    with pytest.raises(TelegramConfigurationError):
        _build(Telegram(), webapp_url=url)


def test_a_plain_http_url_is_accepted_because_the_stand_uses_one() -> None:
    """The counterpart: without it the rule above could reject every URL.

    `http://localhost:4174` is what the local stand configures, and refusing it
    would make the adapter untestable against the only deployment there is.
    """
    telegram = Telegram(_ok())

    _build(telegram, webapp_url="http://localhost:4174").send_reminder(
        chat_id=CHAT_ID,
        deadline=NOW + timedelta(minutes=10),
    )

    _, body = telegram.calls[0]
    assert body["reply_markup"]["inline_keyboard"][0][0]["url"] == (
        "http://localhost:4174"
    )


# --------------------------------------------------------------------- refusals


def test_a_forbidden_response_is_a_block_and_not_a_failure() -> None:
    telegram = Telegram(httpx.Response(403, json={"ok": False, "error_code": 403}))

    receipt = _build(telegram).send_reminder(
        chat_id=CHAT_ID,
        deadline=NOW + timedelta(minutes=10),
    )

    assert receipt.outcome is SendOutcome.BLOCKED
    assert receipt.message_id is None


@pytest.mark.parametrize("status", [400, 500, 502, 503])
def test_a_bad_request_or_a_server_error_fails_without_a_second_attempt(
    status: int,
) -> None:
    """at-most-once: after these, whether the message went out is unknowable."""
    telegram = Telegram(httpx.Response(status, json={"ok": False}))

    receipt = _build(telegram).send_reminder(
        chat_id=CHAT_ID,
        deadline=NOW + timedelta(minutes=10),
    )

    assert receipt.outcome is SendOutcome.FAILED
    assert len(telegram.calls) == 1


def test_a_transport_error_fails_without_a_second_attempt() -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("no route", request=request)

    api = TelegramBotApi(
        token=TOKEN,
        webapp_url=WEBAPP_URL,
        client=httpx.Client(transport=httpx.MockTransport(explode)),
        clock=lambda: NOW,
        sleep=lambda _: None,
        monotonic=lambda: 0.0,
    )

    receipt = api.send_reminder(chat_id=CHAT_ID, deadline=NOW + timedelta(minutes=10))

    assert receipt.outcome is SendOutcome.FAILED


def test_a_success_without_a_message_id_is_not_reported_as_sent() -> None:
    """§6.4 needs the id to take the message back; `sent` without one is a lie."""
    telegram = Telegram(httpx.Response(200, json={"ok": True, "result": {}}))

    receipt = _build(telegram).send_reminder(
        chat_id=CHAT_ID,
        deadline=NOW + timedelta(minutes=10),
    )

    assert receipt.outcome is SendOutcome.FAILED


# ------------------------------------------------------------------ throttling


def test_a_throttled_request_is_retried_once_the_wait_is_over() -> None:
    """§10: a 429 means Telegram did not process it, so a retry cannot duplicate."""
    telegram = Telegram(_throttled(3), _ok(message_id=77))
    sleeps: list[float] = []

    receipt = _build(telegram, sleeps=sleeps).send_reminder(
        chat_id=CHAT_ID,
        deadline=NOW + timedelta(minutes=10),
    )

    assert receipt.outcome is SendOutcome.SENT
    assert receipt.message_id == 77
    assert 3.0 in sleeps
    assert [method for method, _ in telegram.calls] == ["sendMessage", "sendMessage"]


def test_a_wait_that_would_outlive_the_budget_is_refused() -> None:
    """The inequality that keeps the sweeper from meeting a live attempt."""
    telegram = Telegram(_throttled(600))
    sleeps: list[float] = []

    receipt = _build(telegram, sleeps=sleeps).send_reminder(
        chat_id=CHAT_ID,
        deadline=NOW + timedelta(minutes=5),
    )

    assert receipt.outcome is SendOutcome.FAILED
    assert sleeps == []
    assert len(telegram.calls) == 1


def test_a_wait_that_would_cross_into_quiet_hours_is_refused() -> None:
    """§10 bounds `retry_after` by the validity window of the same local day."""
    telegram = Telegram(_throttled(120))

    receipt = _build(telegram).send_reminder(
        chat_id=CHAT_ID,
        # The caller has already reduced the deadline to nightfall; one minute
        # of it is left and the wait asks for two.
        deadline=NOW + timedelta(minutes=1),
    )

    assert receipt.outcome is SendOutcome.FAILED


def test_a_throttling_without_a_retry_after_is_not_retried() -> None:
    telegram = Telegram(httpx.Response(429, json={"ok": False, "error_code": 429}))

    receipt = _build(telegram).send_reminder(
        chat_id=CHAT_ID,
        deadline=NOW + timedelta(minutes=10),
    )

    assert receipt.outcome is SendOutcome.FAILED
    assert len(telegram.calls) == 1


def test_a_retry_after_header_is_honoured_when_the_body_has_none() -> None:
    telegram = Telegram(
        httpx.Response(429, headers={"Retry-After": "2"}, json={"ok": False}),
        _ok(),
    )
    sleeps: list[float] = []

    receipt = _build(telegram, sleeps=sleeps).send_reminder(
        chat_id=CHAT_ID,
        deadline=NOW + timedelta(minutes=10),
    )

    assert receipt.outcome is SendOutcome.SENT
    assert 2.0 in sleeps


# ---------------------------------------------------------------------- pacing


def test_the_token_bucket_paces_sends_at_the_configured_rate() -> None:
    """§10: ~25 msg/s, spent one message at a time and never in a burst."""
    telegram = Telegram(_ok(), _ok(), _ok())
    sleeps: list[float] = []
    ticks = iter([0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    api = TelegramBotApi(
        token=TOKEN,
        webapp_url=WEBAPP_URL,
        client=httpx.Client(transport=telegram.transport()),
        clock=lambda: NOW,
        sleep=sleeps.append,
        monotonic=lambda: next(ticks),
        rate_per_second=25,
    )

    for _ in range(3):
        api.send_reminder(chat_id=CHAT_ID, deadline=NOW + timedelta(minutes=10))

    # A clock that never moves means every message after the first has to wait
    # its whole interval: two waits for three messages.
    assert sleeps == [pytest.approx(0.04), pytest.approx(0.08)]


def test_a_method_outside_the_allowlist_cannot_be_called() -> None:
    """§5.3 pt. 6 as a runtime refusal, not only as a constant.

    The guard used to carry `# pragma: no cover - defended by tests` while no
    test ever entered it: the allowlist assertion above compares a frozenset,
    which says nothing about reachability.
    """
    api = _build(Telegram())

    with pytest.raises(TelegramConfigurationError):
        api._call("getChatMember", {"chat_id": CHAT_ID})


def test_a_server_that_always_throttles_is_not_hammered_for_ten_minutes() -> None:
    """`retry_after: 0` is legal, and a deadline alone does not stop it.

    Without an attempt ceiling the bucket would issue thousands of requests
    inside one budget — aimed at a service that had just asked to be left alone.
    """
    telegram = Telegram(*[_throttled(0) for _ in range(20)])
    sleeps: list[float] = []

    receipt = _build(telegram, sleeps=sleeps).send_reminder(
        chat_id=CHAT_ID,
        deadline=NOW + timedelta(minutes=10),
    )

    assert receipt.outcome is SendOutcome.FAILED
    assert len(telegram.calls) == 5
