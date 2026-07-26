"""The journal adapter against a store that behaves like the real one (§6.4).

Keys are generated inside the run: gitleaks scans the whole history, so a
64-character hex literal in a file would keep CI red long after it left HEAD.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.erasure_journal import (
    GENESIS,
    JournalLine,
    erasure_ref,
    line_hash,
)
from app.infra.erasure_journal.journal import ObjectStoreErasureJournal
from app.infra.erasure_journal.store import (
    InMemoryObjectStore,
    ObjectStoreError,
    ObjectStorePort,
)

MOMENT = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
PREFIX = "erasure"


@pytest.fixture
def store() -> InMemoryObjectStore:
    return InMemoryObjectStore()


@pytest.fixture
def keys() -> tuple[bytes, bytes]:
    return (secrets.token_bytes(32), secrets.token_bytes(32))


@pytest.fixture
def journal(
    store: InMemoryObjectStore,
    keys: tuple[bytes, bytes],
) -> ObjectStoreErasureJournal:
    return ObjectStoreErasureJournal(
        store,
        erasure_key=keys[0],
        head_key=keys[1],
        prefix=PREFIX,
    )


def _lines(journal: ObjectStoreErasureJournal) -> list[JournalLine]:
    return [JournalLine.from_json(item.body) for item in journal.read()]


# --------------------------------------------------------------------- the port


def test_the_store_port_has_no_delete() -> None:
    """§6.4: no principal with DELETE exists, expressed as a missing method.

    Trimming is a bucket lifecycle rule at `J = 90` days. An active trim from
    the application contradicts append-only, and the way to keep it unwritable
    is to leave nothing to call.
    """
    assert not hasattr(ObjectStorePort, "delete")
    assert not hasattr(InMemoryObjectStore, "delete")
    assert not hasattr(ObjectStoreErasureJournal, "delete")


# ------------------------------------------------------------------- appending


def test_a_recorded_intent_lands_as_one_line_and_one_signed_head(
    journal: ObjectStoreErasureJournal,
    store: InMemoryObjectStore,
    keys: tuple[bytes, bytes],
) -> None:
    account = uuid4()

    journal.record_intent(account_id=account, code="full", at=MOMENT)

    lines = _lines(journal)
    assert len(lines) == 1
    assert lines[0].erasure_ref == erasure_ref(keys[0], account)
    assert lines[0].code == "full"
    assert lines[0].prev_hash == GENESIS
    heads = [key for key in store.list(PREFIX).keys if "/head/" in key]
    assert len(heads) == 1
    head = json.loads(store.get(heads[0]))
    assert head["day"] == "2026-07-24"
    assert journal.audit().unsigned_days == ()


def test_the_time_is_rounded_up_before_it_is_written(
    journal: ObjectStoreErasureJournal,
) -> None:
    journal.record_intent(
        account_id=uuid4(),
        code="full",
        at=datetime(2026, 7, 24, 12, 34, 56, tzinfo=UTC),
    )

    assert _lines(journal)[0].at == datetime(2026, 7, 24, 13, 0, tzinfo=UTC)


def test_the_second_line_points_at_the_first(
    journal: ObjectStoreErasureJournal,
) -> None:
    journal.record_intent(account_id=uuid4(), code="full", at=MOMENT)
    first = journal.read()[0]

    journal.record_intent(
        account_id=uuid4(),
        code="sync_off",
        at=MOMENT + timedelta(hours=1),
    )

    chain = {line_hash(item.body): item for item in journal.read()}
    successor = next(
        JournalLine.from_json(item.body)
        for digest, item in chain.items()
        if digest != line_hash(first.body)
    )
    assert successor.prev_hash == line_hash(first.body)
    assert journal.audit().chain.intact


def test_a_retry_after_a_lost_response_does_not_fork_the_chain(
    journal: ObjectStoreErasureJournal,
    store: InMemoryObjectStore,
) -> None:
    """§6.4 allows retries within 24 hours, so retries have to be harmless.

    The write landed, the answer did not, and the caller tries again. Content
    addressing makes the second attempt the same bytes under the same key, so
    the journal ends up with one line rather than two siblings.
    """
    account = uuid4()
    store.losing_responses = True
    with pytest.raises(ObjectStoreError):
        journal.record_intent(account_id=account, code="full", at=MOMENT)
    store.losing_responses = False

    journal.record_intent(account_id=account, code="full", at=MOMENT)

    assert len(journal.read()) == 1
    assert journal.audit().trustworthy


def test_a_refusing_store_propagates_rather_than_swallowing(
    journal: ObjectStoreErasureJournal,
    store: InMemoryObjectStore,
) -> None:
    """§6.4: a failed journal write has to stop the erasure, so it must raise."""
    store.refusing = True

    with pytest.raises(ObjectStoreError):
        journal.record_intent(account_id=uuid4(), code="full", at=MOMENT)


def test_confirm_writes_nothing_to_the_store(
    journal: ObjectStoreErasureJournal,
    store: InMemoryObjectStore,
) -> None:
    """The line format of §6.4 has four fields and no completion.

    Appending a second `done` line would change a normative format, so the
    external journal records intent only. Completion lives in
    `diary.erasure_job`, and a restore does not miss it: reconciliation reads
    the external journal and replays finished and unfinished entries alike,
    which changes no data either way.
    """
    reference = journal.record_intent(account_id=uuid4(), code="full", at=MOMENT)
    before = store.puts

    journal.confirm(reference, at=MOMENT)

    assert store.puts == before


# --------------------------------------------------------------------- reading


def test_the_reader_follows_the_pagination_cursor(
    keys: tuple[bytes, bytes],
) -> None:
    """A reader that stops at the first page verifies half a chain and says
    `intact` — the failure this port's explicit cursor exists to prevent."""
    store = InMemoryObjectStore(page_size=2)
    journal = ObjectStoreErasureJournal(
        store,
        erasure_key=keys[0],
        head_key=keys[1],
        prefix=PREFIX,
    )
    for index in range(5):
        journal.record_intent(
            account_id=uuid4(),
            code="full",
            at=MOMENT + timedelta(hours=index),
        )

    assert len(journal.read()) == 5
    assert journal.audit().chain.intact


