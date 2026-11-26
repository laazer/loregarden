"""No hook-driven server test run passes its wall-clock ceiling."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from tests.repo_paths import REPO_ROOT

_SCRIPTS = REPO_ROOT / ".lefthook" / "scripts"
_WALL_LIMIT = _SCRIPTS / "wall_limit.py"
_SCRUBBED_ENV = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}


def _load():
    spec = importlib.util.spec_from_file_location("wall_limit", _WALL_LIMIT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


wall_limit = _load()


def _run(*args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_WALL_LIMIT), *args],
        capture_output=True,
        text=True,
        env=_SCRUBBED_ENV,
        timeout=timeout,
        check=False,
    )


def test_a_command_under_the_limit_keeps_its_own_exit_code():
    assert _run("--seconds", "30", "--", "sh", "-c", "exit 3").returncode == 3
    assert _run("--seconds", "30", "--", "true").returncode == 0


def test_a_run_past_the_limit_is_stopped_and_names_its_slowest_files(tmp_path: Path):
    durations = tmp_path / "durations.json"
    durations.write_text(
        json.dumps(
            {
                "files": {
                    "tests/test_fast.py": 1.0,
                    "tests/test_slow.py": 900.0,
                    "tests/test_mid.py": 40.0,
                },
                "running": {"tests/test_hang.py::test_forever": time.time() - 600},
            }
        ),
        encoding="utf-8",
    )
    child_pid = tmp_path / "child.pid"

    result = _run(
        "--seconds", "2", "--durations", str(durations), "--",
        "sh", "-c", f'sleep 120 & echo $! > "{child_pid}"; wait',
    )  # fmt: skip

    assert result.returncode == wall_limit.EXIT_TIMED_OUT
    assert "STOPPED after" in result.stderr and "(2s)" in result.stderr
    lines = [line for line in result.stderr.splitlines() if "tests/test_" in line]
    assert [line.split()[-1] for line in lines] == [
        "tests/test_slow.py",
        "tests/test_mid.py",
        "tests/test_fast.py",
        "tests/test_hang.py::test_forever",
    ]
    assert "still running" in result.stderr
    # The whole group goes, not only the direct child: xdist workers are grandchildren.
    pid = int(child_pid.read_text(encoding="utf-8"))
    time.sleep(0.2)
    assert (
        subprocess.run(["kill", "-0", str(pid)], capture_output=True, check=False).returncode != 0
    )


def test_time_already_spent_counts_against_the_limit():
    # 99 of 100 seconds spent: a 60 s sleep is stopped, which it would not be otherwise.
    result = _run("--seconds", "100", "--elapsed", "99", "--", "sleep", "60")
    assert result.returncode == wall_limit.EXIT_TIMED_OUT
    assert "STOPPED after" in result.stderr


def test_a_stop_with_no_recorded_times_says_so(tmp_path: Path):
    result = _run(
        "--seconds", "1", "--durations", str(tmp_path / "never-written.json"), "--", "sleep", "60"
    )  # fmt: skip
    assert result.returncode == wall_limit.EXIT_TIMED_OUT
    assert "no per-file times were recorded" in result.stderr


def test_the_durations_plugin_totals_each_file(tmp_path: Path):
    (tmp_path / "test_one.py").write_text(
        "import time\n\ndef test_a():\n    time.sleep(0.2)\n\ndef test_b():\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "test_two.py").write_text("def test_c():\n    pass\n", encoding="utf-8")
    out = tmp_path / "durations.json"

    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "pytest_file_durations",
            "-p",
            "no:cacheprovider",
        ],
        cwd=tmp_path,
        env=_SCRUBBED_ENV
        | {"PYTHONPATH": str(_SCRIPTS), "LOREGARDEN_FILE_DURATIONS_FILE": str(out)},
        check=True,
        capture_output=True,
    )

    recorded = json.loads(out.read_text(encoding="utf-8"))
    assert recorded["running"] == {}, "every test finished, so none is still running"
    totals = recorded["files"]
    assert set(totals) == {"test_one.py", "test_two.py"}
    assert totals["test_one.py"] >= 0.2 > totals["test_two.py"]


def test_every_hook_pytest_run_is_under_the_wall_limit():
    text = (_SCRIPTS / "server-tests.sh").read_text(encoding="utf-8")
    runs = [line for line in text.splitlines() if "run_and_judge pytest" in line]
    assert len(runs) == 2
    assert all('-- "${WALL_LIMIT[@]}" env ' in line for line in runs)
    assert text.count("-p pytest_file_durations") == 2
    assert "LOREGARDEN_TEST_WALL_LIMIT_SECONDS:-1800" in text
