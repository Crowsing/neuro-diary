"""Every worker has a way to be started, and the loop that starts them holds.

Phase 3 closed with «жоден воркер і далі не має точки запуску» in the progress
file. This module is what makes that sentence false and keeps it false: a
worker added later without an entry point fails here rather than being noticed
during an incident.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import threading
import tomllib
from datetime import timedelta
from pathlib import Path

import pytest

from app import worker_main

API_ROOT = Path(__file__).resolve().parents[2]

#: Every long-running or hand-started worker in the codebase. The list is
#: spelled out rather than discovered so that adding a worker is a deliberate
#: edit here too.
WORKERS = (
    "OutboxDispatcher",
    "Housekeeper",
    "VaultCompactor",
    "ReminderWorker",
    "RestoreReconciler",
)


def _instantiated_names() -> set[str]:
    """Class names `app.worker_main` actually constructs, read from its AST.

    A test that only checked the imports would pass on a module that imports a
    worker and never builds it — which is precisely the state phase 3 left.
    """
    tree = ast.parse((API_ROOT / "app" / "worker_main.py").read_text("utf-8"))
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def test_every_worker_is_constructed_by_an_entry_point() -> None:
    assert set(WORKERS) <= _instantiated_names()


def test_the_entry_points_are_declared_as_console_scripts() -> None:
    manifest = tomllib.loads((API_ROOT / "pyproject.toml").read_text("utf-8"))
    scripts = manifest["project"]["scripts"]

    assert scripts == {
        "neuro-maintenance": "app.worker_main:maintenance",
        "neuro-reminders": "app.worker_main:reminders",
        "neuro-restore-reconcile": "app.worker_main:restore_reconcile",
    }
    for target in scripts.values():
        _, _, attribute = target.partition(":")
        assert callable(getattr(worker_main, attribute))


def test_the_reconciler_is_a_command_and_not_a_loop() -> None:
    """§6.4: reconciliation deletes data on a decision a person takes."""
    manifest = tomllib.loads((API_ROOT / "pyproject.toml").read_text("utf-8"))

    assert "neuro-restore-reconcile" in manifest["project"]["scripts"]
    assert worker_main.restore_reconcile(["only-one-argument"]) == 2


# ------------------------------------------------------------------- the driver


def test_the_driver_calls_the_step_until_it_is_stopped() -> None:
    stop = threading.Event()
    ticks: list[int] = []

    def step() -> None:
        ticks.append(1)
        if len(ticks) == 3:
            stop.set()

    worker_main.drive("test", step, period=timedelta(0), stop=stop)

    assert len(ticks) == 3


def test_a_failing_tick_does_not_end_the_process() -> None:
    """One unreachable row must not become an outage that lasts until morning."""
    stop = threading.Event()
    ticks: list[int] = []

    def step() -> None:
        ticks.append(1)
        if len(ticks) == 3:
            stop.set()
        raise RuntimeError("the database went away")

    worker_main.drive("test", step, period=timedelta(0), stop=stop)

    assert len(ticks) == 3


def test_a_stop_before_the_first_tick_runs_nothing() -> None:
    """SIGTERM during startup ends the loop rather than starting one more write."""
    stop = threading.Event()
    stop.set()

    def step() -> None:  # pragma: no cover - the point is that it never runs
        raise AssertionError("stopped loops do not tick")

    worker_main.drive("test", step, period=timedelta(0), stop=stop)


def test_no_entry_point_needs_a_bot_token_to_be_imported() -> None:
    """CI has no bot and never will (§10 launch parameters).

    Importing the module is what a console script does first, so anything that
    reached for the token at import time would make every green run depend on a
    secret that is not there. A fresh interpreter with an emptied environment,
    so nothing an earlier test set can hide it.
    """
    probe = subprocess.run(
        [sys.executable, "-c", "import app.worker_main; print('ok')"],
        cwd=API_ROOT,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(API_ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "ok"


def test_the_timing_invariants_are_on_the_worker_import_path() -> None:
    """§10's consistency condition is checked with `raise`, not with `assert`.

    That argument only holds if a running process loads the module — `python -O`
    strips `assert`, and a check nothing imports dodges the letter of the
    problem while keeping all of its substance. Same reasoning, and the same
    test, as `app.domain.retention` already carries.
    """
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import app.worker_main, sys; print('app.domain.reminders' in sys.modules)",
        ],
        cwd=API_ROOT,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(API_ROOT)},
        capture_output=True,
        text=True,
        check=True,
    )

    assert probe.stdout.strip() == "True", probe.stderr


def test_the_maintenance_period_fits_inside_the_erasure_ceiling() -> None:
    """§4.3 gives the safety net fifteen minutes; one pass at the edge is not it."""
    assert worker_main.MAINTENANCE_PERIOD < timedelta(minutes=15)


@pytest.mark.parametrize("name", WORKERS)
def test_each_worker_is_importable_without_configuration(name: str) -> None:
    assert getattr(worker_main, name)
