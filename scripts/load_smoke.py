#!/usr/bin/env python3
"""Навантажувальний smoke першого вивантаження (§9.5 проти §11).

DoD Фази 5 вимагає **числа**: обсяг, чанки, час, точку, де вмикається
`5 MiB/хв`, і поведінку клієнта в цій точці. «Пройшло» без чисел не є
результатом, тож цей скрипт друкує таблицю, а не «ok».

**Чому випадкові байти є чесним стендом для шифротексту.** Сервер
zero-knowledge: він ніколи не парсить payload (§6.1, §9.1) і бачить лише
довжину. Отже єдина властивість вмісту, що впливає на вимір, — це саме довжина,
і генерувати справжній AES-GCM тут було б витратою часу на властивість, якої
вимір не має.

**Чого цей скрипт НЕ робить.** Він не є частиною наскрізного gate: як і
`sync-e2e`, він потребує стенду. Він не ходить у жоден зовнішній сервіс — §6.5
робить будь-який новий носій повторним Gate D, і це стосується генераторів
навантаження так само, як моніторингу.

Запуск (стенд уже піднято `./scripts/dev-stand.sh`, api слухає :8000):
    uv run --locked python ../../scripts/load_smoke.py            # усі профілі
    uv run --locked python ../../scripts/load_smoke.py --profile heavy
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# §9.5 і §11 дослівно. Повторені тут, а не імпортовані: скрипт має лишатися
# запускним без встановленого пакета api, і саме ці числа він і перевіряє.
MAX_RECORDS_PER_CHUNK = 200
MAX_BYTES_PER_CHUNK = 1_048_576
MAX_RECORD_BYTES = 65_536
PUSH_BYTES_PER_MINUTE = 5 * 1024 * 1024
SYNC_REQUESTS_PER_MINUTE = 60

#: Стеля очікування — та сама, що в клієнті (`apps/web/src/sync/engine.ts`).
MAX_RETRY_AFTER_SECONDS = 60


@dataclass(frozen=True)
class Profile:
    name: str
    #: Записи `entry:<date>` — по одному на добу щоденника.
    entries: int
    #: Середній розмір шифротексту одного запису, байтів.
    record_bytes: int
    #: Синглтони §6.1: cycle, catalog, groups, settings, manifest.
    singletons: int = 5
    description: str = ""

    @property
    def records(self) -> int:
        return self.entries + self.singletons

    @property
    def total_bytes(self) -> int:
        return self.records * self.record_bytes


PROFILES: dict[str, Profile] = {
    "typical": Profile(
        name="typical",
        entries=1_095,
        record_bytes=1_024,
        description="три роки щоденних записів, ~1 КіБ шифротексту на запис",
    ),
    "verbose": Profile(
        name="verbose",
        entries=1_095,
        record_bytes=4_096,
        description="ті самі три роки, але з довгими нотатками — ~4 КіБ на запис",
    ),
    "heavy": Profile(
        name="heavy",
        entries=3_650,
        record_bytes=1_536,
        description="десять років щоденних записів, ~1.5 КіБ на запис",
    ),
}


@dataclass
class Attempt:
    chunk: int
    records: int
    payload_bytes: int
    status: int
    retry_after: int | None
    elapsed_ms: int


@dataclass
class Outcome:
    profile: Profile
    attempts: list[Attempt] = field(default_factory=list)
    waited_seconds: int = 0
    wall_seconds: float = 0.0
    final_revision: int = 0

    @property
    def chunks(self) -> int:
        return len({attempt.chunk for attempt in self.attempts})

    @property
    def refusals(self) -> list[Attempt]:
        return [attempt for attempt in self.attempts if attempt.status == 429]

    @property
    def accepted_bytes(self) -> int:
        return sum(a.payload_bytes for a in self.attempts if a.status == 200)


def plan_chunks(profile: Profile) -> list[int]:
    """Скільки записів у кожному чанку за межами §9.5.

    Той самий закон, що в `apps/web/src/sync/chunks.ts`: ≤200 записів **і**
    ≤1 МіБ; одне місце в чанку зарезервоване під manifest, який §9.5 вимагає
    оновлювати в КОЖНОМУ чанку.
    """
    per_chunk = min(
        MAX_RECORDS_PER_CHUNK - 1,
        max(1, MAX_BYTES_PER_CHUNK // profile.record_bytes - 1),
    )
    remaining = profile.records
    chunks: list[int] = []
    while remaining > 0:
        take = min(per_chunk, remaining)
        chunks.append(take)
        remaining -= take
    return chunks


def base64_of(byte_count: int) -> str:
    """Рівно `byte_count` випадкових байтів у base64."""
    import base64

    return base64.b64encode(secrets.token_bytes(byte_count)).decode()


def push(
    api_url: str,
    token: str,
    body: dict[str, object],
) -> tuple[int, dict, int | None, int]:
    request = urllib.request.Request(
        f"{api_url}/v1/sync/push",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            elapsed = int((time.perf_counter() - started) * 1000)
            return response.status, dict(json.loads(response.read())), None, elapsed
    except urllib.error.HTTPError as error:
        elapsed = int((time.perf_counter() - started) * 1000)
        named = error.headers.get("Retry-After")
        retry_after = int(named) if named and named.isdigit() else None
        return error.code, {}, retry_after, elapsed


def mint(api_url: str, bot_id: int, telegram_user_id: int) -> str:
    """Своя сесія на профіль: вікна §11 — per-account."""
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "mint_session.py"),
            "--api-url",
            api_url,
            "--bot-id",
            str(bot_id),
            "--telegram-user-id",
            str(telegram_user_id),
            "--grant",
            "health_sync",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"не вдалося мінтити сесію:\n{result.stderr}")
    return result.stdout.strip().splitlines()[-1]


def run(profile: Profile, api_url: str, bot_id: int, user_id: int) -> Outcome:
    token = mint(api_url, bot_id, user_id)
    outcome = Outcome(profile=profile)
    chunks = plan_chunks(profile)

    # Один і той самий шифротекст на всі записи чанка: сервер його не читає, а
    # генерувати 3650 різних рядків по 1.5 КіБ коштувало б хвилин на властивість,
    # якої вимір не має.
    payload = base64_of(profile.record_bytes)
    base_revision = 0
    started = time.perf_counter()
    key_index = 0

    for chunk_index, size in enumerate(chunks, start=1):
        changes = []
        for _ in range(size):
            key_index += 1
            changes.append(
                {
                    "record_key": f"{key_index:064x}",
                    "client_ts_ms": 1_768_435_200_000 + key_index,
                    "payload_b64": payload,
                    "tombstone": False,
                }
            )
        body = {"base_revision": base_revision, "changes": changes}
        volume = size * profile.record_bytes

        # Дві спроби на чанк — рівно стільки, скільки в клієнта (`retries = 2`).
        for attempt_index in range(2):
            status, response, retry_after, elapsed = push(api_url, token, body)
            outcome.attempts.append(
                Attempt(
                    chunk=chunk_index,
                    records=size,
                    payload_bytes=volume,
                    status=status,
                    retry_after=retry_after,
                    elapsed_ms=elapsed,
                )
            )
            if status == 200:
                base_revision = int(response["new_revision"])
                outcome.final_revision = base_revision
                break
            if status != 429 or attempt_index == 1:
                raise SystemExit(
                    f"чанк {chunk_index} відповів {status}; smoke зупинено"
                )
            wait = min(retry_after or 1, MAX_RETRY_AFTER_SECONDS)
            outcome.waited_seconds += wait
            time.sleep(wait)

    outcome.wall_seconds = time.perf_counter() - started
    return outcome


def report(outcome: Outcome) -> None:
    profile = outcome.profile
    chunks = plan_chunks(profile)
    refusals = outcome.refusals

    print(f"\n=== профіль {profile.name}: {profile.description} ===")
    print(f"  записів entry:            {profile.entries}")
    print(f"  синглтонів (§6.1)         {profile.singletons}")
    print(f"  усього записів            {profile.records}")
    print(f"  розмір запису             {profile.record_bytes} Б")
    print(
        f"  сумарний шифротекст       {profile.total_bytes} Б"
        f" ({profile.total_bytes / 1024 / 1024:.2f} МіБ)"
    )
    print(
        f"  чанків за §9.5            {len(chunks)}"
        f" (≤{MAX_RECORDS_PER_CHUNK} записів і ≤1 МіБ; фактично"
        f" {chunks[0]} записів у чанку)"
    )
    print(f"  запитів надіслано         {len(outcome.attempts)}")
    print(f"  прийнято сервером         {outcome.accepted_bytes} Б")
    print(f"  фінальна ревізія          {outcome.final_revision}")
    print(f"  відмов 429                {len(refusals)}")
    if refusals:
        first = refusals[0]
        before = sum(
            a.payload_bytes
            for a in outcome.attempts
            if a.status == 200 and a.chunk < first.chunk
        )
        print(
            f"  перша відмова             чанк {first.chunk} з {len(chunks)},"
            f" після {before} Б ({before / 1024 / 1024:.2f} МіБ) прийнятих"
        )
        print(
            f"  Retry-After               {sorted({a.retry_after for a in refusals})}"
        )
    print(f"  сумарне очікування        {outcome.waited_seconds} с")
    print(f"  час від першого до останнього запиту {outcome.wall_seconds:.1f} с")
    server_ms = sum(a.elapsed_ms for a in outcome.attempts)
    print(f"  з нього сервер            {server_ms / 1000:.1f} с")
    print(
        f"  межа §11                  push_bytes {PUSH_BYTES_PER_MINUTE} Б/хв,"
        f" sync {SYNC_REQUESTS_PER_MINUTE} запитів/хв"
    )
    if not refusals:
        print(
            "  висновок                  вивантаж не впирається в §11:"
            f" {profile.total_bytes} Б < {PUSH_BYTES_PER_MINUTE} Б/хв"
        )
    else:
        print(
            "  висновок                  вивантаж упирається в `5 MiB/хв`;"
            " клієнт чекає названу кількість секунд і завершує вивантаж"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://localhost:8000")
    # Свідомо НЕ читає `TELEGRAM_BOT_ID`, а бере `FUZZ_BOT_ID` — та сама пастка,
    # що в `scripts/fuzz-openapi.sh`: фікстурна пара §8 підписує
    # `<bot_id>:WebAppData\n…`, тож id мусить збігатися з тим, на який
    # налаштований api під тестом, а не з id реального бота з `.env`. Успадкування
    # довколишнього значення дає 401 `auth_invalid`, який виглядає як дефект коду.
    parser.add_argument(
        "--bot-id",
        type=int,
        default=int(os.environ.get("FUZZ_BOT_ID", "1234567890")),
    )
    parser.add_argument("--profile", choices=sorted(PROFILES), action="append")
    parser.add_argument("--first-user-id", type=int, default=800000000)
    arguments = parser.parse_args()

    chosen = [PROFILES[name] for name in (arguments.profile or sorted(PROFILES))]
    for index, profile in enumerate(chosen, start=1):
        outcome = run(
            profile,
            arguments.api_url,
            arguments.bot_id,
            arguments.first_user_id + index,
        )
        report(outcome)
    return 0


if __name__ == "__main__":
    sys.exit(main())
