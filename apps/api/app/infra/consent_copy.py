"""Reader for the shared consent copy registry.

The registry lives at the repository root so `apps/web` can show the same text
`apps/api` hashes. The digest is taken over the bytes of the file, which is the
one rule both a Python and a TypeScript reader reproduce identically.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.domain.identity import ConsentKind
from app.domain.records import ConsentText


class ConsentCopyUnavailable(RuntimeError):
    """The registry is missing an entry the running code depends on."""


class FileConsentCopyRegistry:
    def __init__(self, root: Path) -> None:
        self._root = root
        payload: dict[str, Any] = json.loads(
            (root / "registry.json").read_text(encoding="utf-8")
        )
        self._grants = {
            (entry["kind"], entry["locale"]): entry for entry in payload["grants"]
        }
        self._deletion = payload["deletion_copy"]

    def grant_text(self, kind: ConsentKind, *, locale: str) -> ConsentText:
        entry = self._grants.get((kind.value, locale))
        if entry is None:
            raise ConsentCopyUnavailable(f"no {kind.value} copy for {locale}")
        return ConsentText(
            kind=kind,
            text_version=entry["text_version"],
            locale=entry["locale"],
            sha256=bytes.fromhex(entry["sha256"]),
            body=(self._root / entry["file"]).read_text(encoding="utf-8"),
        )

    def deletion_copy_version(self) -> str:
        """The §6.4 promise in force right now, recorded on every erasure job."""
        version: str = self._deletion["text_version"]
        return version
