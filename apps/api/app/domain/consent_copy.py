"""Version identity of the consent copy.

`<kind>@<major>.<minor>` (§4.3). A major of zero means the text still carries a
placeholder and has not been frozen with the controller's name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.identity import ConsentKind

_PATTERN = re.compile(r"^(?P<kind>[a-z_]+)@(?P<major>\d+)\.(?P<minor>\d+)$")


@dataclass(frozen=True, slots=True)
class TextVersion:
    kind: ConsentKind
    major: int
    minor: int

    @classmethod
    def parse(cls, value: str) -> TextVersion:
        match = _PATTERN.match(value)
        if match is None:
            raise ValueError("malformed text version")
        try:
            kind = ConsentKind(match["kind"])
        except ValueError as error:
            raise ValueError("unknown consent kind in text version") from error
        return cls(kind=kind, major=int(match["major"]), minor=int(match["minor"]))

    @property
    def is_frozen(self) -> bool:
        """A frozen text names the controller and can no longer change silently."""
        return self.major >= 1

    def __str__(self) -> str:
        return f"{self.kind.value}@{self.major}.{self.minor}"
