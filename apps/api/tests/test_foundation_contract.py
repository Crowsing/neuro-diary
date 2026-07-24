from pathlib import Path
import tomllib


REPO_ROOT = Path(__file__).resolve().parents[3]
API_ROOT = REPO_ROOT / "apps" / "api"


def test_phase_zero_artifacts_exist() -> None:
    required = (
        REPO_ROOT / ".env.example",
        REPO_ROOT / "compose.yaml",
        REPO_ROOT / ".github" / "workflows" / "ci.yml",
        REPO_ROOT / "docs" / "plans" / "backend-bot-plan-progress.md",
        API_ROOT / "alembic.ini",
        API_ROOT / "app" / "infra" / "db" / "migrations" / "env.py",
        API_ROOT
        / "app"
        / "infra"
        / "db"
        / "migrations"
        / "versions"
        / "0001_foundation.py",
    )

    assert [
        str(path.relative_to(REPO_ROOT)) for path in required if not path.is_file()
    ] == []


def test_root_environment_example_has_no_secret_values_or_bot_token() -> None:
    assignments = {
        key: value
        for line in (REPO_ROOT / ".env.example").read_text().splitlines()
        if line and not line.startswith("#")
        for key, value in (line.split("=", 1),)
    }

    assert "TELEGRAM_BOT_ID" in assignments
    assert "BOT_TOKEN" not in assignments
    assert all(value == "" for value in assignments.values())


def test_api_dependencies_and_import_contracts_are_pinned() -> None:
    pyproject = tomllib.loads((API_ROOT / "pyproject.toml").read_text())
    dependencies = pyproject["project"]["dependencies"]
    dev_dependencies = pyproject["dependency-groups"]["dev"]

    assert all("==" in dependency for dependency in dependencies)
    assert all("==" in dependency for dependency in dev_dependencies)
    assert any(dependency.startswith("pydantic==") for dependency in dependencies)
    assert any(
        dependency.startswith("import-linter==2.") for dependency in dev_dependencies
    )

    import_linter = pyproject["tool"]["importlinter"]
    contracts = import_linter["contracts"]
    assert import_linter == {
        "root_package": "app",
        "include_external_packages": True,
        "contracts": contracts,
    }
    assert [contract["name"] for contract in contracts] == [
        "Layers",
        "Domain is pure",
        "No ORM/DB above repositories",
        "Workers do not import ORM models",
        "Services are framework-free",
        "Schemas are standalone",
        "Reminder worker never touches vault",
    ]


def test_ci_contains_every_phase_zero_gate() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()

    for command in (
        "pnpm test",
        "pnpm build",
        "pnpm e2e",
        "uv run --locked pytest",
        "uv run --locked ruff check .",
        "uv run --locked mypy --strict",
        "uv run --locked lint-imports",
        "gitleaks",
    ):
        assert command in workflow


def test_generated_health_exports_stay_out_of_git() -> None:
    ignores = (REPO_ROOT / ".gitignore").read_text().splitlines()

    assert "artifacts/exports/" in ignores
    assert "output/" in ignores
