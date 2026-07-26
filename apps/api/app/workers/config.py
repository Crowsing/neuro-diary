"""Configuration of the reminder worker process (§5.3, §11).

A second settings object rather than a flag on the first one, and the reason is
mechanical rather than stylistic: `app.infra.config.Settings` **refuses to
start** when `BOT_TOKEN` is visible, and a test forbids it from even having a
field whose name contains it. That guard is a property of the api process, so
the worker cannot borrow the class — and inverting the guard is the worker's
own fail-closed rule: without the token this process has nothing to do.

The second guard is the mirror image of the api's. The api refuses the token;
the worker refuses the wrong role. A DSN that authenticates as `api_rw` would
hand a process holding `BOT_TOKEN` full DML on `diary`, which is precisely the
arrangement §5.3 rejected when it declined to give the token to
`erasure_worker`. A connection string is easy to copy between `.env` files, so
this is checked rather than trusted.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.infra.config import ConfigurationError

#: Every variable `ReminderWorkerSettings.from_env` reads. `BOT_TOKEN` is here
#: and deliberately **not** in `.env.example`: §11 keeps secrets out of the
#: documented environment, and a test asserts its absence there.
WORKER_ENV_KEYS = (
    "REMINDER_WORKER_DATABASE_URL",
    "WEBAPP_URL",
    "BOT_TOKEN",
)

#: The only role this process may authenticate as (§6.3).
REQUIRED_ROLE = "reminder_worker"


@dataclass(frozen=True, slots=True)
class ReminderWorkerSettings:
    database_url: str
    webapp_url: str
    bot_token: str = field(repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ReminderWorkerSettings:
        database_url = _required(env, "REMINDER_WORKER_DATABASE_URL")
        _assert_role(database_url)
        token = _required(env, "BOT_TOKEN")
        return cls(
            database_url=database_url,
            webapp_url=_required(env, "WEBAPP_URL"),
            bot_token=token,
        )


def _required(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise ConfigurationError(f"{key} is required")
    return value


def _assert_role(database_url: str) -> None:
    """Refuse any DSN that is not the worker's own role.

    The message names the expected role and never the given one: a DSN carries
    a password, and an error message reaches the log (§11).
    """
    username = urlsplit(database_url).username
    if username != REQUIRED_ROLE:
        raise ConfigurationError(
            "REMINDER_WORKER_DATABASE_URL must authenticate as"
            f" {REQUIRED_ROLE}: this process holds BOT_TOKEN and §5.3 keeps it"
            " away from the diary schema"
        )
