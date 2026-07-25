"""Configuration of the api process.

Everything here is read once at startup and never mutated. Two rules are
enforced rather than documented: the process refuses to start when `BOT_TOKEN`
is visible to it (§5.3), and an unparsable value is an error rather than a
silent default (fail closed).
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

# §8: the production Ed25519 key Telegram signs Mini App initData with. Public
# by definition, which is exactly why api can validate without the bot token.
TELEGRAM_PRODUCTION_PUBLIC_KEY = bytes.fromhex(
    "e7bf03a2fa4602af4580703d88dda5bb59f32ed8b02a56c187fe7d34caed242d"
)

AppEnv = Literal["development", "production"]
_APP_ENVS: frozenset[str] = frozenset({"development", "production"})
_FORBIDDEN_KEYS = ("BOT_TOKEN",)

# Every variable `Settings.from_env` reads. `.env.example` is checked against
# this list, so a new setting cannot quietly skip the documented environment.
SETTINGS_ENV_KEYS = (
    "API_DATABASE_URL",
    "TELEGRAM_BOT_ID",
    "WEBAPP_URL",
    "APP_ENV",
    "TELEGRAM_ED25519_PUBLIC_KEY",
    "LOG_ACCOUNT_REF_KEY",
    "AUTH_MIN_AUTH_DATE",
    "AUTH_HMAC_FALLBACK_ENABLED",
    "WEBAPP_HMAC_SECRET",
)


class ConfigurationError(RuntimeError):
    """The process cannot start with the environment it was given."""


def _required(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise ConfigurationError(f"{key} is required")
    return value


def _optional(env: Mapping[str, str], key: str) -> str | None:
    value = env.get(key, "").strip()
    return value or None


def _flag(env: Mapping[str, str], key: str) -> bool:
    value = env.get(key, "").strip().lower()
    if value in {"", "false", "0", "no"}:
        return False
    if value in {"true", "1", "yes"}:
        return True
    raise ConfigurationError(f"{key} must be a boolean")


def _key_bytes(value: str, key: str, *, size: int) -> bytes:
    try:
        raw = bytes.fromhex(value)
    except ValueError as error:
        raise ConfigurationError(f"{key} must be hex-encoded") from error
    if len(raw) != size:
        raise ConfigurationError(f"{key} must decode to {size} bytes")
    return raw


def _instant(value: str, key: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ConfigurationError(f"{key} must be an ISO-8601 instant") from error
    if parsed.tzinfo is None:
        raise ConfigurationError(f"{key} must carry a timezone offset")
    return parsed.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything the api process is allowed to know about its deployment."""

    api_database_url: str
    telegram_bot_id: int
    webapp_url: str
    app_env: AppEnv
    telegram_ed25519_public_key: bytes
    log_account_ref_key: bytes
    min_auth_date: datetime | None = None
    auth_hmac_fallback_enabled: bool = False
    webapp_hmac_secret: bytes | None = None

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        for key in _FORBIDDEN_KEYS:
            if env.get(key, "").strip():
                # The value itself is never echoed: this message reaches logs.
                raise ConfigurationError(
                    f"{key} must never be visible to the api process"
                )

        bot_id_raw = _required(env, "TELEGRAM_BOT_ID")
        try:
            bot_id = int(bot_id_raw)
        except ValueError as error:
            raise ConfigurationError("TELEGRAM_BOT_ID must be an integer") from error
        if bot_id <= 0:
            raise ConfigurationError("TELEGRAM_BOT_ID must be positive")

        app_env = env.get("APP_ENV", "").strip() or "production"
        if app_env not in _APP_ENVS:
            raise ConfigurationError("APP_ENV must be development or production")

        public_key_hex = _optional(env, "TELEGRAM_ED25519_PUBLIC_KEY")
        public_key = (
            _key_bytes(public_key_hex, "TELEGRAM_ED25519_PUBLIC_KEY", size=32)
            if public_key_hex
            else TELEGRAM_PRODUCTION_PUBLIC_KEY
        )

        log_key_hex = _optional(env, "LOG_ACCOUNT_REF_KEY")
        # An absent key yields a per-process random one: journals then cannot be
        # joined across restarts either, which is stronger than a shared secret.
        log_key = (
            _key_bytes(log_key_hex, "LOG_ACCOUNT_REF_KEY", size=32)
            if log_key_hex
            else secrets.token_bytes(32)
        )

        fallback_enabled = _flag(env, "AUTH_HMAC_FALLBACK_ENABLED")
        hmac_secret_hex = _optional(env, "WEBAPP_HMAC_SECRET")
        if fallback_enabled and not hmac_secret_hex:
            raise ConfigurationError(
                "WEBAPP_HMAC_SECRET is required when the HMAC fallback is enabled"
            )
        hmac_secret = (
            _key_bytes(hmac_secret_hex, "WEBAPP_HMAC_SECRET", size=32)
            if hmac_secret_hex
            else None
        )

        min_auth_date_raw = _optional(env, "AUTH_MIN_AUTH_DATE")

        return cls(
            api_database_url=_required(env, "API_DATABASE_URL"),
            telegram_bot_id=bot_id,
            webapp_url=_required(env, "WEBAPP_URL"),
            app_env=app_env,  # type: ignore[arg-type]
            telegram_ed25519_public_key=public_key,
            log_account_ref_key=log_key,
            min_auth_date=(
                _instant(min_auth_date_raw, "AUTH_MIN_AUTH_DATE")
                if min_auth_date_raw
                else None
            ),
            auth_hmac_fallback_enabled=fallback_enabled,
            webapp_hmac_secret=hmac_secret,
        )
