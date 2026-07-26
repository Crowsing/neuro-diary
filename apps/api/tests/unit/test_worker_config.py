"""§5.3: the worker's configuration is the mirror image of the api's.

The api refuses to start when it can see `BOT_TOKEN`. This process refuses to
start without it — and refuses just as hard when its DSN authenticates as the
role that can read the diary, because that combination is exactly the one §5.3
rejected when it declined to hand the token to `erasure_worker`.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.infra.config import ConfigurationError, Settings
from app.workers.config import REQUIRED_ROLE, WORKER_ENV_KEYS, ReminderWorkerSettings

TOKEN = "1234567890:synthetic-value-generated-in-this-run"
BASE_ENV = {
    "REMINDER_WORKER_DATABASE_URL": (
        "postgresql://reminder_worker:secret@localhost:5432/neuro"
    ),
    "WEBAPP_URL": "http://localhost:4174",
    "BOT_TOKEN": TOKEN,
}


def test_the_worker_reads_its_own_three_variables() -> None:
    settings = ReminderWorkerSettings.from_env(BASE_ENV)

    assert settings.bot_token == TOKEN
    assert settings.webapp_url == "http://localhost:4174"
    assert set(WORKER_ENV_KEYS) == set(BASE_ENV)


@pytest.mark.parametrize("missing", sorted(BASE_ENV))
def test_every_variable_is_required(missing: str) -> None:
    env = {key: value for key, value in BASE_ENV.items() if key != missing}

    with pytest.raises(ConfigurationError) as error:
        ReminderWorkerSettings.from_env(env)

    assert missing in str(error.value)


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://api_rw:secret@localhost:5432/neuro",
        "postgresql://migrator:secret@localhost:5432/neuro",
        "postgresql://postgres:secret@localhost:5432/neuro",
        "postgresql://localhost:5432/neuro",
    ],
)
def test_a_dsn_for_any_other_role_is_refused(dsn: str) -> None:
    """A connection string is one copy-paste away from full DML on `diary`."""
    with pytest.raises(ConfigurationError) as error:
        ReminderWorkerSettings.from_env(
            {**BASE_ENV, "REMINDER_WORKER_DATABASE_URL": dsn}
        )

    assert REQUIRED_ROLE in str(error.value)
    assert "secret" not in str(error.value)


def test_the_worker_settings_never_become_the_api_settings() -> None:
    """The two guards point in opposite directions, and both have to hold.

    If the worker's environment were ever fed to `Settings.from_env`, the api
    guard would fire — which is the property that keeps a single misconfigured
    unit file from putting the token in the process that can read `diary`.
    """
    with pytest.raises(ConfigurationError) as error:
        Settings.from_env(
            {
                **BASE_ENV,
                "API_DATABASE_URL": "postgresql://api_rw@localhost:5432/neuro",
                "TELEGRAM_BOT_ID": "1234567890",
                "APP_ENV": "development",
            }
        )

    assert "BOT_TOKEN" in str(error.value)
    assert TOKEN not in str(error.value)


def test_the_token_never_appears_in_a_repr() -> None:
    """A settings object reaches a traceback more often than anyone plans for."""
    settings = ReminderWorkerSettings.from_env(BASE_ENV)

    assert TOKEN not in repr(settings)
    field = next(
        item
        for item in dataclasses.fields(ReminderWorkerSettings)
        if item.name == "bot_token"
    )
    assert field.repr is False
