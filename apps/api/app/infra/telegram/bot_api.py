"""Telegram Bot API adapter for the reminder worker (§10, §5.3).

Two methods reach Telegram from this process, and §5.3 pt. 6 fixes the list at
exactly that: `sendMessage` and `deleteMessages`. There is no `getChat`, no
`getChatMember`, no `getUserProfilePhotos` — a bot that can ask Telegram *about*
a person is a bot that can learn something about her, and the only two calls
here either deliver a fixed sentence or take one back.

**The outbound message is a constant.** Text, button label and URL are read from
`app.domain.reminders` and never composed: there is no format string, no
f-string and no parameter on the path from a schedule row to a request body.
The reminder is blind by construction — it cannot say «you have not written
today» because nothing in this module knows.

**What is retried and what is not.** Only 429. Telegram states that a
rate-limited request was not processed, so waiting out `retry_after` and trying
again cannot duplicate a message (§10 says so in as many words). A 5xx, a 400
and a timeout are all `FAILED` without a second attempt: after those the
delivery may or may not have happened, and at-most-once resolves that doubt
against sending.

**The token appears in the URL** — that is how the Bot API works — which is why
nothing here logs a URL, an exception or a response body. The only field this
module ever emits is `retry_after`, which §11 allowlists.
"""

from __future__ import annotations

import time as time_module
from collections.abc import Callable
from datetime import datetime
from urllib.parse import urlsplit

import httpx

from app.domain.reminders import (
    BUTTON_LABEL,
    MESSAGE_TEXT,
    SEND_RATE_PER_SECOND,
    SendOutcome,
    SendReceipt,
)
from app.infra.logging import get_logger

#: §5.3 pt. 6, verbatim. Every request this module makes names one of these.
ALLOWED_METHODS = frozenset({"sendMessage", "deleteMessages"})

_API_ROOT = "https://api.telegram.org"
_TIMEOUT_SECONDS = 10.0
_TOO_MANY_REQUESTS = 429
_FORBIDDEN = 403

#: A ceiling on the 429 loop, beside the deadline rather than instead of it.
#: `retry_after: 0` is a legal answer, and with only a deadline to stop it the
#: worker would spend ten minutes hammering Telegram at bucket rate — thousands
#: of requests aimed at a service that just asked to be left alone.
_MAX_ATTEMPTS = 5


class TelegramConfigurationError(RuntimeError):
    """The deployment cannot deliver a message that satisfies §10."""


class _TokenBucket:
    """§10 pacing, ~25 messages a second, spent one message at a time.

    Monotonic rather than wall-clock: a clock stepped backwards by NTP would
    otherwise hand out an unbounded burst exactly when the host is least
    healthy.
    """

    def __init__(
        self,
        *,
        rate_per_second: int,
        monotonic: Callable[[], float],
        sleep: Callable[[float], None],
    ) -> None:
        self._interval = 1.0 / rate_per_second
        self._monotonic = monotonic
        self._sleep = sleep
        self._next_allowed = 0.0

    def take(self) -> None:
        now = self._monotonic()
        if now < self._next_allowed:
            self._sleep(self._next_allowed - now)
            now = self._next_allowed
        self._next_allowed = now + self._interval


