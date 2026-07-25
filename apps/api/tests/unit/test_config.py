"""The api process configuration, including the settings it must refuse to hold.

§5.3 and §11 keep `BOT_TOKEN` in the bot and the reminder worker only. That is a
property of this process, so it is enforced here rather than trusted to a deploy
checklist.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.infra.config import (
    SETTINGS_ENV_KEYS,
    TELEGRAM_PRODUCTION_PUBLIC_KEY,
    ConfigurationError,
    Settings,
)

REPO_ROOT = Path(__file__).resolve().parents[4]

BASE_ENV = {
    "API_DATABASE_URL": "postgresql://api_rw@localhost:5432/diary",
    "TELEGRAM_BOT_ID": "1234567890",
    "WEBAPP_URL": "https://example.invalid",
    "APP_ENV": "development",
}


def test_reads_the_documented_environment() -> None:
    settings = Settings.from_env(BASE_ENV)

    assert settings.telegram_bot_id == 1234567890
    assert settings.webapp_url == "https://example.invalid"
    assert settings.app_env == "development"
    assert settings.telegram_ed25519_public_key == TELEGRAM_PRODUCTION_PUBLIC_KEY
    assert settings.min_auth_date is None
    assert settings.auth_hmac_fallback_enabled is False
    assert settings.webapp_hmac_secret is None


def test_production_is_the_default_environment() -> None:
    env = {key: value for key, value in BASE_ENV.items() if key != "APP_ENV"}

    assert Settings.from_env(env).app_env == "production"


@pytest.mark.parametrize("fallback", ["true", "false"])
def test_bot_token_in_the_environment_is_refused_under_any_flag(
    fallback: str,
) -> None:
    env = {
        **BASE_ENV,
        "BOT_TOKEN": "1234567890:AAHrandomlookingsecretvalue",
        "AUTH_HMAC_FALLBACK_ENABLED": fallback,
        "WEBAPP_HMAC_SECRET": "aa" * 32,
    }

    with pytest.raises(ConfigurationError) as error:
        Settings.from_env(env)

    assert "BOT_TOKEN" in str(error.value)
    assert "AAHrandomlookingsecretvalue" not in str(error.value)


def test_settings_cannot_hold_a_bot_token_field() -> None:
    names = {field.name for field in dataclasses.fields(Settings)}

    assert "bot_token" not in names
    assert not any("bot_token" in name for name in names)


def test_fallback_requires_its_derived_secret() -> None:
    with pytest.raises(ConfigurationError):
        Settings.from_env({**BASE_ENV, "AUTH_HMAC_FALLBACK_ENABLED": "true"})

    settings = Settings.from_env(
        {
            **BASE_ENV,
            "AUTH_HMAC_FALLBACK_ENABLED": "true",
            "WEBAPP_HMAC_SECRET": "bb" * 32,
        }
    )
    assert settings.webapp_hmac_secret == bytes.fromhex("bb" * 32)


def test_min_auth_date_is_parsed_as_an_instant() -> None:
    settings = Settings.from_env(
        {**BASE_ENV, "AUTH_MIN_AUTH_DATE": "2026-07-24T10:00:00Z"}
    )

    assert settings.min_auth_date == datetime(2026, 7, 24, 10, 0, tzinfo=UTC)


def test_min_auth_date_must_carry_a_timezone() -> None:
    with pytest.raises(ConfigurationError):
        Settings.from_env({**BASE_ENV, "AUTH_MIN_AUTH_DATE": "2026-07-24T10:00:00"})


@pytest.mark.parametrize(
    "override",
    [
        {"API_DATABASE_URL": ""},
        {"TELEGRAM_BOT_ID": ""},
        {"TELEGRAM_BOT_ID": "not-a-number"},
        {"TELEGRAM_BOT_ID": "0"},
        {"WEBAPP_URL": ""},
        {"APP_ENV": "staging"},
        {"TELEGRAM_ED25519_PUBLIC_KEY": "zz"},
        {"LOG_ACCOUNT_REF_KEY": "abcd"},
    ],
)
def test_invalid_values_fail_closed(override: dict[str, str]) -> None:
    with pytest.raises(ConfigurationError):
        Settings.from_env({**BASE_ENV, **override})


def test_log_key_is_generated_when_absent_so_refs_never_fall_back_to_raw_ids() -> None:
    first = Settings.from_env(BASE_ENV)
    second = Settings.from_env(BASE_ENV)

    assert len(first.log_account_ref_key) == 32
    assert first.log_account_ref_key != second.log_account_ref_key


def test_documented_environment_covers_every_setting() -> None:
    documented = {
        line.split("=", 1)[0]
        for line in (REPO_ROOT / ".env.example").read_text().splitlines()
        if line and not line.startswith("#")
    }

    assert set(SETTINGS_ENV_KEYS) <= documented
    assert "BOT_TOKEN" not in documented


def test_settings_are_immutable() -> None:
    settings = Settings.from_env(BASE_ENV)

    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.app_env = "development"  # type: ignore[misc]
