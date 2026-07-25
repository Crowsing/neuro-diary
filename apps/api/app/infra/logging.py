"""Structured logging under the §11 allowlist.

The processor drops every key it does not recognise. Masking was rejected: a
masked field still reveals that a field of that shape existed, and the set of
fields a request carries is itself an inference about the person making it.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime
from typing import Any, TextIO
from uuid import UUID

import structlog
from structlog.typing import EventDict, WrappedLogger

# §11, verbatim. Anything absent here never reaches disk.
LOG_ALLOWLIST: frozenset[str] = frozenset(
    {
        "timestamp",
        "level",
        "event",
        "request_id",
        "route_template",
        "method",
        "status_code",
        "duration_ms",
        "account_ref",
        "record_count",
        "revision",
        "error_code",
        "retry_after",
    }
)

_EPOCH_SECONDS = 7 * 24 * 60 * 60
_REF_HEX_LENGTH = 16


def allowlist_processor(
    logger: WrappedLogger,
    method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Keep only the fields §11 permits."""
    del logger, method_name
    return {key: value for key, value in event_dict.items() if key in LOG_ALLOWLIST}


def account_ref(key: bytes, account_id: UUID, now: datetime) -> str:
    """Return the rotating log reference for an account.

    The epoch key changes every seven days, so journals from different weeks
    cannot be joined by activity pattern the way a fixed pseudonym allows.
    """
    epoch = int(now.timestamp()) // _EPOCH_SECONDS
    epoch_key = hmac.new(key, str(epoch).encode("ascii"), hashlib.sha256).digest()
    digest = hmac.new(epoch_key, account_id.bytes, hashlib.sha256).hexdigest()
    return digest[:_REF_HEX_LENGTH]


def client_ref(key: bytes, client_ip: str, now: datetime) -> str:
    """Return an in-memory-only reference for a client address.

    §11 keeps per-IP counters in process memory. Hashing the key as well means a
    memory dump does not hand over the addresses either.
    """
    epoch = int(now.timestamp()) // _EPOCH_SECONDS
    epoch_key = hmac.new(key, str(epoch).encode("ascii"), hashlib.sha256).digest()
    return hmac.new(epoch_key, client_ip.encode("utf-8"), hashlib.sha256).hexdigest()


def configure_logging(*, stream: TextIO | None = None) -> None:
    """Install the allowlist pipeline. Safe to call more than once."""
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            # Runs last so nothing added above it can slip a value through.
            allowlist_processor,
            structlog.processors.JSONRenderer(sort_keys=True),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=stream),
        cache_logger_on_first_use=False,
    )


def ensure_configured() -> None:
    """Install the pipeline only if nothing configured one yet.

    A composition root must never silently replace an existing configuration:
    doing so would discard a caller's sink, and a caller that forgot to set one
    would otherwise log without the allowlist. This resolves both directions.
    """
    if not structlog.is_configured():
        configure_logging()


def get_logger(**initial: Any) -> Any:
    return structlog.get_logger(**initial)
