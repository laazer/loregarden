"""The count the judge judges comes from the runner's own collection.

C-2: never parsed from stdout, never inferred from an exit code —
`len(session.items)` in `pytest_collection_finish` AND `len(ids)` in
`pytest_xdist_node_collection_finished` for pytest, `numTotalTestSuites` for
jest. C-3: the sink is always on and fails closed; a count that could not be
written is an absence the judge resolves as `could_not_run`, never silence and
never a legitimate-looking zero.

| criterion | tests here |
|---|---|
| G-2 recorded from collection, in both modes | `test_the_pytest_sink_records_the_count_*`, `test_the_jest_reporter_records_the_suite_count` |
| G-2 / R-1 the regression that would fail every push with 69 | `test_the_pytest_sink_records_a_zero_collection_in_both_modes` |
| C-3 always on | `test_the_*_sink_records_without_a_progress_file` |
| G-4 fail closed | `test_an_unwritable_count_file_is_said_*` |
| B-3 single writer | `test_the_*_sink_writes_no_progress_file_when_none_was_named` |

`-n 2` is not decoration: under xdist `pytest_collection_finish` fires only in
workers, where the plugin's state is None, so a sink written from that hook
alone is never written on the normal pre-push path and every push would fail
with 69 — diagnosed, wrongly, as "the guard is broken".
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / ".lefthook" / "scripts"
JEST_REPORTER = SCRIPTS / "jest_push_progress.cjs"

COUNT_ENV = "LOREGARDEN_TEST_COUNT_FILE"
PROGRESS_ENV = "LOREGARDEN_PROGRESS_FILE"

#: Serial and under xdist. "0" is pytest-xdist's own spelling for "no workers".
BOTH_MODES = ("0", "2")

#: Scrubbed from every nested runner: a `-k` the OUTER run was given (a push with
#: PYTEST_ADDOPTS set, or a developer narrowing the suite) deselects the inner
#: suite, which then exits 5 and the test reads as a defect in the sink.
INHERITED_PYTEST_STATE = ("PYTEST_XDIST_WORKER", "PYTEST_ADDOPTS", "PYTEST_CURRENT_TEST")


@pytest.fixture(name="tiny_suite")
def tiny_suite_fixture(tmp_path) -> Path:
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_tiny.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.parametrize('i', range(6))\n"
        "def test_ok(i):\n    assert i >= 0\n\n"
        "def test_fails():\n    assert False\n"
    )
    return suite


def _run_pytest(
    suite: Path,
    tmp_path: Path,
    *,
    workers: str,
    count_file: Path | None,
    progress_file: Path | None = None,
    extra: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(SCRIPTS)}
    for inherited in (*INHERITED_PYTEST_STATE, PROGRESS_ENV, COUNT_ENV):
        env.pop(inherited, None)
    if count_file is not None:
        env[COUNT_ENV] = str(count_file)
    if progress_file is not None:
        env[PROGRESS_ENV] = str(progress_file)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "pytest_push_progress",
         "-n", workers, "-p", "no:cacheprovider", *extra, str(suite)],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=180, check=False,
    )  # fmt: skip


def _count(path: Path) -> int:
    return json.loads(path.read_text(encoding="utf-8"))["count"]


# ---- pytest: the count comes from collection, in both modes ----------------


@pytest.mark.parametrize("workers", BOTH_MODES)
def test_the_pytest_sink_records_the_count_from_its_own_collection(
    tiny_suite, tmp_path, workers
) -> None:
    count_file = tmp_path / "count.json"
    result = _run_pytest(tiny_suite, tmp_path, workers=workers, count_file=count_file)

    assert result.returncode == 1, result.stdout + result.stderr  # the run keeps its verdict
    assert _count(count_file) == 7


@pytest.mark.parametrize("workers", BOTH_MODES)
def test_the_pytest_sink_records_a_zero_collection_in_both_modes(
    tiny_suite, tmp_path, workers
) -> None:
    """R-1. Measured: a non-matching `-k` exits 5 serially and under `-n 2`. The
    guard must judge the recorded 0, so the 0 has to be recorded in both modes."""
    count_file = tmp_path / "count.json"
    result = _run_pytest(
        tiny_suite, tmp_path, workers=workers, count_file=count_file, extra=("-k", "zzzNoSuchTest")
    )

    assert result.returncode == 5, result.stdout + result.stderr
    assert _count(count_file) == 0


@pytest.mark.parametrize("workers", BOTH_MODES)
def test_the_pytest_sink_records_without_a_progress_file(tiny_suite, tmp_path, workers) -> None:
    """C-3: the sink is always on. Today the plugin returns early unless a capacity
    holder named a progress file; the count must not depend on that."""
    count_file = tmp_path / "count.json"
    _run_pytest(tiny_suite, tmp_path, workers=workers, count_file=count_file, progress_file=None)

    assert _count(count_file) == 7


@pytest.mark.parametrize("workers", BOTH_MODES)
def test_the_pytest_sink_writes_no_progress_file_when_none_was_named(
    tiny_suite, tmp_path, workers
) -> None:
    """B-3: one writer per file, and the count file is not the progress file."""
    count_file = tmp_path / "count.json"
    _run_pytest(tiny_suite, tmp_path, workers=workers, count_file=count_file)

    assert [p.name for p in sorted(tmp_path.glob("*.json"))] == ["count.json"]


def test_the_pytest_sink_records_the_count_beside_a_progress_file(tiny_suite, tmp_path) -> None:
    """Both files, written by the same plugin, holding the same number (B-3)."""
    count_file = tmp_path / "count.json"
    progress = tmp_path / "progress.json"
    _run_pytest(tiny_suite, tmp_path, workers="2", count_file=count_file, progress_file=progress)

    assert _count(count_file) == 7
    assert json.loads(progress.read_text(encoding="utf-8"))["total"] == 7


@pytest.mark.parametrize("workers", BOTH_MODES)
def test_an_unwritable_count_file_is_said_not_swallowed(tiny_suite, tmp_path, workers) -> None:
    """G-4 / R-4 / R-7: today both reporters swallow their own write failures and
    warn once. The count must fail closed — the file stays absent, so the judge
    resolves `could_not_run`, and the run says which path it could not write."""
    unwritable = tmp_path / "no-such-dir" / "count.json"
    result = _run_pytest(tiny_suite, tmp_path, workers=workers, count_file=unwritable)

    assert not unwritable.exists()
    assert str(unwritable) in result.stdout + result.stderr
    assert "count" in (result.stdout + result.stderr).lower()


def test_the_pytest_sink_does_not_read_the_exit_code_or_the_report_text() -> None:
    """C-2, structurally: the number comes from the two collection hooks."""
    source = (SCRIPTS / "pytest_push_progress.py").read_text(encoding="utf-8")

    assert "len(session.items)" in source
    assert "len(ids)" in source
    assert "exitstatus" not in source


# ---- jest: the suite count, from the reporter's own run start ---------------


def _run_reporter(
    tmp_path: Path, *, suites: int, count_file: Path | None, progress_file: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """The reporter in isolation: jest's own `--listTests`/run cost nothing here,
    and A-3 means the number is the suite (file) count jest knows up front."""
    node = shutil.which("node")
    assert node, "the client suite needs node; so does its reporter"
    script = (
        f"const R = require({str(JEST_REPORTER)!r});\n"
        "const r = new R({}, {});\n"
        f"r.onRunStart({{ numTotalTestSuites: {suites} }});\n"
        f"r.onRunComplete({{}}, {{ numTotalTestSuites: {suites}, numTotalTests: 0 }});\n"
    )
    env = {k: v for k, v in os.environ.items() if k not in (COUNT_ENV, PROGRESS_ENV)}
    if count_file is not None:
        env[COUNT_ENV] = str(count_file)
    if progress_file is not None:
        env[PROGRESS_ENV] = str(progress_file)
    return subprocess.run(
        [node, "-e", script],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip


@pytest.mark.parametrize("suites", [286, 156, 1])
def test_the_jest_reporter_records_the_suite_count(tmp_path, suites) -> None:
    count_file = tmp_path / "count.json"
    result = _run_reporter(tmp_path, suites=suites, count_file=count_file)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _count(count_file) == suites


def test_the_jest_reporter_records_a_zero_suite_selection(tmp_path) -> None:
    """Measured: `npx jest --bail --passWithNoTests zzzNoSuchSuite` exits 0, exactly
    like a legitimately empty `--changedSince`. The recorded zero is what the
    guard judges, and the `--listTests` probe is what separates the two."""
    count_file = tmp_path / "count.json"
    _run_reporter(tmp_path, suites=0, count_file=count_file)

    assert _count(count_file) == 0


def test_the_jest_reporter_records_without_a_progress_file(tmp_path) -> None:
    count_file = tmp_path / "count.json"
    _run_reporter(tmp_path, suites=286, count_file=count_file, progress_file=None)

    assert _count(count_file) == 286


def test_the_jest_reporter_writes_no_progress_file_when_none_was_named(tmp_path) -> None:
    count_file = tmp_path / "count.json"
    _run_reporter(tmp_path, suites=286, count_file=count_file)

    assert [p.name for p in sorted(tmp_path.glob("*.json"))] == ["count.json"]


def test_an_unwritable_count_file_is_said_by_the_jest_reporter(tmp_path) -> None:
    unwritable = tmp_path / "no-such-dir" / "count.json"
    result = _run_reporter(tmp_path, suites=286, count_file=unwritable)

    assert not unwritable.exists()
    assert str(unwritable) in result.stdout + result.stderr


def test_the_jest_reporter_takes_the_count_from_the_run_not_the_results() -> None:
    """C-2: `numTotalTestSuites`, jest's own count, and no exit code anywhere."""
    source = JEST_REPORTER.read_text(encoding="utf-8")

    assert "numTotalTestSuites" in source
    assert "process.exitCode" not in source
    assert "exitCode" not in source


