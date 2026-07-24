"""Telegram Mini App initData validation (§8).

The algorithm is transcribed from core.telegram.org/bots/webapps, section
"Validating data received via the Mini App". That page documents both variants
and both public keys but publishes no worked example with real values, so the
vectors below are signed with a deterministic test key pair. The production key
constant is asserted against the documented hex separately.

Branch coverage of the validator must stay at 100% (phase 1 DoD).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.domain.identity import AuthInvalid, AuthStale
from app.infra.config import TELEGRAM_PRODUCTION_PUBLIC_KEY
from app.infra.telegram.initdata import (
    TELEGRAM_TEST_PUBLIC_KEY,
    InitDataValidator,
)

BOT_ID = 1234567890
USER_ID = 4242424242
NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
HMAC_SECRET = bytes(range(32))

_SIGNING_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
PUBLIC_KEY = _SIGNING_KEY.public_key().public_bytes_raw()


def _encode(fields: dict[str, str]) -> str:
    return "&".join(f"{key}={quote(value, safe='')}" for key, value in fields.items())


def _check_string(fields: dict[str, str]) -> bytes:
    pairs = sorted(
        (key, value)
        for key, value in fields.items()
        if key not in {"hash", "signature"}
    )
    body = "\n".join(f"{key}={value}" for key, value in pairs)
    return f"{BOT_ID}:WebAppData\n{body}".encode()


def make_init_data(
    *,
    auth_date: datetime = NOW,
    user: str | None = None,
    signature: str | None = None,
    extra: dict[str, str] | None = None,
    drop: set[str] | None = None,
) -> str:
    fields: dict[str, str] = {
        "auth_date": str(int(auth_date.timestamp())),
        "query_id": "AAHdF6IQAAAAAN0Xoh",
        "user": user if user is not None else json.dumps({"id": USER_ID}),
    }
    fields.update(extra or {})
    for key in drop or set():
        fields.pop(key, None)

    signed = _SIGNING_KEY.sign(_check_string(fields))
    fields["signature"] = (
        signature
        if signature is not None
        else base64.urlsafe_b64encode(signed).decode().rstrip("=")
    )
    return _encode(fields)


def make_hmac_init_data(*, auth_date: datetime = NOW, bad_hash: bool = False) -> str:
    fields = {
        "auth_date": str(int(auth_date.timestamp())),
        "user": json.dumps({"id": USER_ID}),
    }
    pairs = sorted(fields.items())
    check = "\n".join(f"{key}={value}" for key, value in pairs).encode()
    secret = hmac.new(b"WebAppData", HMAC_SECRET, hashlib.sha256).digest()
    digest = hmac.new(secret, check, hashlib.sha256).hexdigest()
    fields["hash"] = "0" * 64 if bad_hash else digest
    return _encode(fields)


def validator(**overrides: object) -> InitDataValidator:
    settings: dict[str, object] = {
        "bot_id": BOT_ID,
        "public_key": PUBLIC_KEY,
        "min_auth_date": None,
        "hmac_fallback_enabled": False,
        "hmac_secret": None,
    }
    settings.update(overrides)
    return InitDataValidator(**settings)  # type: ignore[arg-type]


def test_documented_public_keys() -> None:
    assert (
        TELEGRAM_PRODUCTION_PUBLIC_KEY.hex()
        == "e7bf03a2fa4602af4580703d88dda5bb59f32ed8b02a56c187fe7d34caed242d"
    )
    assert (
        TELEGRAM_TEST_PUBLIC_KEY.hex()
        == "40055058a4ee38156a06562e52eece92a771bcd8346a8c4615cb7376eddf72ec"
    )


def test_valid_init_data_yields_only_the_user_id_and_a_hash() -> None:
    raw = make_init_data()

    result = validator().validate(raw, now=NOW)

    assert result.telegram_user_id == USER_ID
    assert result.auth_date == NOW
    assert result.initdata_sha256 == hashlib.sha256(raw.encode()).digest()
    assert len(result.initdata_sha256) == 32
    assert not hasattr(result, "raw")
    assert not hasattr(result, "query_id")


def test_signature_padding_is_optional() -> None:
    signed = _SIGNING_KEY.sign(
        _check_string(
            {
                "auth_date": str(int(NOW.timestamp())),
                "user": json.dumps({"id": USER_ID}),
            }
        )
    )
    padded = base64.urlsafe_b64encode(signed).decode()
    assert padded.endswith("==")

    raw = _encode(
        {
            "auth_date": str(int(NOW.timestamp())),
            "user": json.dumps({"id": USER_ID}),
            "signature": padded,
        }
    )

    assert validator().validate(raw, now=NOW).telegram_user_id == USER_ID


def test_tampered_field_breaks_the_signature() -> None:
    raw = make_init_data().replace(str(USER_ID), "1111111111")

    with pytest.raises(AuthInvalid):
        validator().validate(raw, now=NOW)


def test_corrupt_signature_is_rejected() -> None:
    forged = base64.urlsafe_b64encode(b"\x00" * 64).decode().rstrip("=")

    with pytest.raises(AuthInvalid):
        validator().validate(make_init_data(signature=forged), now=NOW)


def test_non_base64url_signature_is_rejected() -> None:
    with pytest.raises(AuthInvalid):
        validator().validate(make_init_data(signature="not base64!"), now=NOW)


def test_signature_of_the_wrong_length_is_rejected() -> None:
    short = base64.urlsafe_b64encode(b"\x01\x02\x03").decode().rstrip("=")

    with pytest.raises(AuthInvalid):
        validator().validate(make_init_data(signature=short), now=NOW)


def test_missing_signature_is_rejected_while_the_fallback_is_off() -> None:
    raw = _encode(
        {"auth_date": str(int(NOW.timestamp())), "user": json.dumps({"id": USER_ID})}
    )

    with pytest.raises(AuthInvalid):
        validator().validate(raw, now=NOW)


def test_a_wrong_bot_id_breaks_the_check_string() -> None:
    with pytest.raises(AuthInvalid):
        validator(bot_id=999).validate(make_init_data(), now=NOW)


def test_empty_init_data_is_rejected() -> None:
    with pytest.raises(AuthInvalid):
        validator().validate("", now=NOW)


def test_malformed_query_is_rejected() -> None:
    with pytest.raises(AuthInvalid):
        validator().validate("this-is-not-a-query", now=NOW)


def test_duplicate_fields_are_rejected() -> None:
    raw = make_init_data()

    with pytest.raises(AuthInvalid):
        validator().validate(f"{raw}&auth_date=1", now=NOW)


def test_expired_auth_date_is_stale() -> None:
    raw = make_init_data(auth_date=NOW - timedelta(hours=24, seconds=1))

    with pytest.raises(AuthStale):
        validator().validate(raw, now=NOW)


def test_auth_date_at_the_ttl_boundary_still_passes() -> None:
    raw = make_init_data(auth_date=NOW - timedelta(hours=24))

    assert validator().validate(raw, now=NOW).telegram_user_id == USER_ID


def test_min_auth_date_rejects_previously_valid_init_data() -> None:
    """After a restore, `auth_replay` rolls back and old initData looks new."""
    raw = make_init_data(auth_date=NOW - timedelta(hours=2))
    restored_at = NOW - timedelta(hours=1)

    with pytest.raises(AuthStale):
        validator(min_auth_date=restored_at).validate(raw, now=NOW)


def test_min_auth_date_accepts_init_data_issued_after_the_restore() -> None:
    raw = make_init_data(auth_date=NOW - timedelta(minutes=30))
    restored_at = NOW - timedelta(hours=1)

    result = validator(min_auth_date=restored_at).validate(raw, now=NOW)

    assert result.telegram_user_id == USER_ID


def test_auth_date_far_in_the_future_is_invalid() -> None:
    raw = make_init_data(auth_date=NOW + timedelta(minutes=10))

    with pytest.raises(AuthInvalid):
        validator().validate(raw, now=NOW)


def test_small_clock_skew_is_tolerated() -> None:
    raw = make_init_data(auth_date=NOW + timedelta(seconds=30))

    assert validator().validate(raw, now=NOW).telegram_user_id == USER_ID


def test_missing_auth_date_is_invalid() -> None:
    with pytest.raises(AuthInvalid):
        validator().validate(make_init_data(drop={"auth_date"}), now=NOW)


def test_non_numeric_auth_date_is_invalid() -> None:
    raw = make_init_data(extra={"auth_date": "yesterday"})

    with pytest.raises(AuthInvalid):
        validator().validate(raw, now=NOW)


def test_missing_user_is_invalid() -> None:
    with pytest.raises(AuthInvalid):
        validator().validate(make_init_data(drop={"user"}), now=NOW)


def test_unparsable_user_is_invalid() -> None:
    with pytest.raises(AuthInvalid):
        validator().validate(make_init_data(user="{not json"), now=NOW)


def test_user_without_an_id_is_invalid() -> None:
    with pytest.raises(AuthInvalid):
        validator().validate(make_init_data(user=json.dumps({})), now=NOW)


def test_user_that_is_not_an_object_is_invalid() -> None:
    with pytest.raises(AuthInvalid):
        validator().validate(make_init_data(user=json.dumps([1, 2])), now=NOW)


@pytest.mark.parametrize("identifier", ["4242424242", 0, -1, True])
def test_user_id_must_be_a_positive_integer(identifier: object) -> None:
    raw = make_init_data(user=json.dumps({"id": identifier}))

    with pytest.raises(AuthInvalid):
        validator().validate(raw, now=NOW)


def test_hmac_fallback_accepts_init_data_without_a_signature() -> None:
    checker = validator(hmac_fallback_enabled=True, hmac_secret=HMAC_SECRET)

    result = checker.validate(make_hmac_init_data(), now=NOW)

    assert result.telegram_user_id == USER_ID


def test_hmac_fallback_rejects_a_wrong_hash() -> None:
    checker = validator(hmac_fallback_enabled=True, hmac_secret=HMAC_SECRET)

    with pytest.raises(AuthInvalid):
        checker.validate(make_hmac_init_data(bad_hash=True), now=NOW)


def test_hmac_fallback_rejects_init_data_without_a_hash() -> None:
    checker = validator(hmac_fallback_enabled=True, hmac_secret=HMAC_SECRET)
    raw = _encode(
        {"auth_date": str(int(NOW.timestamp())), "user": json.dumps({"id": USER_ID})}
    )

    with pytest.raises(AuthInvalid):
        checker.validate(raw, now=NOW)


def test_a_signature_is_still_preferred_when_the_fallback_is_enabled() -> None:
    checker = validator(hmac_fallback_enabled=True, hmac_secret=HMAC_SECRET)

    result = checker.validate(make_init_data(), now=NOW)

    assert result.telegram_user_id == USER_ID


def test_enabling_the_fallback_without_a_secret_is_a_programming_error() -> None:
    with pytest.raises(ValueError):
        validator(hmac_fallback_enabled=True, hmac_secret=None)