class TelegramBotApi:
    """The `TelegramDeliveryPort` implementation that talks to the real API."""

    def __init__(
        self,
        *,
        token: str,
        webapp_url: str,
        client: httpx.Client,
        clock: Callable[[], datetime],
        sleep: Callable[[float], None] = time_module.sleep,
        monotonic: Callable[[], float] = time_module.monotonic,
        rate_per_second: int = SEND_RATE_PER_SECOND,
    ) -> None:
        _assert_button_url_is_allowlisted(webapp_url)
        self._token = token
        self._webapp_url = webapp_url
        self._client = client
        self._clock = clock
        self._sleep = sleep
        self._bucket = _TokenBucket(
            rate_per_second=rate_per_second,
            monotonic=monotonic,
            sleep=sleep,
        )

    def send_reminder(self, *, chat_id: int, deadline: datetime) -> SendReceipt:
        """Deliver the one allowlisted message, or say why it did not go.

        `deadline` is the end of this attempt's budget (§10). It bounds the
        `retry_after` waits and nothing else — the caller has already decided
        that the occurrence belongs to today, so a wait that would cross the
        deadline is refused rather than shortened.
        """
        body: dict[str, object] = {
            "chat_id": chat_id,
            "text": MESSAGE_TEXT,
            "reply_markup": {
                "inline_keyboard": [[{"text": BUTTON_LABEL, "url": self._webapp_url}]]
            },
        }

        for attempt in range(_MAX_ATTEMPTS):
            del attempt
            self._bucket.take()
            response = self._call("sendMessage", body)
            if response is None:
                return SendReceipt(outcome=SendOutcome.FAILED)
            if response.status_code == _FORBIDDEN:
                return SendReceipt(outcome=SendOutcome.BLOCKED)
            if response.status_code == _TOO_MANY_REQUESTS:
                if not self._wait_out(response, deadline=deadline):
                    return SendReceipt(outcome=SendOutcome.FAILED)
                continue
            if response.status_code != httpx.codes.OK:
                return SendReceipt(outcome=SendOutcome.FAILED)
            message_id = _message_id_of(response)
            if message_id is None:
                # A 200 whose shape we do not recognise is not a delivery we can
                # ever clean up (§6.4 needs the id), and claiming `sent` without
                # one would leave a message in the chat with nothing pointing
                # at it. `failed` is the honest half of that pair.
                return SendReceipt(outcome=SendOutcome.FAILED)
            return SendReceipt(outcome=SendOutcome.SENT, message_id=message_id)
        return SendReceipt(outcome=SendOutcome.FAILED)

    def delete_message(self, *, chat_id: int, message_id: int) -> bool:
        """§6.4: take one message back. A refusal is not an error to retry.

        Telegram allows a bot to delete its own message for a limited time, so
        a failure here is expected near the end of the window. The queue has an
        unconditional TTL for exactly that reason, and the caller drops the row
        either way.
        """
        self._bucket.take()
        response = self._call(
            "deleteMessages",
            {"chat_id": chat_id, "message_ids": [message_id]},
        )
        return response is not None and response.status_code == httpx.codes.OK

    def _call(self, method: str, body: dict[str, object]) -> httpx.Response | None:
        if method not in ALLOWED_METHODS:
            raise TelegramConfigurationError("method outside the §5.3 allowlist")
        try:
            return self._client.post(
                f"{_API_ROOT}/bot{self._token}/{method}",
                json=body,
                timeout=_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError:
            # Neither the exception nor the URL is logged: the URL carries the
            # token and the exception carries the URL. Whether the message went
            # out is unknowable from here, and at-most-once answers that doubt
            # by not trying again.
            get_logger().warning("reminder_send_failed")
            return None

    def _wait_out(self, response: httpx.Response, *, deadline: datetime) -> bool:
        """Honour `retry_after`, but only inside the attempt budget."""
        retry_after = _retry_after_of(response)
        if retry_after is None:
            return False
        remaining = (deadline - self._clock()).total_seconds()
        if retry_after > remaining:
            return False
        # §11 allowlists `retry_after`; nothing else about the response is
        # loggable, and the chat id in particular is not.
        get_logger().info("reminder_send_throttled", retry_after=retry_after)
        self._sleep(retry_after)
        return True


def _assert_button_url_is_allowlisted(webapp_url: str) -> None:
    """§10: the button carries the configured URL and nothing appended to it.

    Checked once, at construction, so a deployment that would put a query
    parameter in front of Telegram fails to start rather than fails privately on
    the first evening at eight o'clock.
    """
    parts = urlsplit(webapp_url)
    if parts.query or parts.fragment:
        raise TelegramConfigurationError(
            "WEBAPP_URL must carry no query and no fragment: §10 allows exactly"
            " the configured URL in the reminder button"
        )
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise TelegramConfigurationError("WEBAPP_URL must be an absolute http(s) URL")


def _message_id_of(response: httpx.Response) -> int | None:
    payload = _payload_of(response)
    result = payload.get("result") if payload else None
    if not isinstance(result, dict):
        return None
    message_id = result.get("message_id")
    return message_id if isinstance(message_id, int) else None


def _retry_after_of(response: httpx.Response) -> float | None:
    payload = _payload_of(response)
    parameters = payload.get("parameters") if payload else None
    if isinstance(parameters, dict):
        value = parameters.get("retry_after")
        if isinstance(value, int | float) and value >= 0:
            return float(value)
    header = response.headers.get("Retry-After")
    if header is not None:
        try:
            return float(header)
        except ValueError:
            return None
    return None


def _payload_of(response: httpx.Response) -> dict[str, object]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}
