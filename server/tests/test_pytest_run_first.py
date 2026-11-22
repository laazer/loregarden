"""A full pre-push run starts with the tests the change reaches.

`pytest_run_first.py` moves the files named by `--run-first` to the front of
the collection, and `--ff` puts last run's failures ahead of those. Each test
here runs a real nested pytest over a tiny suite, because the claim is about
the order pytest actually executes, not about a list a helper returns.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / ".lefthook" / "scripts"

#: Scrubbed from every nested runner, so the outer run's narrowing cannot
#: deselect the inner suite (see test_push_test_count_sink.py).
INHERITED_PYTEST_STATE = ("PYTEST_XDIST_WORKER", "PYTEST_ADDOPTS", "PYTEST_CURRENT_TEST")

#: Enough passing tests ahead of the listed file that a run reaching it in
#: collection order is measurably later than one that starts with it.
FILLER = 120


@pytest.fixture(name="suite")
def suite_fixture(tmp_path: Path) -> Path:
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_a.py").write_text(
        "import time, pytest\n\n"
        f"@pytest.mark.parametrize('i', range({FILLER}))\n"
        "def test_filler(i):\n    time.sleep(0.005)\n"
    )
    (suite / "test_b.py").write_text("def test_b():\n    assert True\n")
    (suite / "test_c.py").write_text("def test_c():\n    assert True\n")
    return suite


def _pytest(suite: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(SCRIPTS)}
    for inherited in INHERITED_PYTEST_STATE:
        env.pop(inherited, None)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "pytest_run_first", "-o", "addopts=", *args],
        cwd=suite,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _executed(result: subprocess.CompletedProcess[str]) -> list[str]:
    """Test files in the order a serial `-v` run executed them, deduplicated."""
    order: list[str] = []
    for match in re.finditer(r"^(test_\w+\.py)::", result.stdout, re.MULTILINE):
        if match.group(1) not in order:
            order.append(match.group(1))
    return order


def _write(path: Path, *lines: str) -> str:
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return str(path)


def test_the_listed_files_run_before_the_rest(suite: Path, tmp_path: Path):
    listed = _write(tmp_path / "first.txt", "test_c.py")

    result = _pytest(suite, "-v", "-p", "no:xdist", f"--run-first={listed}")

    assert result.returncode == 0, result.stdout + result.stderr
    assert _executed(result) == ["test_c.py", "test_a.py", "test_b.py"]


def test_the_lists_order_wins_over_collection_order(suite: Path, tmp_path: Path):
    """The selector lists closest first; the plugin must not re-sort by path."""
    listed = _write(tmp_path / "first.txt", "test_c.py", "test_b.py")

    result = _pytest(suite, "-v", "-p", "no:xdist", f"--run-first={listed}")

    assert _executed(result) == ["test_c.py", "test_b.py", "test_a.py"]


def test_an_empty_list_keeps_collection_order(suite: Path, tmp_path: Path):
    listed = _write(tmp_path / "first.txt")

    result = _pytest(suite, "-v", "-p", "no:xdist", f"--run-first={listed}")

    assert result.returncode == 0, result.stdout + result.stderr
    assert _executed(result) == ["test_a.py", "test_b.py", "test_c.py"]


def test_last_runs_failures_still_go_first(suite: Path, tmp_path: Path):
    """`--ff` wraps the reorder: a re-push after a fix answers on the old failure first."""
    (suite / "test_b.py").write_text("def test_b():\n    assert False\n")
    first = _pytest(suite, "-q", "-p", "no:xdist")
    assert first.returncode == 1, first.stdout + first.stderr
    listed = _write(tmp_path / "first.txt", "test_c.py")

    result = _pytest(suite, "-v", "-p", "no:xdist", "--ff", f"--run-first={listed}")

    assert _executed(result) == ["test_b.py", "test_c.py", "test_a.py"]


def test_under_xdist_a_listed_failure_stops_the_run_early(suite: Path, tmp_path: Path):
    """The pre-push shape: `-x -n 2 --maxschedchunk=1`. The failure is collected last; listed, it runs first."""
    (suite / "test_c.py").write_text("def test_c():\n    assert False\n")
    listed = _write(tmp_path / "first.txt", "test_c.py")

    unordered = _pytest(suite, "-q", "-x", "-n", "2", "--maxschedchunk=1")
    ordered = _pytest(suite, "-q", "-x", "-n", "2", "--maxschedchunk=1", f"--run-first={listed}")

    # xdist reports a `-x` stop as an interruption (2), serial pytest as a failure (1).
    for result in (unordered, ordered):
        assert "1 failed" in result.stdout, result.stdout + result.stderr
    assert _passed(unordered) == FILLER + 1, unordered.stdout
    assert _passed(ordered) < FILLER // 2, ordered.stdout


def _passed(result: subprocess.CompletedProcess[str]) -> int:
    match = re.search(r"(\d+) passed", result.stdout)
    return int(match.group(1)) if match else 0


def test_an_unreadable_list_is_a_usage_error(suite: Path, tmp_path: Path):
    """A full run that silently ignored its order would look like one that had none."""
    missing = tmp_path / "nope.txt"

    result = _pytest(suite, "-q", "-p", "no:xdist", f"--run-first={missing}")

    assert result.returncode == pytest.ExitCode.USAGE_ERROR, result.stdout + result.stderr
    assert str(missing) in result.stderr
