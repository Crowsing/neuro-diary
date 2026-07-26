"""Кожна знахідка фаззера §12 — власним цільовим тестом.

Прогін schemathesis із випадковим seed **не є** регресійним тестом: він знаходить
дефект один раз, а потім наступний seed шукає деінде. Тому кожна знахідка, яку
Фаза 5 виправила, має тут тест, який червоніє від зняття саме того виправлення.

Тести схеми (оголошені статуси, форма 422, патерни, межі) живуть у
`tests/unit/test_schema_declarations.py` — там вони не потребують бази. Тут —
поведінка проти реальної PostgreSQL.
"""

from __future__ import annotations

import base64
import hashlib

import pytest
from sqlalchemy import Engine

from app.domain.vault import MAX_COUNTER
from app.main import AUTH_ATTEMPTS_PER_MINUTE

from conftest import REPO_ROOT, Caller, sign_init_data

REGISTRY = REPO_ROOT / "consent-copy"
HEALTH_SYNC_SHA = hashlib.sha256(
    (REGISTRY / "uk/health_sync/0.9.md").read_bytes()
).hexdigest()


@pytest.fixture
def session(caller: Caller, engine: Engine) -> Caller:
    del engine
    response = caller.post(
        "/v1/auth/telegram",
        {
            "init_data": sign_init_data(),
            "grant": {
                "kind": "health_sync",
                "text_version": "health_sync@0.9",
                "text_sha256": HEALTH_SYNC_SHA,
            },
        },
    )
    assert response.status_code == 200, response.text
    caller.token = str(response.json()["session_token"])
    return caller


# ------------------------------------------------------- 500 на курсорі понад bigint


def test_a_cursor_past_the_ceiling_is_refused_and_not_a_server_error(
    session: Caller,
) -> None:
    """Знайдено фаззером: `?since=3138421778880289832960` → **500**.

    Величезне ціле доходило до PostgreSQL як параметр поза діапазоном, драйвер
    кидав, і санітизований обробник відповідав 500. 500 — це дефект, і саме тому
    він не оголошений очікуваним статусом ніде в схемі.
    """
    refused = session.get(f"/v1/sync/pull?since={MAX_COUNTER + 1}")

    assert refused.status_code == 422
    assert refused.json() == {"detail": "Request validation failed"}


def test_the_cursor_at_the_ceiling_is_still_accepted(session: Caller) -> None:
    """Межа — це межа, а не «трохи менше»: без цього боку тест пройшов би
    і на схемі, що відхиляє геть усе."""
    accepted = session.get(f"/v1/sync/pull?since={MAX_COUNTER}")

    # `since` вище за компакшн-горизонт — це не 410: горизонт нульовий на
    # порожньому сейфі, тож сторінка просто порожня.
    assert accepted.status_code == 200, accepted.text


def test_a_base_revision_past_the_ceiling_is_refused(session: Caller) -> None:
    refused = session.post(
        "/v1/sync/push",
        {
            "base_revision": MAX_COUNTER + 1,
            "changes": [
                {
                    "record_key": "aa" * 32,
                    "client_ts_ms": 1_768_435_200_000,
                    "payload_b64": base64.b64encode(b"x").decode(),
                    "tombstone": False,
                }
            ],
        },
    )

    assert refused.status_code == 422


def test_a_client_timestamp_past_the_ceiling_is_refused(session: Caller) -> None:
    refused = session.post(
        "/v1/sync/push",
        {
            "base_revision": 0,
            "changes": [
                {
                    "record_key": "aa" * 32,
                    "client_ts_ms": MAX_COUNTER + 1,
                    "payload_b64": base64.b64encode(b"x").decode(),
                    "tombstone": False,
                }
            ],
        },
    )

    assert refused.status_code == 422


# -------------------------------------------- невідомий query-параметр у request line


def test_an_unknown_query_parameter_is_refused_rather_than_ignored(
    session: Caller,
) -> None:
    """Знайдено фаззером: `?x-schemathesis-unknown-property=42` приймався з 200.

    Ігнорувати параметр — не те саме, що відмовити: request line на той момент
    уже написана, і саме вона потрапляє в лог reverse proxy. §2 тримає медичні
    значення поза URL, і до Фази 5 це правило виконував лише клієнт.
    """
    refused = session.get(
        "/v1/sync/pull?since=0&note=%D0%B3%D0%BE%D0%BB%D0%BE%D0%B2%D0%B0"
    )

    assert refused.status_code == 422
    assert refused.json() == {"detail": "Request validation failed"}


def test_the_three_cursor_parameters_are_still_accepted(session: Caller) -> None:
    """Зворотний бік: без нього відмова могла б стосуватися всіх параметрів."""
    accepted = session.get("/v1/sync/pull?since=0&limit=10&consent_epoch=0")

    assert accepted.status_code == 200, accepted.text


def test_a_query_parameter_on_a_path_that_takes_none_is_refused(
    session: Caller,
) -> None:
    """§9.2 дозволяє курсор рівно одному шляху, а не всім."""
    refused = session.get("/v1/consents?since=0")

    assert refused.status_code == 422


