"""The status codes each operation can actually answer, declared per route.

Until Phase 5 every operation documented 200 and 422 and nothing else, while
answering 401, 403, 404, 409, 410, 413, 429 and 503 in practice. That is not a
documentation nicety: a schema that names two of nine codes cannot be tested
against, and the schemathesis job of §12 exists precisely to test against it.

Three rules govern what may appear here:

* **only codes the code can produce.** A declaration is a claim; an unreachable
  one is a false claim that makes the next reader trust the list less;
* **never 500.** A server error is a defect, and a defect must not become an
  expected status. `not_a_server_error` is the check that would stop mattering;
* **the body of a refusal is `{"error": "<ascii-code>"}`** and is declared as
  such, except for the two 409s that carry a value — `conflict_keys` and
  `current_wrap_version` — which §11 keeps out of exceptions and returns as
  values from the service.
"""

from __future__ import annotations

from typing import Any

from app.schemas.errors import ErrorResponse, ValidationRefusal
from app.schemas.sync import KeyWriteConflictOutput, PushConflictOutput

#: 400 is not a coded refusal, and declaring it as one was wrong.
#:
#: The body arrived undecodable — not merely not-JSON — so FastAPI answers before
#: any validator runs, with its own `{"detail": ...}` shape. The first pass of the
#: schemathesis run of §12 reported the missing declaration; the second reported
#: that the declaration was the wrong shape. Both were the document's fault.
_PARSE_FAILURE: dict[str, Any] = {
    "model": ValidationRefusal,
    "description": "The body could not be parsed at all. FastAPI answers before"
    " any validator runs; the shape is the same one constant string as a 422.",
}

#: Every refusal that answers a bare code, by status.
_CODED: dict[int, str] = {
    401: "The bearer token is missing, expired, replayed, or the initData behind"
    " it is stale (§8): `auth_invalid`, `auth_stale`, `auth_replayed`.",
    403: "The consent this endpoint requires is not active, the operation needs"
    " a step-up, or no account exists yet (§8, §11): `consent_required`,"
    " `step_up_required`, `no_account`.",
    404: "Nothing to read: `no_vault_key`, `no_schedule`.",
    409: "A precondition of §4.3 or §9.1 refuses the write.",
    410: "The cursor is below the compaction horizon; a full resync is required"
    " (§6.4, §9.4): `gone`.",
    413: "A record, a batch, or an envelope is over the §9.5 limit, or is not"
    " decodable at all: `payload_too_large`.",
    429: "A §11 per-account window is spent. `Retry-After` names the wait, and"
    " the client honours it: `rate_limited`.",
    503: "Outside `development` the server refuses to create a consent while the"
    " copy is unfrozen (Phase 1 blocker): `consent_copy_not_frozen`.",
}

#: The two refusals whose body carries a value rather than a code.
_VALUED: dict[str, Any] = {
    "push_conflict": {
        "model": PushConflictOutput,
        "description": "Per-key conflict under the lock, or a vault reset"
        ' (§9.1, §9.4). A reset answers `{"error": "vault_reset"}` instead.',
    },
    "wrap_conflict": {
        "model": KeyWriteConflictOutput,
        "description": "CAS on `wrap_version` failed (§7); the current value is"
        " returned so the client can re-read the envelope.",
    },
}

_SCHEMA_422 = (
    "The request does not match this schema. The body is one constant string:"
    " §11 forbids echoing the submitted value, and a Pydantic error carries it."
)

_DOMAIN_422 = (
    "Either the shape is wrong — one constant string, as §11 requires — or the"
    " value is well-formed and the domain refuses it: `quiet_hours_violation`"
    " (§10) and `unknown_timezone`, which answer a code. Two shapes on one"
    " status, and the union is declared because both occur: the fuzzer of §12"
    ' reported `{"error": "unknown_timezone"}` against a schema that'
    " promised only the first."
)


def refusals(
    *codes: int,
    valued: str | None = None,
    domain_422: bool = False,
) -> dict[int | str, dict[str, Any]]:
    """Build the `responses` mapping for one operation.

    422 is always included: FastAPI declares it for any operation with a
    parameter or a body, and the point of this module is that the declaration
    matches the sanitized handler rather than Pydantic's default shape.

    `domain_422` widens it to the union for the three operations that can also
    refuse a well-formed value from the domain — the tz database and the quiet
    hours of §10 live there, not in the schema.
    """
    declared: dict[int | str, dict[str, Any]] = {
        422: {
            "model": ValidationRefusal | ErrorResponse
            if domain_422
            else ValidationRefusal,
            "description": _DOMAIN_422 if domain_422 else _SCHEMA_422,
        }
    }
    for code in codes:
        if code == 400:
            declared[400] = dict(_PARSE_FAILURE)
            continue
        declared[code] = {
            "model": ErrorResponse,
            "description": _CODED[code],
        }
    if valued is not None:
        declared[409] = _VALUED[valued]
    return declared
