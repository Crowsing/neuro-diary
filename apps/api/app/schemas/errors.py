"""What a refusal actually looks like on the wire.

Declared because it was not. FastAPI documents a 422 as `HTTPValidationError`
with `detail` an **array** of per-field errors, and §11 forbids exactly that:
the sanitized handler answers a single string, because a Pydantic error carries
the submitted value and the submitted value is medical content.

So the schema said one thing and the server did another, on every operation with
a body or a parameter. The fuzzer of Phase 5 found it on the first pass; nothing
else would have, because no client reads the schema.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ErrorResponse(ContractModel):
    """A stable ASCII code and nothing else (§10, §11).

    Never a message, never the submitted value, never the name of a consent —
    the set of consents an account holds is itself a health inference.
    """

    error: str


class ValidationRefusal(ContractModel):
    """The sanitized 422 body, replacing FastAPI's per-field array.

    One constant string. `request_validation_error_handler` builds it and drops
    everything Pydantic collected, including `input`.
    """

    detail: str
