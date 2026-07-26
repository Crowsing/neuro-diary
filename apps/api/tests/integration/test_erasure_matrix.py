"""The erasure matrix is named by table, and the naming is checked (§6.4).

§6.4 asked for a matrix "named by each table, not given as a number" — and then
gave it as a number, which is how it came to say twelve while the migrations
create fourteen. A count cannot notice a new table; this can.

The matrix lives in `docs/restore-runbook.md` rather than here, so the document
the controller reads during an incident is the same one CI holds to. The price
is parsing a markdown table, and it is worth it: a copy in the test suite would
be a second source that drifts silently.
"""

from __future__ import annotations

import re

from sqlalchemy import Engine, text

from conftest import REPO_ROOT

RUNBOOK = REPO_ROOT / "docs" / "restore-runbook.md"

#: Rows of the matrix start with a backticked `schema.table` in the first cell.
ROW = re.compile(r"^\|\s*`(?P<table>[a-z_]+\.[a-z_]+)`\s*\|(?P<reason>.+)\|\s*$")

SCHEMAS = ("diary", "reminders")


def _matrix() -> dict[str, str]:
    text_body = RUNBOOK.read_text(encoding="utf-8")
    return {
        match.group("table"): match.group("reason").strip()
        for line in text_body.splitlines()
        if (match := ROW.match(line))
    }


def _tables(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT table_schema, table_name FROM information_schema.tables"
                " WHERE table_schema = ANY(:schemas) AND table_type = 'BASE TABLE'"
            ),
            {"schemas": list(SCHEMAS)},
        ).fetchall()
    return {f"{row.table_schema}.{row.table_name}" for row in rows}


def test_the_matrix_names_every_table_the_migrations_create(engine: Engine) -> None:
    """A migration that adds a table reddens CI until the matrix names it.

    Read as `api_rw`, which is also the point: a table this role cannot see is
    a table the erasure path could not clear even if it tried, and that is a
    finding rather than an omission.
    """
    matrix = _matrix()
    tables = _tables(engine)

    assert set(matrix) == tables
    assert len(tables) == 14


def test_every_row_says_what_closes_it(engine: Engine) -> None:
    """ "Named" means the row states a mechanism, not that it appears in a list."""
    del engine
    matrix = _matrix()

    mechanisms = ("каскад", "DELETE", "TTL", "reset_counters", "не видаляється")
    for table, reason in matrix.items():
        assert any(word in reason for word in mechanisms), table


def test_the_three_tables_without_a_cascade_are_called_out(engine: Engine) -> None:
    """The ones §6.4 singles out, plus the two this phase had to add.

    `outbox` and `auth_replay` were named by the plan. `erasure_job` was not,
    and it is the sharpest of them: "zero rows" is false for it by design,
    because it is the journal. `message_cleanup` was not named either — it has
    no foreign key, no writer yet, and `api_rw` holds INSERT only on it.
    """
    del engine
    matrix = _matrix()

    assert "payload" in matrix["diary.outbox"]
    assert "TTL 48" in matrix["diary.auth_replay"]
    assert "не видаляється свідомо" in matrix["diary.erasure_job"]
    assert "TTL 48" in matrix["reminders.message_cleanup"]


def test_the_runbook_keeps_the_operational_half_out_of_git() -> None:
    """§6.5: bucket names, hosts and accounts do not live in a public repo."""
    body = RUNBOOK.read_text(encoding="utf-8")

    for leak in ("s3://", "https://", "AKIA", "amazonaws", "hetzner", "nbg1"):
        assert leak.lower() not in body.lower(), leak


def test_the_runbook_states_the_step_that_is_easiest_to_forget() -> None:
    """§8: a restore rolls `auth_replay` back and used initData turns new again."""
    body = RUNBOOK.read_text(encoding="utf-8")

    assert "AUTH_MIN_AUTH_DATE" in body
    assert "at >= t_b" in body
    assert "ListObjectVersions" in body
