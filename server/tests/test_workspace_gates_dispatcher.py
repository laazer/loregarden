"""`workspace-gates.sh`: the one list of gates other workspaces run.

Its contract is what the managed pre-commit block and every orchestration
profile depend on: each gate sees only its own language's files, every gate runs
even after one fails, and the exit code keeps "could not run" (69) apart from
"failed" (1) so the orchestration runner routes each to the right handler.

The checkers are replaced with stubs that log their argv and exit with a code
read from the environment, so these pin the dispatcher, not the rules.
"""

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_DISPATCHER = _ROOT / ".lefthook" / "scripts" / "workspace-gates.sh"
_EX_UNAVAILABLE = 69

_PY_STUB = """import os, sys
from pathlib import Path
name = Path(__file__).name
with open(os.environ["GATE_LOG"], "a") as log:
    log.write(name + " " + " ".join(sys.argv[1:]) + "\\n")
sys.exit(int(os.environ.get("EXIT_" + name.split(".")[0], "0")))
"""

_TS_STUB = """const fs = require("fs");
const path = require("path");
const name = path.basename(__filename);
fs.appendFileSync(process.env.GATE_LOG, name + " " + process.argv.slice(2).join(" ") + "\\n");
process.exit(Number(process.env["EXIT_" + name.split(".")[0]] || "0"));
"""

#: Stands in for server_python.sh and records that it was the route taken.
_RUNNER_STUB = """#!/usr/bin/env bash
echo "via-server-python $(basename "$1")" >> "$GATE_LOG"
exec "{python}" "$@"
"""


def _gates() -> list[str]:
    return subprocess.run(
        ["bash", str(_DISPATCHER), "--list"], capture_output=True, text=True, check=True
    ).stdout.split()


@pytest.fixture
def stubbed(tmp_path: Path) -> Path:
    """A copy of the dispatcher beside stub checkers, so it runs nothing real."""
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(_DISPATCHER, scripts / _DISPATCHER.name)
    (scripts / "server_python.sh").write_text(_RUNNER_STUB.format(python=sys.executable))
    for gate in _gates():
        (scripts / gate).write_text(_PY_STUB if gate.endswith(".py") else _TS_STUB)
    return scripts


