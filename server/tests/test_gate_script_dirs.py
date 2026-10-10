"""The Python gates grade the repo's own scripts outside server/.

`.claude/hooks/pr_ux_section_precheck.py` was committed with six ruff findings
while every Python gate printed "(skip) no files for inspection": the lefthook
globs named `server/**/*.py`, and `py-staged-paths.sh` mapped anything outside
server/ to nothing, so widening a glob alone still checked nothing. Two of the
diff filters then dropped what did reach them, because they looked the finding
up as `server/../<path>`, which no staged diff names.

These run each gate the way its caller does — lefthook's script on a staged
path, or the transition gate at `--scope worktree` — against a throwaway
repository holding a deliberately bad script in each directory.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / ".lefthook" / "scripts"
_VENV = _ROOT / "server" / ".venv"


def _script_dirs() -> list[str]:
    """PY_SCRIPT_DIRS, read from the shell file that owns it."""
    out = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; printf "%s\\n" "${PY_SCRIPT_DIRS[@]}"',
            "_",
            str(_SCRIPTS / "py-staged-paths.sh"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.split()


#: Stated here rather than read from the shell file: parametrizing over what
#: the file says would turn an emptied list into skipped tests, not failures.
SCRIPT_DIRS = [".claude/hooks", ".lefthook/scripts", ".github/scripts"]

#: Every pre-commit command that grades Python. Each must reach every script
#: directory — through its glob, and through the script it runs.
PYTHON_PRECOMMIT_GATES = [
    "format-staged",
    "py-review",
    "py-pylint",
    "py-complexity",
    "py-organization",
    "py-defensive-normalization",
    "py-silent-except",
    "py-git-subprocess",
    "py-mypy",
]


def _scrubbed_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=_scrubbed_env())


def test_the_three_lists_of_script_directories_agree() -> None:
    """lefthook's globs, the path mapper, and the worktree-scope selector.

    Each was a separate place a directory could be left out, and leaving one
    out is silent: the gate prints "skip" or "examined 0" and exits 0.
    """
    sys.path.insert(0, str(_SCRIPTS))
    try:
        import py_organization_check
    finally:
        sys.path.remove(str(_SCRIPTS))

    assert _script_dirs() == SCRIPT_DIRS
    owned = {"/".join(parts) for parts in py_organization_check._OWNED_SCRIPT_DIR_PARTS}
    assert owned == set(SCRIPT_DIRS)

    commands = yaml.safe_load((_ROOT / "lefthook.yml").read_text())["pre-commit"]["commands"]
    for name in PYTHON_PRECOMMIT_GATES:
        glob = commands[name]["glob"]
        for directory in SCRIPT_DIRS:
            assert f"{directory}/*.py" in glob, f"{name}'s glob misses {directory}"


# --------------------------------------------------------------------------- #
# The pre-commit scripts, on a staged bad file
# --------------------------------------------------------------------------- #

#: Trips ruff (UP045 at py39 under the future import, F401), defensive
#: normalization, pylint's statement cap, and mypy — one finding per gate.
#: The normalization line is spliced in so this file does not trip that gate.
_DEFENSIVE_LINE = "    if str(value).strip()" + '.lower() == "yes":'
_LONG_BODY = "\n".join(f"    v{i} = {i}" for i in range(55))
BAD_SCRIPT = f"""from __future__ import annotations

import os
from typing import Optional


def pick(value: Optional[str]) -> int:
{_DEFENSIVE_LINE}
        return "not an int"
    return 0