def test_head_objects_are_not_mistaken_for_lines(
    journal: ObjectStoreErasureJournal,
) -> None:
    journal.record_intent(account_id=uuid4(), code="full", at=MOMENT)

    assert len(journal.read()) == 1


# ----------------------------------------------------------------------- audit


def test_the_audit_names_a_removed_middle_line(
    journal: ObjectStoreErasureJournal,
    store: InMemoryObjectStore,
) -> None:
    """A line somebody points at cannot vanish quietly — that is `prev_hash`."""
    for index in range(3):
        journal.record_intent(
            account_id=uuid4(),
            code="full",
            at=MOMENT + timedelta(hours=index),
        )
    lines = journal.read()
    parents = {JournalLine.from_json(item.body).prev_hash for item in lines}
    middle = next(item for item in lines if line_hash(item.body) in parents)
    # Nothing in the application can do this — there is no DELETE. A break is
    # what the controller sees if the bucket policy ever stops being true.
    del store._objects[middle.key]  # noqa: SLF001

    audit = journal.audit()

    assert not audit.trustworthy
    assert not audit.chain.intact
    assert audit.chain.breaks[0].reason == "missing_predecessor"


def test_the_audit_names_a_truncated_tail(
    journal: ObjectStoreErasureJournal,
    store: InMemoryObjectStore,
) -> None:
    """Removing the last line leaves the chain adding up — the head does not.

    Nothing points at a tail, so `prev_hash` alone cannot notice it is gone.
    The signed head that attests it is what turns a silent truncation into a
    named finding.
    """
    for index in range(3):
        journal.record_intent(
            account_id=uuid4(),
            code="full",
            at=MOMENT + timedelta(hours=index),
        )
    lines = journal.read()
    parents = {JournalLine.from_json(item.body).prev_hash for item in lines}
    tail = next(item for item in lines if line_hash(item.body) not in parents)
    tail_hash = line_hash(tail.body)
    del store._objects[tail.key]  # noqa: SLF001

    audit = journal.audit()

    # The chain is silent about it, and that is exactly the point.
    assert audit.chain.intact
    assert audit.missing_attested == (tail_hash,)
    assert not audit.trustworthy