def _run(scripts: Path, *args: str, exits: dict[str, int] | None = None):
    log = scripts.parent / "gate.log"
    log.unlink(missing_ok=True)
    env = {**os.environ, "GATE_LOG": str(log)}
    for gate, code in (exits or {}).items():
        env[f"EXIT_{gate.split('.')[0]}"] = str(code)
    result = subprocess.run(
        ["bash", str(scripts / _DISPATCHER.name), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return result, calls


def test_lists_the_five_workspace_gates():
    assert _gates() == [
        "py_organization_check.py",
        "py_silent_except_check.py",
        "ts_organization_check.cjs",
        "ts_no_silent_failures_check.cjs",
        "ts_ux_states_check.cjs",
    ]


def test_every_listed_gate_exists():
    scripts = _DISPATCHER.parent
    assert [gate for gate in _gates() if not (scripts / gate).is_file()] == []


def test_each_gate_sees_only_its_own_languages_files(stubbed: Path):
    result, calls = _run(stubbed, "a.py", "b.ts", "c.tsx", "notes.md")

    assert result.returncode == 0, result.stdout
    gate_calls = [c for c in calls if not c.startswith("via-")]
    assert gate_calls == [
        "py_organization_check.py a.py",
        "py_silent_except_check.py a.py",
        "ts_organization_check.cjs b.ts c.tsx",
        "ts_no_silent_failures_check.cjs b.ts c.tsx",
        "ts_ux_states_check.cjs b.ts c.tsx",
    ]


def test_python_gates_run_under_loregardens_interpreter(stubbed: Path):
    """Both checkers need >=3.11; a bare `python3` resolves against the target
    workspace's PATH, which handed blobert 3.10 and a commit blocked by exit 69."""
    _, calls = _run(stubbed, "a.py")
    assert calls[0] == "via-server-python py_organization_check.py"
    assert calls[2] == "via-server-python py_silent_except_check.py"


def test_a_gate_with_no_files_of_its_language_is_skipped(stubbed: Path):
    result, calls = _run(stubbed, "notes.md")
    assert (result.returncode, calls) == (0, [])


def test_scope_flags_reach_every_gate(stubbed: Path):
    result, calls = _run(stubbed, "--repo", "/w", "--scope", "worktree")

    assert result.returncode == 0
    gate_calls = [c for c in calls if not c.startswith("via-")]
    assert gate_calls == [f"{gate} --repo /w --scope worktree" for gate in _gates()]


def test_every_gate_runs_after_one_fails(stubbed: Path):
    result, calls = _run(stubbed, "a.py", "b.ts", exits={"py_organization_check.py": 1})

    assert result.returncode == 1
    assert len([c for c in calls if not c.startswith("via-")]) == 5


def test_unavailable_alone_exits_unavailable(stubbed: Path):
    """69 routes to a human; folding it into 1 sends an agent after violations
    that were never found."""
    result, _ = _run(stubbed, "a.py", exits={"py_silent_except_check.py": _EX_UNAVAILABLE})
    assert result.returncode == _EX_UNAVAILABLE


def test_a_real_failure_outranks_an_unavailable_gate(stubbed: Path):
    result, _ = _run(
        stubbed,
        "a.py",
        "b.ts",
        exits={"py_silent_except_check.py": _EX_UNAVAILABLE, "ts_ux_states_check.cjs": 1},
    )
    assert result.returncode == 1


def test_gate_output_reaches_stdout(stubbed: Path, tmp_path: Path):
    """The orchestration runner reports stderr *instead of* stdout when both
    exist, so one gate's warning on stderr would hide another's findings."""
    (stubbed / "ts_ux_states_check.cjs").write_text(
        'console.error("x.ts:3: finding"); process.exit(1);\n'
    )
    result, _ = _run(stubbed, "x.ts")
    assert "x.ts:3: finding" in result.stdout
    assert result.stderr == ""


@pytest.mark.parametrize("args", [[], ["--bogus"], ["--repo"]])
def test_bad_invocations_are_usage_errors(args: list[str]):
    result = subprocess.run(
        ["bash", str(_DISPATCHER), *args], capture_output=True, text=True, check=False
    )
    assert result.returncode == 2


# --------------------------------------------------------------------------- #
# --all: which workspaces the installers serve
# --------------------------------------------------------------------------- #


@pytest.fixture
def loregarden_root(tmp_path: Path) -> Path:
    root = tmp_path / "loregarden"
    (root / "data").mkdir(parents=True)
    with sqlite3.connect(root / "data" / "loregarden.db") as db:
        db.execute("CREATE TABLE workspaces (slug TEXT, repo_path TEXT, archived_at TEXT)")
        db.executemany(
            "INSERT INTO workspaces VALUES (?, ?, ?)",
            [
                ("loregarden", ".", None),
                ("branch", str(root / ".claude" / "worktrees" / "x"), None),
                ("blobert", str(tmp_path / "blobert"), None),
                ("gone", str(tmp_path / "gone"), "2026-01-01"),
                ("rel", "../rel", None),
            ],
        )
    return root


def _list_roots(loregarden_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(_ROOT / "scripts" / "list_workspace_roots.py"),
            "--loregarden-root",
            str(loregarden_root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_all_lists_live_workspaces_other_than_loregarden(loregarden_root: Path, tmp_path: Path):
    result = _list_roots(loregarden_root)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"blobert\t{(tmp_path / 'blobert').resolve()}",
        f"rel\t{(tmp_path / 'rel').resolve()}",
    ]


def test_all_without_a_database_fails_loudly(tmp_path: Path):
    """An empty listing would read as "no workspaces" and install nothing."""
    result = _list_roots(tmp_path)
    assert result.returncode == 1
    assert result.stdout == ""
    assert not (tmp_path / "data" / "loregarden.db").exists()
