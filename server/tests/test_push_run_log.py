"""Pre-push reports its time against a budget, and every full-suite run is counted."""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from tests.repo_paths import REPO_ROOT

_SCRIPTS = REPO_ROOT / ".lefthook" / "scripts"
_LOG_SCRIPT = _SCRIPTS / "push_run_log.py"
_SCRUBBED_ENV = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}


def _load():
    spec = importlib.util.spec_from_file_location("push_run_log", _LOG_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


push_run_log = _load()
DAY = push_run_log.DAY


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=_SCRUBBED_ENV)
    return tmp_path


def _entry(elapsed: int, *, reason: str = "", requested: bool = False, budget: int = 300) -> dict:
    return {
        "elapsed": elapsed,
        "budget": budget,
        "exit": 0,
        "files": 40,
        "full_suite_reason": reason,
        "requested": requested,
    }


def test_the_report_counts_fallbacks_and_the_median_over_the_window():
    now = 100 * DAY
    entries = [
        {"at": now - 1, **_entry(100)},
        {
            "at": now - 2,
            **_entry(200, reason="server/tests/conftest.py changes how every test runs"),
        },
        {"at": now - 3, **_entry(400, reason="LOREGARDEN_FULL_TESTS is set", requested=True)},
        {"at": now - 8 * DAY, **_entry(9000, reason="old gap")},
    ]

    summary = push_run_log.summarize(entries, now, 7)

    assert summary["runs"] == 3
    assert summary["median_seconds"] == 200
    assert summary["over_budget"] == 1
    assert summary["full_suite_fallbacks"] == 1
    assert summary["full_suite_requested"] == 1
    assert summary["fallback_reasons"] == ["server/tests/conftest.py changes how every test runs"]


def test_no_runs_reports_no_median_rather_than_zero():
    summary = push_run_log.summarize([], 100 * DAY, 7)
    assert summary["runs"] == 0 and summary["median_seconds"] is None


def test_recording_appends_and_drops_lines_past_the_keep_window(repo: Path):
    now = 100 * DAY
    push_run_log.record(repo, _entry(1), now - (push_run_log.KEEP_DAYS + 1) * DAY)
    push_run_log.record(repo, _entry(2), now - DAY)
    log = push_run_log.record(repo, _entry(3), now)

    assert log.parent == repo / ".git"
    assert [entry["elapsed"] for entry in push_run_log._entries(log)] == [2, 3]


def test_the_cli_records_a_fallback_and_reports_it(repo: Path):
    def cli(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(_LOG_SCRIPT), *args, "--repo", str(repo)],
            capture_output=True,
            text=True,
            env=_SCRUBBED_ENV,
            check=True,
        )

    cli(
        "record", "--elapsed", "500", "--budget", "300", "--exit", "1", "--full-suite-reason", "gap"
    )
    cli("record", "--elapsed", "60", "--budget", "300", "--exit", "0", "--files", "12")

    report = cli("report").stdout
    assert "2 run(s)" in report and "median 280s" in report
    assert "1 over budget" in report and "1 full-suite fallback(s)" in report
    assert "fallback: gap" in report


def _push_run_finish_body() -> str:
    text = (_SCRIPTS / "server-tests.sh").read_text(encoding="utf-8")
    match = re.search(r"^push_run_finish\(\) \{\n.*?^\}\n", text, re.M | re.S)
    assert match, "server-tests.sh defines push_run_finish"
    return match.group(0)


def _finish(repo: Path, elapsed: int, **env: str) -> subprocess.CompletedProcess:
    """push_run_finish as server-tests.sh defines it, `elapsed` seconds into a run."""
    script = (
        _push_run_finish_body()
        + f'LEFTHOOK_SCRIPTS="{_SCRIPTS}"; ROOT="$REPO"; PUSH_BUDGET_SECONDS=300\n'
        + f'RAN_FILES=0; FULL_SUITE_REASON="$REASON"; RUN_STARTED=$((SECONDS - {elapsed}))\n'
        + "push_run_finish 0\n"
    )
    return subprocess.run(
        ["bash", "-euc", script],
        capture_output=True,
        text=True,
        env=_SCRUBBED_ENV | {"REPO": str(repo), "REASON": "", "LOREGARDEN_TESTS_BASE": ""} | env,
        check=True,
    )


def test_server_tests_records_every_push_run_from_its_exit_trap():
    text = (_SCRIPTS / "server-tests.sh").read_text(encoding="utf-8")
    trap = next(line for line in text.splitlines() if line.startswith("trap "))
    assert 'push_run_finish "$rc"' in trap
    assert "FULL_SUITE_REASON=" in text and "RAN_FILES=" in text


def test_a_push_prints_its_time_against_the_budget(repo: Path):
    result = _finish(repo, 400, REASON="server/pyproject.toml is not Python")
    assert "took 400s of a 300s budget" in result.stdout
    assert "over budget by 100s" in result.stdout
    (entry,) = push_run_log._entries(repo / ".git" / push_run_log.LOG)
    assert entry["elapsed"] >= 400 and entry["budget"] == 300
    assert entry["full_suite_reason"] == "server/pyproject.toml is not Python"
    assert entry["requested"] is False


def test_a_full_run_asked_for_is_recorded_as_asked_for(repo: Path):
    _finish(repo, 5, REASON="LOREGARDEN_FULL_TESTS is set", LOREGARDEN_FULL_TESTS="1")
    (entry,) = push_run_log._entries(repo / ".git" / push_run_log.LOG)
    assert entry["requested"] is True


def test_a_landing_is_not_recorded_as_a_push(repo: Path):
    result = _finish(repo, 10, LOREGARDEN_TESTS_BASE="origin/main")
    assert result.stdout == ""
    assert not (repo / ".git" / push_run_log.LOG).exists()
