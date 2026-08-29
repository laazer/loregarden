"""Pre-push test selection: which tests can the pushed commits actually break?

The full suite is minutes, so pre-push runs only the tests that reach the
changed modules. That trade is only safe while the fallbacks hold: every case
the selector cannot map has to widen to the full suite, never narrow to a fast
green. These tests pin the fallbacks first and the graph second.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SELECTOR = _ROOT / ".lefthook" / "scripts" / "select_pytest_targets.py"
_SCRUBBED_ENV = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}


def _load():
    spec = importlib.util.spec_from_file_location("select_pytest_targets", _SELECTOR)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


selector = _load()


# --------------------------------------------------------------------------- #
# fallbacks: everything unmappable must widen, never narrow
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "path",
    [
        "server/tests/conftest.py",
        "server/tests/factories.py",
        "server/tests/worktree_helpers.py",
        "server/pyproject.toml",
        "server/loregarden/data/seed.json",
        "lefthook.yml",
        "agent_context/orchestration/default.yaml",
        "CLAUDE.md",
    ],
)
def test_unmappable_change_runs_everything(path):
    assert selector.full_suite_reason([path]) is not None


@pytest.mark.parametrize(
    "paths",
    [
        ["server/loregarden/services/doctor.py"],
        ["server/tests/test_doctor.py"],
        ["client/src/App.tsx"],  # jest's problem, not pytest's
        ["server/loregarden/services/doctor.py", "client/src/App.tsx"],
    ],
)
def test_mappable_change_is_narrowed(paths):
    assert selector.full_suite_reason(paths) is None


# --------------------------------------------------------------------------- #
# the import graph
# --------------------------------------------------------------------------- #


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    pkg = tmp_path / "server" / "loregarden" / "services"
    pkg.mkdir(parents=True)
    (tmp_path / "server" / "loregarden" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "leaf.py").write_text("VALUE = 1\n", encoding="utf-8")
    (pkg / "middle.py").write_text("from loregarden.services.leaf import VALUE\n", encoding="utf-8")
    (pkg / "unrelated.py").write_text("OTHER = 2\n", encoding="utf-8")

    tests = tmp_path / "server" / "tests"
    tests.mkdir()
    (tests / "test_middle.py").write_text(
        "from loregarden.services import middle\n\ndef test_x():\n    assert middle\n",
        encoding="utf-8",
    )
    (tests / "test_unrelated.py").write_text(
        "from loregarden.services import unrelated\n\ndef test_y():\n    assert unrelated\n",
        encoding="utf-8",
    )
    (tests / "test_dynamic.py").write_text(
        "import importlib\n\ndef test_z():\n"
        '    assert importlib.import_module("loregarden.services.leaf")\n',
        encoding="utf-8",
    )
    return tmp_path


def _names(paths) -> set[str]:
    return {p.name for p in paths}


def test_transitive_import_is_selected(fake_repo: Path):
    """test_middle imports middle, which imports leaf. Changing leaf must select it."""
    selected = selector.select(fake_repo, ["server/loregarden/services/leaf.py"])
    assert "test_middle.py" in _names(selected)
    assert "test_unrelated.py" not in _names(selected)


def test_string_reference_counts_as_an_import(fake_repo: Path):
    """importlib.import_module("loregarden.x") is invisible to an import-only walk."""
    selected = selector.select(fake_repo, ["server/loregarden/services/leaf.py"])
    assert "test_dynamic.py" in _names(selected)


def test_changed_test_file_runs_itself(fake_repo: Path):
    selected = selector.select(fake_repo, ["server/tests/test_unrelated.py"])
    assert _names(selected) == {"test_unrelated.py"}


def test_deleted_test_file_is_not_selected(fake_repo: Path):
    """pytest reports a missing path as an error, and a stale name would abort the run."""
    selected = selector.select(fake_repo, ["server/tests/test_gone.py"])
    assert selected == []


# --------------------------------------------------------------------------- #
# the CLI contract the hook depends on
# --------------------------------------------------------------------------- #


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=_SCRUBBED_ENV)


def _run_cli(repo: Path, base: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SELECTOR), "--repo", str(repo), "--base", base],
        capture_output=True,
        text=True,
        env=_SCRUBBED_ENV,
    )


@pytest.fixture
def git_repo(fake_repo: Path) -> Path:
    _git(fake_repo, "init", "-q", ".")
    _git(fake_repo, "config", "user.email", "t@example.com")
    _git(fake_repo, "config", "user.name", "t")
    _git(fake_repo, "add", "-A")
    _git(fake_repo, "commit", "-qm", "base")
    return fake_repo


def test_cli_prints_repo_relative_paths(git_repo: Path):
    leaf = git_repo / "server" / "loregarden" / "services" / "leaf.py"
    leaf.write_text("VALUE = 2\n", encoding="utf-8")
    _git(git_repo, "commit", "-aqm", "change leaf")

    result = _run_cli(git_repo, "HEAD~1")
    assert result.returncode == selector.EXIT_SELECTED
    assert result.stdout.split() == ["server/tests/test_dynamic.py", "server/tests/test_middle.py"]


def test_cli_signals_full_suite_with_a_reason(git_repo: Path):
    (git_repo / "lefthook.yml").write_text("x: 1\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-qm", "touch repo config")

    result = _run_cli(git_repo, "HEAD~1")
    assert result.returncode == selector.EXIT_RUN_EVERYTHING
    assert "lefthook.yml" in result.stderr


def test_cli_runs_everything_when_nothing_is_reachable(git_repo: Path):
    """Zero selected tests is more often a hole in the graph than a fact."""
    orphan = git_repo / "server" / "loregarden" / "services" / "orphan.py"
    orphan.write_text("X = 1\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-qm", "add orphan module")

    result = _run_cli(git_repo, "HEAD~1")
    assert result.returncode == selector.EXIT_RUN_EVERYTHING
    assert "no test file reaches" in result.stderr


def test_cli_runs_everything_on_an_empty_diff(git_repo: Path):
    result = _run_cli(git_repo, "HEAD")
    assert result.returncode == selector.EXIT_RUN_EVERYTHING


# --------------------------------------------------------------------------- #
# the hook wiring
# --------------------------------------------------------------------------- #


def test_server_hook_falls_back_to_the_full_suite():
    """Every path out of selection must reach a plain `pytest -q -n auto`."""
    script = (_ROOT / ".lefthook" / "scripts" / "server-tests.sh").read_text(encoding="utf-8")
    assert "select_pytest_targets.py" in script
    assert "LOREGARDEN_FULL_TESTS" in script
    assert 'pytest -q -n auto"' not in script  # the full run must stay unquoted/real
    assert script.count("pytest -q -n auto") >= 2


def test_client_hook_narrows_jest_and_keeps_the_wide_checks():
    script = (_ROOT / ".lefthook" / "scripts" / "client-tests.sh").read_text(encoding="utf-8")
    assert "--changedSince=" in script
    assert "LOREGARDEN_FULL_TESTS" in script
    # oxlint and tsc still cover everything.
    assert "npm run lint" in script
    assert "npx tsc -b" in script
