"""Документи не падають — і саме тому їхні твердження перевіряються тут.

Головний клас дефекту Фази 5, названий у промпті прямо: «твердження в threat
model і в матриці відповідності, яких код не виконує». Документ, що посилається
на тест, якого немає, читається як доказ і ним не є — і жоден інший тест цього не
побачить.

Цей модуль знайшов дві такі помилки в першій редакції `docs/threat-model.md`
(посилання на `apps/web/src/state/persist.test.ts`, якого не існує, і на
`test_sync_concurrency.py` замість модуля, де насправді живе тест гонки §9.8).
Обидві були написані на око й виглядали правдоподібно.

Перевіряється три речі, і кожна — механічно:

* кожен шлях до тесту, названий у документі, існує;
* кожне ім'я **python**-тесту у формі `модуль::test_щось` справді є в тому модулі;
* у threat model немає операційних значень (§6.5), і всі 18 пунктів §13 названі.

**Названа межа:** для web перевіряється лише існування файлу, не ім'я тесту.
Імена там українські й у вільній формі (`it('save серіалізує все, крім…')`), тож
надійного розбору немає, а ненадійний давав би хибні червоні й привчав його
обходити. Ту половину тримає рецензент.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
DOCS = (
    REPO_ROOT / "docs" / "threat-model.md",
    REPO_ROOT / "docs" / "plans" / "backend-bot-plan-progress.md",
    REPO_ROOT / "docs" / "restore-runbook.md",
)

#: `tests/integration/test_x.py::test_y` або `apps/web/src/a/b.test.ts`.
_PYTHON_TEST = re.compile(r"(?:apps/api/)?(tests/[\w/]+\.py)(?:::([\w]+))?")
_WEB_TEST = re.compile(r"(apps/web/(?:src|e2e)/[\w/.\-]+\.(?:test|spec)\.tsx?)")
_SCRIPT = re.compile(r"(apps/web/scripts/[\w.\-]+\.mjs|scripts/[\w.\-]+\.(?:sh|py))")


def _documents() -> list[tuple[Path, str]]:
    return [(path, path.read_text(encoding="utf-8")) for path in DOCS]


def _python_test_path(relative: str) -> Path:
    return REPO_ROOT / "apps" / "api" / relative


@pytest.mark.parametrize("document", DOCS, ids=lambda path: path.name)
def test_every_python_test_module_named_in_a_document_exists(document: Path) -> None:
    text = document.read_text(encoding="utf-8")
    missing = {
        relative
        for relative, _ in _PYTHON_TEST.findall(text)
        if not _python_test_path(relative).is_file()
    }

    assert missing == set(), f"{document.name} names modules that do not exist"


@pytest.mark.parametrize("document", DOCS, ids=lambda path: path.name)
def test_every_named_python_test_exists_in_its_module(document: Path) -> None:
    """`модуль::test_ім'я` — і саме ім'я, а не лише модуль.

    Це те, що ловить «тест перейменували, документ ні»: посилання лишається
    правдоподібним, а доказу за ним уже немає.
    """
    text = document.read_text(encoding="utf-8")
    missing: set[str] = set()
    for relative, name in _PYTHON_TEST.findall(text):
        if not name:
            continue
        path = _python_test_path(relative)
        if not path.is_file():
            continue  # покрито тестом вище
        if f"def {name}(" not in path.read_text(encoding="utf-8"):
            missing.add(f"{relative}::{name}")

    assert missing == set(), f"{document.name} names tests that do not exist"


@pytest.mark.parametrize("document", DOCS, ids=lambda path: path.name)
def test_every_web_test_module_named_in_a_document_exists(document: Path) -> None:
    text = document.read_text(encoding="utf-8")
    missing = {
        relative
        for relative in _WEB_TEST.findall(text)
        if not (REPO_ROOT / relative).is_file()
    }

    assert missing == set(), f"{document.name} names web test files that do not exist"


@pytest.mark.parametrize("document", DOCS, ids=lambda path: path.name)
def test_every_script_named_in_a_document_exists(document: Path) -> None:
    text = document.read_text(encoding="utf-8")
    missing = {
        relative
        for relative in _SCRIPT.findall(text)
        if not (REPO_ROOT / relative).is_file()
    }

    assert missing == set(), f"{document.name} names scripts that do not exist"


# ------------------------------------------------------- threat model, окремо

THREAT_MODEL = REPO_ROOT / "docs" / "threat-model.md"

#: §6.5: операційна частина резидентності в публічному git не живе. Перелік — це
#: форми, у яких вона просочилася б: провайдер, хост, бакет, ключ.
_OPERATIONAL = (
    re.compile(r"\bs3://", re.IGNORECASE),
    re.compile(r"\bhetzner\b", re.IGNORECASE),
    re.compile(r"\bbackblaze\b", re.IGNORECASE),
    re.compile(r"\bwasabi\b", re.IGNORECASE),
    re.compile(r"\bcloudflare\b", re.IGNORECASE),
    re.compile(r"\bnbg1\b|\bfsn1\b|\bhel1\b", re.IGNORECASE),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # Сира IPv4-адреса, крім документаційних діапазонів RFC 5737.
    re.compile(
        r"\b(?!192\.0\.2\.|198\.51\.100\.|203\.0\.113\.)\d{1,3}(?:\.\d{1,3}){3}\b"
    ),
    # 64 hex-символи виглядають як ключ незалежно від того, чим вони є.
    re.compile(r"\b[0-9a-f]{64}\b"),
)


def test_the_threat_model_names_no_operational_value() -> None:
    text = THREAT_MODEL.read_text(encoding="utf-8")

    found = {pattern.pattern for pattern in _OPERATIONAL if pattern.search(text)}

    assert found == set(), "§6.5: операційне значення в публічному документі"


def test_the_threat_model_names_no_external_host() -> None:
    """Посилання всередину репозиторію — так; зовнішній хост — ні.

    Виняток рівно один і він не операційний: `https://telegram.org` у CSP, який
    §13.17 називає обов'язковим. Він у документі є як **оцінка політики**, тож
    перелічений тут, а не дозволений загалом.
    """
    text = THREAT_MODEL.read_text(encoding="utf-8")
    allowed = {
        "https://telegram.org",
        "https://web.telegram.org",
        "https://*.telegram.org",
    }

    hosts = set(re.findall(r"https?://[\w.*\-]+", text)) - allowed

    assert hosts == set(), f"зовнішні хости в threat model: {sorted(hosts)}"


@pytest.mark.parametrize("item", range(1, 19))
def test_the_threat_model_names_every_open_risk_of_section_thirteen(item: int) -> None:
    """Усі 18 пунктів §13 — «відсутність пункту є дефектом, а не скороченням».

    Перевіряється рядок таблиці, а не згадка в тексті: згадка «§13.4» деінде
    пройшла б і на документі, який пункт 4 не розглядає.
    """
    text = THREAT_MODEL.read_text(encoding="utf-8")

    assert f"\n| {item} |" in text, f"§13.{item} не має рядка в таблиці"


def test_every_claim_in_the_threat_model_carries_a_marker() -> None:
    """Три стани, і третього немає: тест, прийнятий ризик, людина.

    Слабка форма перевірки — що всі три позначки взагалі вживаються і що їх
    достатньо, щоб покрити чотирнадцять підрозділів і вісімнадцять пунктів. Сильну
    («кожне твердження має позначку») машина не відрізнить від прози, тож її
    тримає рецензент, а не цей тест.
    """
    text = THREAT_MODEL.read_text(encoding="utf-8")

    assert text.count("`[T]`") >= 18
    assert text.count("`[R]`") >= 14
    assert text.count("`[H]`") >= 3
