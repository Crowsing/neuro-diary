"""Telegram Mini App initData validation (§8).

Transcribed from core.telegram.org/bots/webapps, "Validating data received via
the Mini App". The Ed25519 variant is the chosen mechanism precisely because it
needs no bot token: api holds only the public key and the non-secret bot id.

Nothing here returns the raw initData. The caller receives the user id, the
issue time, and a SHA-256 digest for the replay table — initData itself is
neither logged nor persisted.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.domain.identity import INITDATA_MAX_AGE, AuthInvalid, AuthStale

# Documented alongside the production key; kept so a test-environment bot can be
# validated without inventing a value at deploy time.
TELEGRAM_TEST_PUBLIC_KEY = bytes.fromhex(
    "40055058a4ee38156a06562e52eece92a771bcd8346a8c4615cb7376eddf72ec"
)

_SIGNATURE_FIELD = "signature"
_HASH_FIELD = "hash"
_SIGNATURE_SIZE = 64
_CLOCK_SKEW = timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class ValidatedInitData:
    """Everything §8 permits api to keep out of an initData payload."""

    telegram_user_id: int
    auth_date: datetime
    initdata_sha256: bytes


class InitDataValidator:
    def __init__(
        self,
        *,
        bot_id: int,
        public_key: bytes,
        min_auth_date: datetime | None = None,
        hmac_fallback_enabled: bool = False,
        hmac_secret: bytes | None = None,
        max_age: timedelta = INITDATA_MAX_AGE,
    ) -> None:
        if hmac_fallback_enabled and hmac_secret is None:
            raise ValueError("the HMAC fallback needs its derived secret")
        self._bot_id = bot_id
        self._public_key = Ed25519PublicKey.from_public_bytes(public_key)
        self._min_auth_date = min_auth_date
        self._hmac_fallback_enabled = hmac_fallback_enabled
        self._hmac_secret = hmac_secret
        self._max_age = max_age

    def validate(self, init_data: str, *, now: datetime) -> ValidatedInitData:
        fields = _parse(init_data)

        signature = fields.get(_SIGNATURE_FIELD)
        if signature is not None:
            self._verify_signature(fields, signature)
        elif self._hmac_fallback_enabled:
            self._verify_hmac(fields)
        else:
            raise AuthInvalid()

        auth_date = self._auth_date(fields, now=now)
        return ValidatedInitData(
            telegram_user_id=_user_id(fields),
            auth_date=auth_date,
            initdata_sha256=hashlib.sha256(init_data.encode("utf-8")).digest(),
        )

    def _verify_signature(self, fields: dict[str, str], signature: str) -> None:
        try:
            raw = base64.urlsafe_b64decode(_pad(signature))
        except (binascii.Error, ValueError) as error:
            raise AuthInvalid() from error
        if len(raw) != _SIGNATURE_SIZE:
            raise AuthInvalid()

        message = f"{self._bot_id}:WebAppData\n{_check_string(fields)}".encode()
        try:
            self._public_key.verify(raw, message)
        except InvalidSignature as error:
            raise AuthInvalid() from error

    def _verify_hmac(self, fields: dict[str, str]) -> None:
        provided = fields.get(_HASH_FIELD)
        if provided is None:
            raise AuthInvalid()
        assert self._hmac_secret is not None  # guarded in __init__
        secret = hmac.new(b"WebAppData", self._hmac_secret, hashlib.sha256).digest()
        expected = hmac.new(
            secret,
            _check_string(fields).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, provided):
            raise AuthInvalid()

    def _auth_date(self, fields: dict[str, str], *, now: datetime) -> datetime:
        raw = fields.get("auth_date")
        if raw is None:
            raise AuthInvalid()
        try:
            issued = datetime.fromtimestamp(int(raw), UTC)
        except (ValueError, OverflowError, OSError) as error:
            raise AuthInvalid() from error

        if issued > now + _CLOCK_SKEW:
            raise AuthInvalid()
        if now - issued > self._max_age:
            raise AuthStale()
        # Restores roll `auth_replay` back, so previously used initData becomes
        # "new" again; this flag cuts everything issued before the restore.
        if self._min_auth_date is not None and issued < self._min_auth_date:
            raise AuthStale()
        return issued


def _parse(init_data: str) -> dict[str, str]:
    if not init_data:
        raise AuthInvalid()
    try:
        pairs = parse_qsl(
            init_data,
            keep_blank_values=True,
            strict_parsing=True,
        )
    except ValueError as error:
        raise AuthInvalid() from error

    fields: dict[str, str] = {}
    for key, value in pairs:
        if key in fields:
            # A repeated key makes the check string ambiguous, which is exactly
            # the shape a signature-stripping attempt would take.
            raise AuthInvalid()
        fields[key] = value
    return fields


def _check_string(fields: dict[str, str]) -> str:
    signed = sorted(
        (key, value)
        for key, value in fields.items()
        if key not in {_HASH_FIELD, _SIGNATURE_FIELD}
    )
    return "\n".join(f"{key}={value}" for key, value in signed)


def _pad(value: str) -> str:
    return value + "=" * (-len(value) % 4)


def _user_id(fields: dict[str, str]) -> int:
    raw = fields.get("user")
    if raw is None:
        raise AuthInvalid()
    try:
        user = json.loads(raw)
    except json.JSONDecodeError as error:
        raise AuthInvalid() from error
    if not isinstance(user, dict):
        raise AuthInvalid()

    identifier = user.get("id")
    # §8 takes the id and nothing else: no name, username, or photo.
    if not isinstance(identifier, int) or isinstance(identifier, bool):
        raise AuthInvalid()
    if identifier <= 0:
        raise AuthInvalid()
    return identifier