def long_function() -> None:
{_LONG_BODY}
"""

#: C901 above the configured 10, in a function the diff adds.
COMPLEX_SCRIPT = (
    "def branchy(x: int) -> int:\n"
    + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(12))
    + "    return -1\n"
)


@pytest.fixture
def hook_repo(tmp_path: Path) -> Path:
    """A repository shaped like this one where the pre-commit scripts look.

    The scripts find `server/pyproject.toml` and `server/.venv` relative to
    themselves, so the copy carries both — the venv as a link, not a rebuild.
    """
    if not (_VENV / "bin" / "ruff").exists():
        message = f"the pre-commit scripts need {_VENV}; run `uv sync --extra dev` in server/"
        if os.environ.get("CI"):
            pytest.fail(message)
        pytest.skip(message)
    repo = tmp_path / "repo"
    shutil.copytree(_SCRIPTS, repo / ".lefthook" / "scripts")
    (repo / "server").mkdir()
    shutil.copy(_ROOT / "server" / "pyproject.toml", repo / "server" / "pyproject.toml")
    (repo / "server" / ".venv").symlink_to(_VENV)
    (repo / ".gitignore").write_text("server/.venv\nserver/.pylint_home\n")
    _git(repo, "init", "-q", "-b", "main", ".")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return repo


def _stage(repo: Path, relpath: str, body: str) -> None:
    target = repo / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)
    _git(repo, "add", relpath)


def _run_hook(repo: Path, script: str, *paths: str) -> subprocess.CompletedProcess:
    """`bash .lefthook/scripts/<script> {staged_files}`, as lefthook.yml runs it."""
    return subprocess.run(
        ["bash", f".lefthook/scripts/{script}", *paths],
        cwd=repo,
        capture_output=True,
        text=True,
        env=_scrubbed_env(),
        timeout=300,
    )


def _out(result: subprocess.CompletedProcess) -> str:
    return result.stdout + result.stderr


#: (script, what must appear in its output) for the bad file.
PRECOMMIT_FINDINGS = [
    pytest.param("py-review.sh", "UP045", id="py-review"),
    pytest.param("py-pylint.sh", "too-many-statements", id="py-pylint"),
    pytest.param("detect-defensive-normalization.sh", "Defensive", id="defensive-norm"),
    pytest.param("mypy-changed.sh", "return-value", id="py-mypy"),
]


@pytest.mark.parametrize("directory", SCRIPT_DIRS)
@pytest.mark.parametrize(("script", "finding"), PRECOMMIT_FINDINGS)
def test_a_precommit_gate_fails_a_bad_script_outside_server(
    hook_repo: Path, directory: str, script: str, finding: str
) -> None:
    relpath = f"{directory}/planted.py"
    _stage(hook_repo, relpath, BAD_SCRIPT)

    result = _run_hook(hook_repo, script, relpath)

    assert result.returncode != 0, _out(result)
    assert finding in _out(result), _out(result)


@pytest.mark.parametrize("directory", SCRIPT_DIRS)
def test_the_complexity_filter_keeps_a_finding_outside_server(
    hook_repo: Path, directory: str
) -> None:
    """It used to map the finding to a bare file name and drop it as untouched."""
    relpath = f"{directory}/planted.py"
    _stage(hook_repo, relpath, COMPLEX_SCRIPT)

    result = _run_hook(hook_repo, "py-complexity.sh", relpath)

    assert result.returncode != 0, _out(result)
    assert "branchy" in _out(result), _out(result)


def test_format_staged_formats_a_script_with_the_server_config(hook_repo: Path) -> None:
    """Line length 100 from server/pyproject.toml, not ruff's default 88.

    Without `--config` a script outside server/ finds no config in its
    ancestors and is formatted to defaults, which py-review then disagrees with.
    """
    relpath = ".claude/hooks/planted.py"
    call = "result = some_function_name(argument_one, argument_two, argument_three, arg_four_xxxxx)"
    assert 88 < len(call) + 4 <= 100
    _stage(hook_repo, relpath, f"def f():\n    {call}\n    return  result\n")

    result = _run_hook(hook_repo, "format-staged.sh", relpath)

    assert result.returncode == 0, _out(result)
    assert (hook_repo / relpath).read_text() == f"def f():\n    {call}\n    return result\n"


def test_a_clean_script_passes_py_review(hook_repo: Path) -> None:
    """The mapping must not turn every script into a failure either."""
    relpath = ".claude/hooks/planted.py"
    _stage(hook_repo, relpath, "from __future__ import annotations\n\nX: str | None = None\n")

    result = _run_hook(hook_repo, "py-review.sh", relpath)

    assert result.returncode == 0, _out(result)
    assert "All checks passed" in _out(result), _out(result)


# --------------------------------------------------------------------------- #
# The transition gates, at --scope worktree
# --------------------------------------------------------------------------- #

TRANSITION_GATES = [
    pytest.param(
        "py_organization_check.py",
        "def read(payload):\n    return isinstance(payload, dict)\n",
        id="py-org",
    ),
    pytest.param(
        "py_silent_except_check.py",
        "def read(path):\n    try:\n        return path.read_text()\n"
        "    except Exception:\n        return None\n",
        id="py-silent-except",
    ),
    pytest.param(
        "py_git_subprocess_check.py",
        "import subprocess\n\n\ndef show(repo):\n"
        '    return subprocess.run(["git", "status"], cwd=repo, check=False)\n',
        id="py-git-subprocess",
    ),
]


@pytest.mark.parametrize("directory", SCRIPT_DIRS)
@pytest.mark.parametrize(("gate", "body"), TRANSITION_GATES)
def test_a_transition_gate_grades_a_script_outside_the_source_root(
    hook_repo: Path, directory: str, gate: str, body: str
) -> None:
    """Discovery confines a worktree run to the source root (server/ here);
    the repo's script directories are the exception."""
    (hook_repo / "server" / "pkg").mkdir()
    (hook_repo / "server" / "pkg" / "__init__.py").write_text("")
    _git(hook_repo, "add", "-A")
    _git(hook_repo, "commit", "-qm", "source root")
    _stage(hook_repo, f"{directory}/planted.py", body)

    result = subprocess.run(
        [sys.executable, str(_SCRIPTS / gate), "--repo", str(hook_repo), "--scope", "worktree"],
        capture_output=True,
        text=True,
        env=_scrubbed_env(),
        timeout=120,
    )

    assert result.returncode == 1, _out(result)
    assert "planted.py" in _out(result), _out(result)
