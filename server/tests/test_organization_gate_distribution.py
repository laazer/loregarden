"""The organization gates as workspace-agnostic tooling.

The same rules have to reach code two ways: a human's `git commit` (lefthook,
scoped to the index) and an agent's stage transition (an orchestration gate,
scoped to the working tree, in a workspace with a layout nobody promised us).
These pin the parts that make one script serve both.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / ".lefthook" / "scripts"


def _load(name: str, path: Path):
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


checker = _load("py_organization_check", _SCRIPTS / "py_organization_check.py")
installer = _load("install_workspace_hooks", _ROOT / "scripts" / "install_workspace_hooks.py")


def _git(repo: Path, *args: str) -> None:
    # Scrub GIT_DIR/GIT_WORK_TREE: they beat cwd, and a test run from a worktree
    # hook inherits them pointing at the real repo.
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", ".")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    return tmp_path


# --------------------------------------------------------------------------- #
# invocation modes
# --------------------------------------------------------------------------- #


def test_bare_file_list_is_precommit_mode():
    inv = checker.parse_argv(["prog", "server/loregarden/foo.py"])
    assert inv.diff_scope == "staged"
    assert inv.label == "pre-commit"
    assert [p.name for p in inv.files] == ["foo.py"]


def test_repo_and_scope_flags_are_gate_mode():
    inv = checker.parse_argv(["prog", "--repo", "/tmp/ws", "--scope", "worktree"])
    assert inv.diff_scope == "worktree"
    assert inv.label == "gate"
    assert inv.repo == Path("/tmp/ws").resolve()
    assert inv.files == []


def test_unknown_scope_falls_back_to_staged():
    assert checker.parse_argv(["prog", "--scope", "nonsense"]).diff_scope == "staged"


def test_branch_scope_takes_a_base_ref():
    inv = checker.parse_argv(
        ["prog", "--repo", "/tmp/ws", "--scope", "branch", "--base", "develop"]
    )
    assert (inv.diff_scope, inv.base_ref) == ("branch", "develop")


# --------------------------------------------------------------------------- #
# layout detection
# --------------------------------------------------------------------------- #


def test_conventional_layout_wins(tmp_path: Path):
    (tmp_path / "server" / "pkg").mkdir(parents=True)
    (tmp_path / "server" / "pkg" / "a.py").write_text("x = 1\n")
    assert checker.python_source_roots(tmp_path) == [tmp_path / "server"]


def test_monorepo_falls_back_to_each_project_root(tmp_path: Path):
    # No server/src/app at the top; two unrelated Python projects underneath.
    for project in ("tools/gen", "web/api"):
        (tmp_path / project / "pkg").mkdir(parents=True)
        (tmp_path / project / "pyproject.toml").write_text("[project]\n")
        (tmp_path / project / "pkg" / "mod.py").write_text("x = 1\n")
    changed = [tmp_path / "tools/gen/pkg/mod.py", tmp_path / "web/api/pkg/mod.py"]
    roots = checker.python_source_roots(tmp_path, changed)
    assert sorted(roots) == [tmp_path / "tools" / "gen", tmp_path / "web" / "api"]
    # Not the repo root: walking a monorepo per gate run cost 26s on blobert.
    assert tmp_path not in roots


def test_project_root_detection_stops_at_the_repo(tmp_path: Path):
    (tmp_path / "loose").mkdir()
    stray = tmp_path / "loose" / "mod.py"
    stray.write_text("x = 1\n")
    assert checker.python_source_roots(tmp_path, [stray]) == [tmp_path / "loose"]


# --------------------------------------------------------------------------- #
# worktree scope: what an agent actually leaves behind
# --------------------------------------------------------------------------- #

VIOLATION = """
def read(payload):
    return isinstance(payload, dict)