# ---- B-3: one recording process, not one writing module -------------------
#
# server-tests.sh EXPORTS $LOREGARDEN_TEST_COUNT_FILE, so every pytest the suite
# itself spawns inherits it — and this suite does spawn pytest (the two modules
# beside this one, and test_capacity_progress.py). With last-writer-wins a run
# that collected 25 reported 6, 6 and 7 over three repeats: the number was
# whichever nested run finished collecting last. The grep that pins the single
# writer could not see it, because the second writer WAS pytest_push_progress.py
# in another process, and a module-name grep cannot express "one process".


def _nesting_suite(tmp_path: Path, outer_count: Path) -> tuple[Path, Path]:
    """An outer suite of 3 tests, one of which runs pytest twice over an inner
    suite of 6 — once with the environment it inherited, once with the outer
    run's count path handed to it explicitly, which is the environment
    `{**os.environ, ...}` rebuilds by hand."""
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "test_inner.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.parametrize('i', range(6))\n"
        "def test_inner(i):\n    assert True\n"
    )
    outer = tmp_path / "outer"
    outer.mkdir()
    (outer / "test_outer.py").write_text(
        "import os\n"
        "import subprocess\n"
        "import sys\n\n"
        "def _nested(env):\n"
        "    return subprocess.run(\n"
        f"        [sys.executable, '-m', 'pytest', '-q', '-p', 'pytest_push_progress',\n"
        f"         '-p', 'no:cacheprovider', {str(inner)!r}],\n"
        f"        env=env, cwd={str(tmp_path)!r}, capture_output=True, text=True,\n"
        "        timeout=180, check=False,\n"
        "    )\n\n"
        "def test_a_nested_run_inheriting_the_environment():\n"
        "    nested = _nested({**os.environ})\n"
        "    assert nested.returncode == 0, nested.stdout + nested.stderr\n\n"
        "def test_a_nested_run_handed_the_path_by_hand():\n"
        f"    nested = _nested({{**os.environ, {COUNT_ENV!r}: {str(outer_count)!r}}})\n"
        "    assert nested.returncode == 0, nested.stdout + nested.stderr\n\n"
        "def test_something_the_push_actually_verified():\n"
        "    assert True\n"
    )
    return outer, inner


