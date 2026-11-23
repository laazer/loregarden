#!/usr/bin/env python3
"""Run mutmut on a plan from mutation_changed_lines.py; write a JSON verdict.

    mutmut_driver.py --plan PLAN.json --result RESULT.json [--baseline-only]

Runs from ``server/`` (where ``[tool.mutmut]`` lives), inside the capacity
grant, with ``LOREGARDEN_CAPACITY_WORKERS`` as mutmut's ``--max-children``.

Why in-process rather than the ``mutmut`` CLI: two settings change every run,
``only_mutate`` (the changed files) and ``pytest_add_cli_args_test_selection``
(the selected tests), and mutmut reads its config only from
``pyproject.toml``. Rewriting the checked-in file per run would race other runs
and dirty the tree the orchestrator commits, so this loads that config and
replaces those two fields. That touches mutmut internals, which is why the
version is pinned exactly in ``server/pyproject.toml`` and checked here.

The result's ``phase`` separates the outcomes mutmut reports only as
``exit(1)``: ``baseline_failed`` (stats or clean run red on the unmutated
copy), ``no_coverage`` (the selected tests run none of the mutated code),
``error`` (anything else), and ``done``.
"""

from __future__ import annotations

import argparse
import dataclasses
import fcntl
import hashlib
import json
import os
import shutil
import sys
import tempfile
import traceback
from enum import Enum
from pathlib import Path
from typing import Any

PINNED_MUTMUT = "3.8.1"
_SCRIPTS = Path(__file__).resolve().parent
MUTANTS_DIR = Path("mutants")


class Phase(str, Enum):
    """How far a run got — the verdict's ``phase``, read back by
    mutation_changed_lines.py. A str enum so it round-trips through JSON."""

    DONE = "done"
    BASELINE_FAILED = "baseline_failed"
    NO_COVERAGE = "no_coverage"
    ERROR = "error"


class CheckoutBusy(Exception):
    """Another mutation run holds this checkout's mutants/."""


class _BaselineDone(Exception):
    """Raised after a green clean run when only the baseline was asked for."""


class _Phases:
    """What the wrapped runner saw, so a bare ``exit(1)`` can be classified."""

    def __init__(self) -> None:
        self.stats_exit: int | None = None
        self.clean_exit: int | None = None


def _wrap_runner(runner: Any, phases: _Phases, baseline_only: bool) -> Any:
    collect_stats = runner.collect_stats
    run_clean_tests = runner.run_clean_tests

    def recording_collect_stats(tests: Any) -> int:
        phases.stats_exit = collect_stats(tests)
        return phases.stats_exit

    def recording_run_clean_tests(tests: Any) -> int:
        phases.clean_exit = run_clean_tests(tests)
        if baseline_only and phases.clean_exit == 0:
            raise _BaselineDone
        return phases.clean_exit

    runner.collect_stats = recording_collect_stats
    runner.run_clean_tests = recording_run_clean_tests
    return runner


def _workers() -> int:
    raw = os.environ.get("LOREGARDEN_CAPACITY_WORKERS", "")
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    print(
        "mutmut_driver: LOREGARDEN_CAPACITY_WORKERS unset (ran outside capacity-run.sh?); "
        "using 2 workers",
        file=sys.stderr,
    )
    return 2


def _check_version() -> str | None:
    import mutmut

    if mutmut.__version__ != PINNED_MUTMUT:
        return (
            f"mutmut {mutmut.__version__} is installed; this driver is written against "
            f"{PINNED_MUTMUT} internals. Run `uv sync --extra dev` in server/."
        )
    return None


def _checkout_lock() -> Any:
    """Hold this checkout's mutants/ for the run; a second run here would delete it
    under the first. Returns the open lock file; raises CheckoutBusy when held."""
    digest = hashlib.sha256(str(Path.cwd().resolve()).encode()).hexdigest()[:16]
    handle = open(Path(tempfile.gettempdir()) / f"loregarden-mutmut-{digest}.lock", "w")  # noqa: SIM115
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise CheckoutBusy(f"another mutation run holds {Path.cwd() / MUTANTS_DIR}") from exc
    return handle


def run(plan: dict[str, Any], baseline_only: bool) -> dict[str, Any]:
    problem = _check_version()
    if problem:
        return {"phase": Phase.ERROR, "detail": problem}
    if "[tool.mutmut]" not in Path("pyproject.toml").read_text(encoding="utf-8"):
        return {
            "phase": Phase.ERROR,
            "detail": f"run from server/ ({Path.cwd()} has no [tool.mutmut])",
        }
    try:
        lock = _checkout_lock()
    except CheckoutBusy as exc:
        return {"phase": Phase.ERROR, "detail": str(exc)}
    with lock:
        return _run_locked(plan, baseline_only)