def test_the_refusal_never_echoes_the_parameter_name(session: Caller) -> None:
    """§11: назва параметра — це надісланe значення, і в тіло воно не потрапляє."""
    refused = session.get("/v1/sync/pull?migraine_on=2026-01-15")

    assert "migraine" not in refused.text
    assert "2026-01-15" not in refused.text


# ------------------------------------------------------------ форма 422 від домену


def test_a_domain_refusal_on_a_well_formed_value_answers_a_code(
    session: Caller,
) -> None:
    """Дві форми на одному статусі, і схема тепер оголошує обидві.

    `Europe/Atlantis` добре сформована як рядок і невідома базі зон, тож §10
    відповідає кодом, а не санітизованим рядком валідації. Фаззер повідомив це
    як «відповідь порушує схему» — і мав рацію щодо схеми, не щодо коду.
    """
    grant = session.post(
        "/v1/consents",
        {
            "kind": "telegram_reminders",
            "text_version": "telegram_reminders@0.9",
            "text_sha256": hashlib.sha256(
                (REGISTRY / "uk/telegram_reminders/0.9.md").read_bytes()
            ).hexdigest(),
            "settings": {"time": "20:00", "timezone": "Europe/Atlantis"},
        },
    )

    assert grant.status_code == 422
    assert grant.json() == {"error": "unknown_timezone"}


def test_a_shape_refusal_on_the_same_endpoint_answers_the_sanitized_string(
    session: Caller,
) -> None:
    """Той самий статус, інша форма — і саме тому оголошено union."""
    refused = session.post(
        "/v1/consents",
        {
            "kind": "telegram_reminders",
            "text_version": "telegram_reminders@0.9",
            "text_sha256": "not-a-digest",
            "settings": {"time": "20:00", "timezone": "Europe/Kyiv"},
        },
    )

    assert refused.status_code == 422
    assert refused.json() == {"detail": "Request validation failed"}


# ---------------------------------------------------------------- тіло, що не JSON


def test_a_body_that_is_not_json_answers_the_declared_four_hundred(
    session: Caller,
) -> None:
    """Знайдено фаззером: 400 не був оголошений у схемі жодної операції.

    Байти саме такі, і це не випадковість: FastAPI розрізняє два випадки. Тіло,
    яке декодується як UTF-8 але не є JSON, дає `RequestValidationError` і наш
    санітизований 422; тіло, яке не декодується взагалі, дає `HTTPException(400)`
    **до** будь-якого валідатора, з власною формою тіла. Фаззер генерував саме
    другий випадок, тож саме він і оголошений окремо.
    """
    response = session.request(
        "POST",
        "/v1/sync/push",
        json_body=None,
        raw_body=b"\xff\xfe\x00 not json at all",
    )

    assert response.status_code == 400
    body = response.json()
    assert set(body) == {"detail"}
    # §11: нічого з надісланого не повертається.
    assert "not json" not in response.text