"""


def _run_gate(repo: Path, scope: str = "worktree") -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    return subprocess.run(
        [
            sys.executable,
            str(_SCRIPTS / "py_organization_check.py"),
            "--repo",
            str(repo),
            "--scope",
            scope,
        ],
        capture_output=True,
        text=True,
        env=env,
    )


def test_worktree_scope_reads_uncommitted_edits(repo: Path):
    src = repo / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "mod.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")

    (src / "mod.py").write_text(f"x = 1\n{VIOLATION}")
    result = _run_gate(repo)
    assert result.returncode == 1
    assert "isinstance" in result.stdout


def test_worktree_scope_reads_untracked_files(repo: Path):
    # The whole point: a file an agent just created is not in `git diff` at all,
    # and it is the least-reviewed code in the change.
    src = repo / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "keep.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")

    (src / "brand_new.py").write_text(VIOLATION)
    result = _run_gate(repo)
    assert result.returncode == 1
    assert "brand_new.py" in result.stdout


def test_clean_worktree_passes(repo: Path):
    src = repo / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "mod.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")

    result = _run_gate(repo)
    assert result.returncode == 0


def test_repo_without_python_is_a_no_op(repo: Path):
    (repo / "main.gd").write_text("extends Node\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    (repo / "other.gd").write_text("extends Node2D\n")
    assert _run_gate(repo).returncode == 0


# --------------------------------------------------------------------------- #
# hook installer
# --------------------------------------------------------------------------- #

LEFTHOOK = """pre-commit:
  parallel: true
  commands:
    existing-check:
      name: Something already here
      run: echo hi

pre-push:
  commands:
    tests:
      run: echo tests
"""


def _install(config: Path, check: bool = False) -> int:
    argv = ["--config", str(config), "--loregarden-root", str(_ROOT)]
    if check:
        argv.append("--check")
    saved = sys.argv
    sys.argv = ["install_workspace_hooks.py", *argv]
    try:
        return installer.main()
    finally:
        sys.argv = saved


def test_installer_namespaces_its_commands(tmp_path: Path):
    """A workspace with its own `py-organization` must keep it.

    blobert has one. An unnamespaced block would put two entries under the same
    key in one map — a duplicate YAML key that either fails to parse or silently
    keeps whichever the loader saw last, disabling a real gate.
    """
    yaml = pytest.importorskip("yaml")
    config = tmp_path / "lefthook.yml"
    config.write_text(LEFTHOOK.replace("existing-check:", "py-organization:"))

    assert _install(config) == 0
    commands = yaml.safe_load(config.read_text())["pre-commit"]["commands"]
    assert set(installer.MANAGED_COMMAND_NAMES) <= set(commands)
    assert commands["py-organization"]["run"] == "echo hi"


def test_installer_refuses_a_collision_on_its_own_names(tmp_path: Path):
    config = tmp_path / "lefthook.yml"
    original = LEFTHOOK.replace("existing-check:", installer.MANAGED_COMMAND_NAMES[0] + ":")
    config.write_text(original)

    assert _install(config) == 1
    assert config.read_text() == original


def test_glob_matches_root_level_and_nested_files():
    """`**/*.py` alone skips a root-level file.

    lefthook reported "no files for inspection" for a root `foo.py` and moved on —
    which reads exactly like a pass. Verified against lefthook v2.1.10; the
    alternation is what makes both depths match.
    """
    for glob in (installer.PY_GLOB, installer.TS_GLOB):
        assert glob.startswith("{*."), f"{glob} would skip root-level files"
        assert "**/" in glob, f"{glob} would skip nested files"


def test_installer_nests_entries_under_precommit_commands(tmp_path: Path):
    yaml = pytest.importorskip("yaml")
    config = tmp_path / "lefthook.yml"
    config.write_text(LEFTHOOK)

    assert _install(config) == 0
    parsed = yaml.safe_load(config.read_text())
    commands = parsed["pre-commit"]["commands"]
    assert set(commands) == {"existing-check", *installer.MANAGED_COMMAND_NAMES}
    # Everything around the managed block survives untouched.
    assert parsed["pre-commit"]["parallel"] is True
    assert list(parsed["pre-push"]["commands"]) == ["tests"]


def test_installer_is_idempotent(tmp_path: Path):
    config = tmp_path / "lefthook.yml"
    config.write_text(LEFTHOOK)
    assert _install(config) == 0
    once = config.read_text()
    assert _install(config) == 0
    assert config.read_text() == once


def test_check_mode_reports_without_writing(tmp_path: Path):
    config = tmp_path / "lefthook.yml"
    config.write_text(LEFTHOOK)
    assert _install(config, check=True) == 1
    assert config.read_text() == LEFTHOOK


def test_installer_refuses_a_config_with_no_precommit_commands(tmp_path: Path):
    config = tmp_path / "lefthook.yml"
    config.write_text("pre-push:\n  commands:\n    tests:\n      run: echo t\n")
    assert _install(config) == 1
