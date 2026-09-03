"""A gate that could not run must not be reported as a gate that found something.

The three Python gate scripts need 3.11 — `py_string_vocab` reads `ast.Match`
to see `match` statements in graded code, and `py_git_subprocess_check`
evaluates PEP 604 annotations at import. On an older interpreter both raise at
module level, before any file is examined, and exit 1.

Exit 1 is also what a real violation returns, and `gate_runner._run_command`
reads nothing else. So every orchestration transition on a host whose `python3`
predates 3.11 reported a phantom violation and sent the autofix loop to repair
it. The orchestration profiles invoked exactly that bare `python3` (657).

The coverage here is deliberately split so that the one test needing a real old
interpreter is the *least* load-bearing: the refusal, the runner mapping, the
wiring of every entry point, and the profile commands are all asserted without
one. Only "a genuine 3.9 behaves the way the injected version tuple predicts"
depends on the host, and that is the part least likely to drift.
"""

from __future__ import annotations

import ast
import importlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from loregarden.models.domain import GateOutcome
from loregarden.services import gate_runner

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / ".lefthook" / "scripts"

#: The scripts an orchestration profile or lefthook runs directly, each of which
#: crashes at import on an interpreter below the floor.
GUARDED_ENTRY_POINTS = [
    "py_organization_check.py",
    "py_silent_except_check.py",
    "py_git_subprocess_check.py",
]

#: Modules whose import is what actually raises on an old interpreter. The guard
#: has to run before any of them.
SIBLING_MODULES = {"precommit_git_diff", "py_string_vocab"}


def _guard():
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    return importlib.import_module("gate_python_guard")


def test_the_guard_refuses_an_old_interpreter_with_ex_unavailable() -> None:
    """The refusal carries a code the runner can act on, not just prose."""
    guard = _guard()

    with pytest.raises(SystemExit) as excinfo:
        guard.require_supported_python("py_organization_check.py", version=(3, 9, 7))

    assert excinfo.value.code == guard.EX_UNAVAILABLE


def test_the_refusal_says_it_examined_nothing() -> None:
    """An operator reading the gate output must not read it as a finding.

    Asserted on the claim, not the wording: the message has to name the version
    that ran and the floor it missed, because the usual cause is a `python3` on
    PATH that is not the project's.
    """
    guard = _guard()

    message = guard.format_refusal("py_organization_check.py", (3, 9, 7))

    assert "3.9.7" in message
    assert "3.11" in message
    assert "examined nothing" in message


def test_the_guard_admits_the_interpreter_the_suite_runs_on() -> None:
    """The floor must not be so high it refuses the environment it ships in."""
    guard = _guard()

    guard.require_supported_python("py_organization_check.py")  # must not raise

    assert sys.version_info[:2] >= guard.MIN_PYTHON[:2]


def test_the_guard_runs_on_the_interpreters_it_rejects() -> None:
    """The refusal is worthless if delivering it needs the missing version.

    Parsed under an old grammar rather than executed, because the point is that
    the module carries no syntax the rejected interpreter cannot read. `ast` on
    3.11 cannot emulate a 3.7 parser, so this asserts the narrower property the
    module actually needs: no PEP 604 unions, no `match`, no walrus in it.
    """
    source = (_SCRIPTS / "gate_python_guard.py").read_text()
    tree = ast.parse(source)

    offenders = [
        type(node).__name__
        for node in ast.walk(tree)
        if isinstance(node, (ast.Match, ast.NamedExpr))
        or (isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr))
    ]

    assert offenders == [], offenders


@pytest.mark.parametrize("script_name", GUARDED_ENTRY_POINTS)
def test_every_gate_entry_point_consults_the_guard_before_a_sibling_import(
    script_name: str,
) -> None:
    """Wiring, not intent: a guard imported after the crash is no guard.

    This is what stops the next gate script from quietly skipping the floor —
    the failure mode is silent, because on a 3.11 host an unguarded script
    behaves identically.
    """
    tree = ast.parse((_SCRIPTS / script_name).read_text())

    guard_call_line = min(
        (
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "require_supported_python"
        ),
        default=None,
    )
    sibling_import_line = min(
        (
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module in SIBLING_MODULES
        ),
        default=None,
    )

    assert guard_call_line is not None, f"{script_name} never calls the guard"
    assert sibling_import_line is not None, f"{script_name} imports no guarded sibling"
    assert guard_call_line < sibling_import_line, (
        f"{script_name}: guard at line {guard_call_line} runs after the sibling "
        f"import at line {sibling_import_line}, so the crash happens first"
    )


