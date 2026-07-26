#!/usr/bin/env python3
"""Mint a session against a local stand, and grant the consents asked for.

The schemathesis job of Phase 5 needs a Bearer token before it can test
anything: §8 leaves `/health` and `POST /v1/auth/telegram` as the only endpoints
reachable without one, so an unauthenticated fuzz run would explore the 401 path
and nothing else.

The signing key is the deterministic Ed25519 pair of
`apps/api/tests/integration/conftest.py` — seed bytes 0..31. It is not a secret:
`.github/workflows/ci.yml` already carries its public half for the sync-e2e job,
and the private half is reproducible in three lines. It is *only* accepted by an
api configured with that public key, which no production process is.

Each run mints its own account when given its own `--telegram-user-id`: the
identity is 1:1 with the account (§4.3), so a distinct id is a distinct account.
That is what lets the destructive operations of §9.2 be fuzzed rather than
excluded — each gets a fresh account and destroys only its own.

Usage:
    python scripts/mint_session.py --grant health_sync --grant telegram_reminders
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

REPO_ROOT = Path(__file__).resolve().parents[1]
CONSENT_COPY = REPO_ROOT / "consent-copy"

SIGNING_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
COPY_VERSION = "0.9"

#: §9.2: `settings` is required exactly when the kind is `telegram_reminders`.
REMINDER_SETTINGS = {"time": "20:00", "timezone": "Europe/Kyiv"}


def public_key_hex() -> str:
    return SIGNING_KEY.public_key().public_bytes_raw().hex()


def sign_init_data(bot_id: int, telegram_user_id: int, nonce: str) -> str:
    """Build initData the third-party Ed25519 way of §8.

    `query_id` carries the nonce so two runs never present the same string: §8
    keeps a hash of every validated initData until its TTL expires, and a replay
    answers 401.
    """
    fields = {
        "auth_date": str(int(time.time())),
        "query_id": nonce,
        "user": json.dumps({"id": telegram_user_id}),
    }
    body = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    signature = SIGNING_KEY.sign(f"{bot_id}:WebAppData\n{body}".encode())
    fields["signature"] = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return "&".join(f"{key}={quote(value, safe='')}" for key, value in fields.items())


def consent_text_sha256(kind: str) -> str:
    """The hash of the text actually shown, as §9.2 requires at grant time."""
    path = CONSENT_COPY / "uk" / kind / f"{COPY_VERSION}.md"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def grant_body(kind: str) -> dict[str, object]:
    body: dict[str, object] = {
        "kind": kind,
        "text_version": f"{kind}@{COPY_VERSION}",
        "text_sha256": consent_text_sha256(kind),
    }
    if kind == "telegram_reminders":
        body["settings"] = REMINDER_SETTINGS
    return body


#: §11 puts the auth attempt limit at 10 a minute per address, and a sweep that
#: mints one account per operation walks straight into it. Honouring the header
#: rather than raising is the same decision Phase 5 made for the client itself.
MAX_WAITS = 4
FALLBACK_WAIT_SECONDS = 5


def post(
    url: str,
    payload: dict[str, object],
    token: str | None = None,
    *,
    tolerate: tuple[int, ...] = (),
) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}
        | ({"Authorization": f"Bearer {token}"} if token else {}),
        method="POST",
    )
    for attempt in range(MAX_WAITS + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, dict(json.loads(response.read()))
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")
            if error.code in tolerate:
                return error.code, {}
            if error.code != 429 or attempt == MAX_WAITS:
                raise SystemExit(f"{url} answered {error.code}: {detail}") from error
            named = error.headers.get("Retry-After")
            wait = int(named) if named and named.isdigit() else FALLBACK_WAIT_SECONDS
            print(f"429 from {url}; waiting {wait}s", file=sys.stderr)
            time.sleep(wait + 1)
    raise SystemExit(f"{url} kept refusing")  # pragma: no cover - loop returns


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--bot-id", type=int, default=1234567890)
    parser.add_argument("--telegram-user-id", type=int, default=4242424242)
    parser.add_argument(
        "--grant",
        action="append",
        default=[],
        choices=["health_sync", "telegram_reminders", "cycle_sync"],
        help="repeat for each consent; the first one creates the account (§8)",
    )
    parser.add_argument(
        "--print-public-key",
        action="store_true",
        help="print the public half of the fixture key and exit",
    )
    arguments = parser.parse_args()

    if arguments.print_public_key:
        print(public_key_hex())
        return 0

    grants: list[str] = arguments.grant or ["health_sync"]
    auth_url = f"{arguments.api_url}/v1/auth/telegram"

    def fresh_init_data() -> str:
        """A new string every call: §8 keeps a hash of each validated initData
        until its TTL expires, and presenting one twice answers 401."""
        nonce = f"mint-{arguments.telegram_user_id}-{time.time_ns()}"
        return sign_init_data(arguments.bot_id, arguments.telegram_user_id, nonce)

    # §8 branch three: no account and a grant present is one transaction. Branch
    # one is the re-run of this script against the same id — the account already
    # exists, the grant is already active, and the atomic form answers 409.
    status, body = post(
        auth_url,
        {"init_data": fresh_init_data(), "grant": grant_body(grants[0])},
        tolerate=(409,),
    )
    if status == 409:
        status, body = post(auth_url, {"init_data": fresh_init_data()})
    token = str(body["session_token"])

    for kind in grants[1:]:
        post(
            f"{arguments.api_url}/v1/consents",
            grant_body(kind),
            token=token,
            # Idempotent by intent: a second run of the sweep grants nothing new.
            tolerate=(409,),
        )

    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