def test_a_body_that_decodes_but_is_not_json_answers_the_sanitized_four_two_two(
    session: Caller,
) -> None:
    """Друга гілка тієї самої розвилки — і без неї попередній тест не мав би
    сенсу: він стверджував би, що будь-яке зламане тіло дає 400."""
    response = session.request(
        "POST",
        "/v1/sync/push",
        json_body=None,
        raw_body=b"not json at all",
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Request validation failed"}


# ------------------------------------------- порядок middleware у реальному app


def test_an_unreadable_query_never_spends_the_authentication_window(
    caller: Caller,
    engine: Engine,
) -> None:
    """Порядок реєстрації в `create_app`, а не механізм у зібраному вручну стеку.

    Starlette виконує middleware у зворотному порядку реєстрації, тож твердження
    «перевірка query стоїть ВИЩЕ за лічильник» — це рівно один рядок
    `add_middleware` від того, щоб стати хибним. Юніт-тест механізму цього не
    побачить: він будує власний стек. Тут — справжній застосунок.

    Властивість, яка захищається: запит із параметром, якого цей API не читає, не
    є спробою автентифікації, і його відмова не має коштувати нікому слота
    per-IP вікна §11.
    """
    del engine
    for _ in range(AUTH_ATTEMPTS_PER_MINUTE + 5):
        refused = caller.post(
            "/v1/auth/telegram?note=%D0%B3%D0%BE%D0%BB%D0%BE%D0%B2%D0%B0",
            {"init_data": "irrelevant"},
        )
        assert refused.status_code == 422

    # Вікно ціле: наступна чиста спроба доходить до валідатора, а не до лічильника.
    clean = caller.post("/v1/auth/telegram", {"init_data": "not-signed"})
    assert clean.status_code == 401
    assert clean.json() == {"error": "auth_invalid"}


# ------------------------------------------- цілі числа: строгість проти JSON Schema


def test_a_float_is_refused_where_the_contract_says_integer(session: Caller) -> None:
    """Знайдено фаззером, і виправляти тут нічого — це строгість, а не дефект.

    JSON Schema вважає `1.0` валідним `integer` («число без дробової частини»),
    а `strict=True` у `ContractModel` відхиляє float там, де оголошено ціле. Ця
    строгість свідома: та сама перевірка відхиляє `"5"`, `true` і `null` — тобто
    справжні клієнтські помилки, — а JSON Schema не має способу сказати «саме
    цілочисловий токен».

    Тест існує, щоб поведінка була запінена, а не випадкова: `schemathesis.toml`
    називає це хибним спрацюванням і посилається сюди.
    """
    refused = session.post(
        "/v1/consents/revoke",
        {"kind": "health_sync", "last_acked_revision": 1.0},
    )

    assert refused.status_code == 422
    assert refused.json() == {"detail": "Request validation failed"}


def test_the_same_field_accepts_the_integer(session: Caller) -> None:
    """Зворотний бік: без нього попередній тест пройшов би і на полі, яке
    відхиляє геть усе."""
    accepted = session.post(
        "/v1/consents/revoke",
        {"kind": "health_sync", "last_acked_revision": 1},
    )

    assert accepted.status_code == 200, accepted.text


def test_a_string_is_refused_where_the_contract_says_integer(
    session: Caller,
) -> None:
    """Те, заради чого `strict=True` існує, і що зникло б разом із ним."""
    refused = session.post(
        "/v1/consents/revoke",
        {"kind": "health_sync", "last_acked_revision": "1"},
    )

    assert refused.status_code == 422


# ---------------------------------------- межа `kdf_params` (незалежний review)


def _key_write(**params: object) -> dict[str, object]:
    return {
        "mode": "rewrap",
        "expected_wrap_version": 0,
        "wrapped_dek": base64.b64encode(b"envelope").decode(),
        "kdf": "pbkdf2-sha256",
        "kdf_params": params,
    }


def test_a_multimegabyte_kdf_parameter_is_refused(session: Caller) -> None:
    """Знайдено незалежним review Фази 5, через поле, сусіднє з межею конверта.

    §7 тримає сервер поза **оцінкою** цих параметрів — підлогу перевіряє клієнт, —
    але не поза їхнім розміром. `ck_vault_key_kdf_params` перевіряє лише
    `jsonb_typeof = 'object'`, тож сесія зі step-up могла покласти під ключ,
    якого сервер навіть не читає, багатомегабайтний блоб.
    """
    refused = session.post(
        "/v1/sync/key",
        _key_write(iterations=1_000_000, salt_hex="00" * 16, junk="A" * 400_000),
    )

    assert refused.status_code == 422
    assert refused.json() == {"detail": "Request validation failed"}


def test_too_many_kdf_parameters_are_refused(session: Caller) -> None:
    refused = session.post(
        "/v1/sync/key",
        _key_write(**{f"p{index}": index for index in range(20)}),
    )

    assert refused.status_code == 422


def test_a_nested_kdf_parameter_is_refused(session: Caller) -> None:
    """Вкладений об'єкт — це те, чим межу на кількість ключів обходять."""
    refused = session.post(
        "/v1/sync/key",
        _key_write(salt_hex="00" * 16, nested={"deep": "A" * 100}),
    )

    assert refused.status_code == 422


def test_the_real_client_parameters_are_still_accepted(session: Caller) -> None:
    """Зворотний бік: клієнт надсилає щонайбільше чотири ключі, і вони проходять.

    Без цього боку три відмови вище пройшли б і на схемі, що відхиляє геть усе.
    """
    accepted = session.post(
        "/v1/sync/key",
        {
            "mode": "rewrap",
            "expected_wrap_version": 0,
            "wrapped_dek": base64.b64encode(b"envelope").decode(),
            "kdf": "argon2id",
            "kdf_params": {"m_kib": 65_536, "t": 3, "p": 1, "salt_hex": "00" * 16},
        },
    )

    assert accepted.status_code == 200, accepted.text


def test_the_eleventh_authentication_attempt_in_a_minute_is_rate_limited(
    caller: Caller,
    engine: Engine,
) -> None:
    """Per-IP вікно §11 — поведінково, у справжньому застосунку.

    Знайдено незалежним review: зняття реєстрації `RateLimitMiddleware` у
    `create_app` червонило рівно один тест, і той структурний (читає
    `application.user_middleware`). Механіку лічильника перевіряв юніт-тест на
    власноруч зібраному стеку; що зібраний `create_app` відповідає 429 на
    одинадцятій спробі, не перевіряв ніхто.

    Сусідній `test_an_unreadable_query_never_spends_the_authentication_window`
    тут не допомагає — він доводить, що вікно **не** витрачається, і проходить
    навіть зовсім без лімітера.
    """
    del engine
    for _ in range(AUTH_ATTEMPTS_PER_MINUTE):
        refused = caller.post("/v1/auth/telegram", {"init_data": "not-signed"})
        assert refused.status_code == 401, refused.text

    throttled = caller.post("/v1/auth/telegram", {"init_data": "not-signed"})
    assert throttled.status_code == 429
    assert throttled.json() == {"error": "rate_limited"}
    assert int(throttled.headers["Retry-After"]) > 0