def test_the_runner_constant_agrees_with_the_guard() -> None:
    """Two constants, one contract. Drift here silently restores the old bug."""
    assert gate_runner.GATE_EX_UNAVAILABLE == _guard().EX_UNAVAILABLE


def _stub_gate(tmp_path: Path, exit_code: int) -> str:
    script = tmp_path / f"stub_{exit_code}.py"
    script.write_text(f"import sys\nsys.stderr.write('stub gate\\n')\nsys.exit({exit_code})\n")
    return f"{sys.executable} {script}"


def test_the_runner_maps_ex_unavailable_to_unavailable(tmp_path: Path) -> None:
    """The mapping, driven through the real `_run_command`."""
    result = gate_runner._run_command(_stub_gate(tmp_path, 69), tmp_path)

    assert result.outcome is GateOutcome.UNAVAILABLE
    assert result.ok is False


def test_the_runner_still_maps_a_plain_failure_to_failed(tmp_path: Path) -> None:
    """The control. Without it the test above passes for a runner that calls
    every non-zero exit unavailable, which would disable gating entirely."""
    result = gate_runner._run_command(_stub_gate(tmp_path, 1), tmp_path)

    assert result.outcome is GateOutcome.FAILED
    assert result.ok is False


@pytest.mark.parametrize("profile", ["default", "loregarden", "blobert"])
def test_orchestration_profiles_route_python_gates_through_the_wrapper(profile: str) -> None:
    """A refusal is the fallback; resolving the right interpreter is the fix.

    `server_python.sh` already resolves `server/.venv/bin/python` (else `uv run
    --project server`), and lefthook has used it all along. The orchestration
    surface called a bare `python3` instead, which is the whole defect.
    """
    config = yaml.safe_load(
        (_REPO_ROOT / "agent_context" / "orchestration" / f"{profile}.yaml").read_text()
    )
    commands = config["gates"]["commands"]

    python_gates = [c for c in commands if ".lefthook/scripts/py_" in c]

    assert python_gates, f"{profile}.yaml runs no Python gate"
    for command in python_gates:
        assert "server_python.sh" in command, command
        assert not command.strip().startswith("python3 "), command


def _old_interpreters() -> list[str]:
    found = []
    for name in ("python3.8", "python3.9", "python3.10"):
        path = shutil.which(name)
        if path is None:
            continue
        probe = subprocess.run(
            [path, "-c", "import sys; print(sys.version_info[:2] < (3, 11))"],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.stdout.strip() == "True":
            found.append(path)
    return found


@pytest.mark.parametrize("script_name", GUARDED_ENTRY_POINTS)
def test_a_real_old_interpreter_refuses_rather_than_crashing(
    script_name: str, tmp_path: Path
) -> None:
    """End to end, on a genuine sub-3.11 interpreter when the host has one.

    This is the only host-dependent test here, and deliberately the one carrying
    the least weight — everything it would catch except "a real old interpreter
    matches the injected tuple" is already asserted deterministically above.
    """
    interpreters = _old_interpreters()
    if not interpreters:
        pytest.skip("host has no interpreter below 3.11; the deterministic tests cover the rest")

    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)

    for interpreter in interpreters:
        completed = subprocess.run(
            [
                interpreter,
                str(_SCRIPTS / script_name),
                "--repo",
                str(tmp_path),
                "--scope",
                "worktree",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        assert completed.returncode == _guard().EX_UNAVAILABLE, (
            f"{script_name} under {interpreter}: exit {completed.returncode}\n{completed.stderr}"
        )
        assert "Traceback" not in completed.stderr, completed.stderr