@pytest.mark.parametrize("workers", BOTH_MODES)
def test_a_nested_runner_cannot_overwrite_the_outer_runs_count(tmp_path, workers) -> None:
    """B-3, the defect the grep could not see. The outer run collected 3 tests;
    the two nested runs collect 6 each and must leave the recorded 3 alone, so
    the judge reports the number of tests this push actually ran."""
    count_file = tmp_path / "count.json"
    outer, _inner = _nesting_suite(tmp_path, count_file)

    result = _run_pytest(outer, tmp_path, workers=workers, count_file=count_file)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _count(count_file) == 3, "a nested runner recorded its count over this run's"


def test_the_count_path_is_taken_out_of_the_environment_the_run_hands_on(tmp_path) -> None:
    """The first of the two mechanisms, on its own: the path cannot reach a child
    at all, rather than reaching one asked politely not to write it."""
    count_file = tmp_path / "count.json"
    suite = tmp_path / "suite"
    suite.mkdir()
    seen = tmp_path / "seen.txt"
    (suite / "test_env.py").write_text(
        "import os\n"
        "import subprocess\n\n"
        "def test_what_a_child_inherits():\n"
        f"    subprocess.run(['sh', '-c', 'printf %s \"${COUNT_ENV}\" > {seen}'],\n"
        "                   check=True)\n"
    )

    result = _run_pytest(suite, tmp_path, workers="0", count_file=count_file)

    assert result.returncode == 0, result.stdout + result.stderr
    assert seen.read_text(encoding="utf-8") == "", f"a child inherited ${COUNT_ENV}"
    assert _count(count_file) == 1


def test_the_jest_reporter_does_not_overwrite_a_count_already_recorded(tmp_path) -> None:
    """B-3 for the client half: the reporter claims the path by creating it, so a
    second run handed the same path (an inherited $LOREGARDEN_TEST_COUNT_FILE,
    which client-tests.sh exports too) leaves the first run's number alone."""
    count_file = tmp_path / "count.json"
    _run_reporter(tmp_path, suites=156, count_file=count_file)
    result = _run_reporter(tmp_path, suites=286, count_file=count_file)

    assert _count(count_file) == 156, "a second reporter recorded over the first run's count"
    assert str(count_file) in result.stdout + result.stderr
