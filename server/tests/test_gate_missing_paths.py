"""A gate handed a path that does not exist must not report a pass.

lefthook's `{staged_files}` lists deletions, so every gate skips a missing
explicit path — and used to skip *any* missing path that way. A typo, or a file
list that reached argv as one word (zsh does not split an unquoted `$files`),
was therefore "deleted": the TypeScript gates printed a plain "passed", the
organization gates "skipping 1 deleted file(s) … examined 0 file(s)", and every
one exited 0 having read nothing.

The rule now: a missing path is a deletion only if HEAD tracks it. Anything else
fails the run. Black-box over each gate's CLI, because the rule lives in two
shared helpers (precommit_git_diff.py and ts_git_diff.cjs) and a gate that
bypasses them should fail here rather than quietly keep the old behaviour.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from tests.repo_templates import from_template

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / ".lefthook" / "scripts"
_TS_PARSER = _ROOT / "client" / "node_modules" / "@typescript-eslint" / "typescript-estree"

PY_BODY = "x = 1\n"
TS_BODY = "export const x = 1;\n"

GATES = [
    pytest.param(
        [sys.executable, str(_SCRIPTS / "py_organization_check.py")], "src/pkg/m.py", id="py-org"
    ),
    pytest.param(
        [sys.executable, str(_SCRIPTS / "py_silent_except_check.py")],
        "src/pkg/m.py",
        id="py-silent",
    ),
    pytest.param(
        [sys.executable, str(_SCRIPTS / "py_git_subprocess_check.py")], "src/pkg/m.py", id="py-git"
    ),
    pytest.param(
        ["node", str(_SCRIPTS / "ts_organization_check.cjs")], "client/src/m.ts", id="ts-org"
    ),
    pytest.param(["node", str(_SCRIPTS / "ts_ux_states_check.cjs")], "client/src/m.ts", id="ts-ux"),
    pytest.param(["node", str(_SCRIPTS / "ts_theme_check.cjs")], "client/src/m.ts", id="ts-theme"),
    pytest.param(
        ["node", str(_SCRIPTS / "ts_no_silent_failures_check.cjs")],
        "client/src/m.ts",
        id="ts-silent",
    ),
]


def _env() -> dict[str, str]:
    # GIT_DIR/GIT_WORK_TREE beat cwd, and a run nested in a worktree's hook
    # inherits them pointing at the real repository.
    return {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=_env())


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A committed repository holding one Python and one TypeScript module.

    Built in a fixture so a broken setup is an ERROR, never a gate that
    happened to find nothing. A copy of one built once per process
    (`tests/repo_templates.py`).
    """
    return from_template(tmp_path / "repo", "gate_missing_paths", _build_repo)


def _build_repo(root: Path) -> None:
    root.mkdir()
    _git(root, "init", "-q", "-b", "main", ".")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "m.py").write_text(PY_BODY)
    (root / "client" / "src").mkdir(parents=True)
    (root / "client" / "src" / "m.ts").write_text(TS_BODY)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")


def _run(gate: list[str], repo: Path, *paths: str) -> subprocess.CompletedProcess:
    if gate[0] == "node" and not _TS_PARSER.is_dir():
        message = f"the TS gates need {_TS_PARSER}; run `npm ci` in client/"
        if os.environ.get("CI"):
            pytest.fail(message)
        pytest.skip(message)
    return subprocess.run(
        [*gate, "--repo", str(repo), "--scope", "staged", *paths],
        capture_output=True,
        text=True,
        cwd=repo,
        env=_env(),
    )


def _out(result: subprocess.CompletedProcess) -> str:
    return result.stdout + result.stderr


@pytest.mark.parametrize(("gate", "relpath"), GATES)
def test_an_existing_file_is_examined(repo: Path, gate: list[str], relpath: str) -> None:
    """Control: the same invocation over a real file passes, so the failures
    below are about the missing path and not about the harness."""
    result = _run(gate, repo, relpath)
    assert result.returncode == 0, _out(result)


@pytest.mark.parametrize(("gate", "relpath"), GATES)
def test_a_path_head_never_had_fails_the_run(repo: Path, gate: list[str], relpath: str) -> None:
    missing = relpath.replace("m.", "typo.")
    result = _run(gate, repo, missing)
    assert result.returncode == 1, _out(result)
    assert "not deletions of a tracked file" in _out(result), _out(result)


@pytest.mark.parametrize(("gate", "relpath"), GATES)
def test_a_file_list_glued_into_one_argument_fails_the_run(
    repo: Path, gate: list[str], relpath: str
) -> None:
    """The zsh shape: `gate $files` with files="a b" arrives as one path."""
    result = _run(gate, repo, f"{relpath} {relpath}")
    assert result.returncode == 1, _out(result)


@pytest.mark.parametrize(("gate", "relpath"), GATES)
def test_a_deleted_tracked_file_is_skipped_by_name(
    repo: Path, gate: list[str], relpath: str
) -> None:
    """The case the skip exists for: a commit that removes a file has nothing
    left to grade, and must not be refused for it."""
    _git(repo, "rm", "-q", relpath)
    result = _run(gate, repo, relpath)
    assert result.returncode == 0, _out(result)
    assert "skipping 1 deleted file(s): m." in _out(result), _out(result)


@pytest.mark.parametrize(("gate", "relpath"), GATES)
def test_one_unknown_path_among_real_ones_still_fails(
    repo: Path, gate: list[str], relpath: str
) -> None:
    """A real file beside the bad one must not carry the run to a pass."""
    result = _run(gate, repo, relpath, relpath.replace("m.", "typo."))
    assert result.returncode == 1, _out(result)