def test_the_audit_names_a_day_whose_head_was_forged(
    journal: ObjectStoreErasureJournal,
    store: InMemoryObjectStore,
) -> None:
    journal.record_intent(account_id=uuid4(), code="full", at=MOMENT)
    head = next(key for key in store.list(PREFIX).keys if "/head/" in key)
    body = json.loads(store.get(head))
    body["signature"] = "00" * 32
    store.put(head, json.dumps(body).encode())

    audit = journal.audit()

    assert audit.unsigned_days == ("2026-07-24",)
    # The chain itself is untouched: the two checks answer different questions.
    assert audit.chain.intact


def test_the_head_of_a_day_that_is_not_the_last_one_is_still_checked(
    journal: ObjectStoreErasureJournal,
    store: InMemoryObjectStore,
) -> None:
    """A day's tail is a line no line *of that day* points at.

    Taking the parent set across the whole journal instead would exempt every
    day but the last: the final line of a day is the predecessor of the next
    day's first line, so it would never look like a tail and its head would
    never be checked. Two days, first day's head deleted — it has to be named.
    """
    journal.record_intent(account_id=uuid4(), code="full", at=MOMENT)
    journal.record_intent(
        account_id=uuid4(), code="sync_off", at=MOMENT + timedelta(days=1)
    )
    first_day_tail = min(
        (JournalLine.from_json(item.body).at, line_hash(item.body))
        for item in journal.read()
    )[1]
    del store._objects[  # noqa: SLF001
        next(
            key
            for key in store.list(PREFIX).keys
            if key.endswith(f"{first_day_tail}.json") and "/head/" in key
        )
    ]

    audit = journal.audit()

    assert audit.unsigned_days == ("2026-07-24",)
    assert not audit.trustworthy
    # The chain is untouched — this is the head check doing its own job.
    assert audit.chain.intact


def test_two_writers_on_one_tail_fork_rather_than_fail(
    store: InMemoryObjectStore,
    keys: tuple[bytes, bytes],
) -> None:
    """`OutboxDispatcher` and the synchronous revoke path can append at once.

    Each holds its own view of the tail, so both write against the same
    predecessor. Nothing is lost and nothing raises — refusing the loser would
    turn a race into a refusal to erase.
    """
    first = ObjectStoreErasureJournal(
        store, erasure_key=keys[0], head_key=keys[1], prefix=PREFIX
    )
    second = ObjectStoreErasureJournal(
        store, erasure_key=keys[0], head_key=keys[1], prefix=PREFIX
    )
    # `first` writes and remembers its own tail; `second` starts cold, reads the
    # same tail out of the store, and appends after it.
    first.record_intent(account_id=uuid4(), code="full", at=MOMENT)
    tail = line_hash(first.read()[0].body)
    second.record_intent(
        account_id=uuid4(), code="sync_off", at=MOMENT + timedelta(hours=1)
    )

    # `first` never learned about that line, so its next append lands beside it.
    first.record_intent(
        account_id=uuid4(), code="reminders_off", at=MOMENT + timedelta(hours=2)
    )

    audit = first.audit()
    assert len(first.read()) == 3
    assert audit.chain.intact
    assert audit.chain.forked
    assert audit.chain.forks[0].prev_hash == tail
    assert len(audit.chain.forks[0].keys) == 2
