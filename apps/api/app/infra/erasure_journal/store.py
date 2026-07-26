"""The narrow object-storage port the erasure journal writes through (§6.4).

**There is no `delete`, and that is the point.** §6.4 says the writer holds
`PUT` and that no principal with `DELETE` exists; trimming at `J = 90` days is a
bucket lifecycle rule. Expressing that as a missing method rather than as a
comment means an "active trim from the application" cannot be written by
accident — there is nothing to call.

**Listing is explicitly paged.** A real `ListObjectsV2` returns a truncated page
with a continuation token, and an adapter that reads the first page and stops
silently under-reports the journal — the exact failure that would make a
verification pass over half a chain and call it intact. The cursor is in the
signature so that a test can hand out short pages and catch it.

The transport behind this port is not in this repository yet: no bucket was
available to verify a signer against, and shipping an unverified one would be a
claim the code cannot demonstrate. `InMemoryObjectStore` below is the reference
implementation of the contract and what the tests run against.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ObjectStoreError(RuntimeError):
    """The store could not be reached or refused the write.

    §6.4: a failed journal write stops the erasure. This is the exception that
    has to travel out of `record_intent` for that to happen.
    """


@dataclass(frozen=True, slots=True)
class ObjectPage:
    keys: tuple[str, ...]
    cursor: str | None


class ObjectStorePort(Protocol):
    def put(self, key: str, body: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...

    def list(self, prefix: str, *, cursor: str | None = None) -> ObjectPage: ...


class InMemoryObjectStore:
    """A store that behaves like the real one, including how it fails.

    It confirms a `PUT`, it has no `delete`, it can refuse, and it hands out
    short pages. Only the durability is fake.
    """

    def __init__(self, *, page_size: int = 1000) -> None:
        self._objects: dict[str, bytes] = {}
        self._page_size = page_size
        self.refusing = False
        self.losing_responses = False
        self.puts = 0

    def put(self, key: str, body: bytes) -> None:
        if self.refusing:
            raise ObjectStoreError("object store unavailable")
        self._objects[key] = body
        self.puts += 1
        if self.losing_responses:
            # The write landed and the answer did not — the failure mode that
            # makes a caller retry something that already happened.
            raise ObjectStoreError("response lost")

    def get(self, key: str) -> bytes:
        if self.refusing:
            raise ObjectStoreError("object store unavailable")
        try:
            return self._objects[key]
        except KeyError as error:
            raise ObjectStoreError(f"no such object: {key}") from error

    def list(self, prefix: str, *, cursor: str | None = None) -> ObjectPage:
        if self.refusing:
            raise ObjectStoreError("object store unavailable")
        keys = sorted(key for key in self._objects if key.startswith(prefix))
        start = keys.index(cursor) if cursor is not None and cursor in keys else 0
        page = keys[start : start + self._page_size]
        following = start + self._page_size
        return ObjectPage(
            keys=tuple(page),
            cursor=keys[following] if following < len(keys) else None,
        )
