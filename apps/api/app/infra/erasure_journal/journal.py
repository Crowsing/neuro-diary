"""The append-only erasure journal, written to object storage (§6.4, §6.5).

This is the adapter §6.5 calls non-optional: the journal has to live with a
different provider in a different country from the database and the backups,
because otherwise a single incident takes both the data and the proof that it
must be erased. `DatabaseErasureJournal` — still in `app.services`, because it
performs no I/O of its own — cannot be that store by construction.

It lives in `app.infra` for the same reason: it does I/O. It does not import
`ErasureJournalPort`; the protocol is structural and the composition root is
where mypy checks the fit. That keeps `app.infra` from importing `app.services`
and leaves the layering contract untouched.
"""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

from app.domain.erasure_journal import (
    GENESIS,
    ChainVerdict,
    JournalLine,
    StoredLine,
    ceil_to_hour,
    day_of,
    erasure_ref,
    head_key,
    journal_key,
    line_hash,
    sign_head,
    verify_chain,
    verify_head,
)
from app.infra.erasure_journal.store import ObjectStorePort
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class JournalAudit:
    """What the runbook asks the journal before it trusts it.

    Three questions, because they fail differently:

    * `chain` — is any line's predecessor gone, or was an object replaced;
    * `unsigned_days` — does the tail of each day carry a head this
      controller's key produced;
    * `missing_attested` — is there a signed head for a line that is no longer
      in the bucket. That is the one that catches a *truncation*: removing the
      last line of a day leaves the chain adding up, because nothing points at
      a tail.

    The residual limitation is named rather than papered over: an actor who
    removes both a tail line **and** its head leaves nothing here to notice.
    That is inherent to a journal with no external anchor, and it is why §6.4
    signs the daily head — a head the controller has seen out of band cannot be
    un-seen by deleting objects.
    """

    chain: ChainVerdict
    unsigned_days: tuple[str, ...]
    missing_attested: tuple[str, ...] = ()

    @property
    def trustworthy(self) -> bool:
        return (
            self.chain.intact and not self.unsigned_days and not self.missing_attested
        )


class ObjectStoreErasureJournal:
    def __init__(
        self,
        store: ObjectStorePort,
        *,
        erasure_key: bytes,
        head_key: bytes,
        prefix: str = "erasure",
    ) -> None:
        self._store = store
        self.erasure_key = erasure_key
        self.head_key = head_key
        self._prefix = prefix
        self._cached_tail: str | None = None

    # ---------------------------------------------------------------- writing

    def record_intent(self, *, account_id: UUID, code: str, at: datetime) -> UUID:
        """Append one line and the head that attests it. Raises, or it happened.

        Nothing is swallowed: §6.4 makes a failed journal write stop the
        erasure, and that only works if the store's error travels out of here.

        The returned reference is the first 128 bits of the line's own hash
        rather than an invented identifier, so it traces back to the object by
        prefix. It is deliberately not written *into* the line: §6.4 fixes four
        fields and this is not one of them, which also means the two journals
        of `TeeErasureJournal` carry no cross-reference — that composition
        returns the database row's identifier and discards this one. They are
        joined by `erasure_ref` and by time, which is what the runbook works
        with anyway.
        """
        line = JournalLine(
            erasure_ref=erasure_ref(self.erasure_key, account_id),
            at=ceil_to_hour(at),
            code=code,
            prev_hash=self._tail(),
        )
        body = line.to_json()
        digest = line_hash(body)
        day = day_of(line.at)

        self._store.put(journal_key(self._prefix, line=line, body=body), body)
        self._store.put(
            head_key(self._prefix, day=day, last_hash=digest),
            json.dumps(
                {
                    "day": day,
                    "last_hash": digest,
                    "signature": sign_head(self.head_key, day=day, last_hash=digest),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
        )
        self._cached_tail = digest
        return UUID(hex=digest[:32])

    def confirm(self, reference: UUID, *, at: datetime) -> None:
        """Deliberately nothing, and the docstring is the whole implementation.

        §6.4 fixes the line format at four fields; there is no completion field
        and appending a second `done` line would change a normative format. The
        composition in `TeeErasureJournal` keeps completion in
        `diary.erasure_job`, and that costs a restore nothing: reconciliation
        reads *this* journal, and each of its four actions is idempotent, so
        re-running a finished one is a no-op rather than a mistake.
        """
        del reference, at

    # ---------------------------------------------------------------- reading

    def read(self) -> list[StoredLine]:
        """Every line under the prefix, following the pagination to the end."""
        return [
            StoredLine(key=key, body=self._store.get(key))
            for key in self._keys()
            if "/head/" not in key
        ]

    def audit(self) -> JournalAudit:
        """Chain, signatures and attested-but-absent lines (see `JournalAudit`)."""
        lines = self.read()
        attested = self._attested_hashes()
        present = {line_hash(item.body) for item in lines}
        return JournalAudit(
            chain=verify_chain(lines),
            unsigned_days=self._unsigned_days(lines, attested),
            missing_attested=tuple(sorted(attested - present)),
        )

    # --------------------------------------------------------------- internals

    def _keys(self) -> list[str]:
        keys: list[str] = []
        cursor: str | None = None
        while True:
            page = self._store.list(self._prefix, cursor=cursor)
            keys.extend(page.keys)
            if page.cursor is None:
                return keys
            cursor = page.cursor

    def _attested_hashes(self) -> set[str]:
        """Line hashes carrying a head this controller's key actually signed."""
        attested: set[str] = set()
        for key in self._keys():
            if "/head/" not in key:
                continue
            payload = json.loads(self._store.get(key))
            day = str(payload.get("day"))
            last_hash = str(payload.get("last_hash"))
            if verify_head(
                self.head_key,
                day=day,
                last_hash=last_hash,
                signature=str(payload.get("signature")),
            ):
                attested.add(last_hash)
        return attested

    def _unsigned_days(
        self,
        lines: list[StoredLine],
        attested: set[str],
    ) -> tuple[str, ...]:
        by_day: dict[str, set[str]] = {}
        parents: set[str] = set()
        for item in lines:
            line = JournalLine.from_json(item.body)
            by_day.setdefault(day_of(line.at), set()).add(line_hash(item.body))
            parents.add(line.prev_hash)

        return tuple(
            day
            for day, digests in sorted(by_day.items())
            if not (digests - parents) <= attested
        )

    def _tail(self) -> str:
        """The line to point the next one at.

        Cached in memory after the first scan, and stale by design when another
        process appends in between: that is the fork `verify_chain` reports and
        the price of not coordinating writers through the database this journal
        exists to outlive.
        """
        if self._cached_tail is None:
            self._cached_tail = self._scan_tail()
        return self._cached_tail

    def _scan_tail(self) -> str:
        lines = self.read()
        if not lines:
            return GENESIS
        parsed = [
            (JournalLine.from_json(item.body), line_hash(item.body)) for item in lines
        ]
        parents = {line.prev_hash for line, _ in parsed}
        tails = [(line.at, digest) for line, digest in parsed if digest not in parents]
        if not tails:
            # Every line is somebody's predecessor: the journal is a cycle,
            # which cannot happen with hash links unless the store lied. Start a
            # fresh root rather than guess — `verify_chain` will show the seam.
            return GENESIS
        # Deterministic across processes, so two writers that both scan pick the
        # same tail and fork only when they genuinely raced.
        return max(tails)[1]