def _run_locked(plan: dict[str, Any], baseline_only: bool) -> dict[str, Any]:

    import mutmut.__main__ as mm
    from mutmut import configuration
    from mutmut.mutation.diff_apply import get_diff_for_mutant
    from mutmut.stats import status_by_exit_code

    # A fresh copy every run: mutmut keeps unmutated files it already copied,
    # so a reused mutants/ can test stale code.
    if MUTANTS_DIR.exists():
        shutil.rmtree(MUTANTS_DIR)

    basetemp_root = Path(tempfile.mkdtemp(prefix="loregarden-mutmut-"))
    os.environ["LOREGARDEN_MUTMUT_BASETEMP_ROOT"] = str(basetemp_root)
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))

    base = configuration.config()
    configuration._config = dataclasses.replace(
        base,
        only_mutate=list(plan["only_mutate"]),
        pytest_add_cli_args_test_selection=list(plan["tests"]),
        # Every forked pytest gets its own basetemp; see pytest_mutation_basetemp.py.
        pytest_add_cli_args=[*base.pytest_add_cli_args, "-p", "pytest_mutation_basetemp"],
    )

    phases = _Phases()
    real_get_runner = mm.get_mutant_runner
    mm.get_mutant_runner = lambda max_workers=1: _wrap_runner(
        real_get_runner(max_workers), phases, baseline_only
    )
    globs = list(plan["globs"])
    try:
        try:
            mm._run(globs, _workers())
        except _BaselineDone:
            return {"phase": Phase.DONE, "baseline_only": True, "mutants": []}
        except SystemExit as exc:
            if exc.code not in (0, None):
                return _classify_exit(phases, exc.code, _first_failures(basetemp_root))
        mutants, _ = mm.collect_source_file_mutation_data(mutant_names=globs)
        out = []
        for data, name, exit_code in mutants:
            status = status_by_exit_code[exit_code]
            entry: dict[str, Any] = {"name": name, "status": status}
            if status != "killed":
                entry["diff"] = get_diff_for_mutant(name, path=data.path)
            out.append(entry)
        return {"phase": Phase.DONE, "mutants": out}
    finally:
        mm.get_mutant_runner = real_get_runner
        shutil.rmtree(
            basetemp_root, ignore_errors=True
        )  # silent-ok: scratch dir; a leftover costs disk only
        # The copy is ~1000 files of the package inside the checkout. Gitignored,
        # but the gates and other whole-tree scans read the disk, not git: a
        # leftover one made every test "duplicate" its copy. Rebuilt every run.
        if MUTANTS_DIR.exists():
            shutil.rmtree(MUTANTS_DIR)


def _first_failures(basetemp_root: Path) -> list[str]:
    """Each pytest session's first failure, as pytest_mutation_basetemp.py wrote it.
    Read only when mutmut stopped before any mutant ran, so every file is a
    baseline session's."""
    from pytest_mutation_basetemp import FAILURE_FILE

    found = sorted(basetemp_root.glob(f"*.{FAILURE_FILE}"), key=lambda f: f.stat().st_mtime)
    return [f.read_text(encoding="utf-8") for f in found]


def _classify_exit(phases: _Phases, code: object, failures: list[str]) -> dict[str, Any]:
    if phases.stats_exit not in (None, 0):
        return {
            "phase": Phase.BASELINE_FAILED,
            "detail": f"stats run (all selected tests, unmutated) exited {phases.stats_exit}",
            "failures": failures,
        }
    if phases.stats_exit == 0 and phases.clean_exit is None:
        return {
            "phase": Phase.NO_COVERAGE,
            "detail": "mutmut found no selected test that executes the mutated functions",
        }
    if phases.clean_exit not in (None, 0):
        return {
            "phase": Phase.BASELINE_FAILED,
            "detail": f"clean test run exited {phases.clean_exit}",
            "failures": failures,
        }
    return {"phase": Phase.ERROR, "detail": f"mutmut exited {code}"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--baseline-only", action="store_true")
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    try:
        result = run(plan, args.baseline_only)
    except Exception:  # noqa: BLE001 - recorded as the verdict and printed, never swallowed
        traceback.print_exc()
        result = {"phase": Phase.ERROR, "detail": traceback.format_exc(limit=3)}
    args.result.write_text(json.dumps(result), encoding="utf-8")
    return 0 if result["phase"] == Phase.DONE else 1


if __name__ == "__main__":
    raise SystemExit(main())
