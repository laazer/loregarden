"""A push does not rerun tests that passed on exactly its code, and nothing else counts as that."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from loregarden.services.capacity_progress import PROGRESS_ENV
from tests.repo_paths import REPO_ROOT

SCRIPTS = REPO_ROOT / ".lefthook" / "scripts"
_SPEC = importlib.util.spec_from_file_location("pass_receipt", SCRIPTS / "pass_receipt.py")
receipts = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(receipts)

_CLEAN_ENV = {
    k: v
    for k, v in os.environ.items()
    if k not in ("GIT_DIR", "GIT_WORK_TREE", "PYTEST_ADDOPTS", "PYTEST_XDIST_WORKER")
    and not k.startswith("LOREGARDEN_")
}

_CONFTEST = """
import sys

def pytest_configure(config):
    root = config.rootpath.parent
    sys.path.insert(0, str(root / ".lefthook" / "scripts"))
    import pass_receipt

    found = pass_receipt.recorder_for(config, repo=root, package=root / "server" / "pkg.py")
    if found is not None:
        config.pluginmanager.register(found, "pass_receipt")
"""


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", "-c", "commit.gpgsign=false"]
        + list(args),
        cwd=repo,
        env=_CLEAN_ENV,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def _commit(repo: Path) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "c")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository laid out as this one is, with two test files and the real script."""
    root = (tmp_path / "repo").resolve()
    tests = root / "server" / "tests"
    tests.mkdir(parents=True)
    scripts = root / ".lefthook" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(SCRIPTS / "pass_receipt.py", scripts)
    (root / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n.venv/\n", encoding="utf-8")
    (root / "server" / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    (tests / "conftest.py").write_text(_CONFTEST, encoding="utf-8")
    (tests / "test_a.py").write_text("def test_a(): pass\ndef test_b(): pass\n", encoding="utf-8")
    (tests / "test_c.py").write_text("def test_c(): pass\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _commit(root)
    return root


def _pytest(repo: Path, *args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
        cwd=repo / "server",
        env=_CLEAN_ENV,
        capture_output=True,
        text=True,
        check=False,
    )


def _passed(repo: Path) -> set[str]:
    return receipts.passed_files(repo, _git(repo, "rev-parse", "HEAD^{tree}"))


def _covers(repo: Path, *files: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "pass_receipt.py"), "covers", "--repo", str(repo)],
        input="\n".join(files),
        env=_CLEAN_ENV,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("workers", ["0", "2"])
def test_a_passing_run_records_the_files_it_ran(repo: Path, workers):
    _pytest(repo, "-n", workers)
    assert _passed(repo) == {"tests/test_a.py", "tests/test_c.py"}


def test_runs_of_different_files_add_up(repo: Path):
    _pytest(repo, "tests/test_a.py")
    assert _passed(repo) == {"tests/test_a.py"}
    assert _covers(repo, "server/tests/test_a.py", "server/tests/test_c.py").returncode == 1
    _pytest(repo, "tests/test_c.py")
    done = _covers(repo, "server/tests/test_a.py", "server/tests/test_c.py")
    assert done.returncode == 0
    assert "all 2 test file(s)" in done.stdout


@pytest.mark.parametrize(
    "narrowing",
    [
        ["-k", "test_a"],
        ["-m", "not slow"],
        ["tests/test_a.py::test_a"],
        ["--deselect", "tests/test_a.py::test_b"],
        ["--collect-only"],
    ],
)
def test_part_of_a_file_is_not_the_file(repo: Path, narrowing):
    _pytest(repo, *narrowing)
    assert _passed(repo) == set()


def test_only_last_failures_is_not_the_file(repo: Path):
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--lf"],
        cwd=repo / "server",
        env=_CLEAN_ENV,
        capture_output=True,
        check=True,
    )
    assert _passed(repo) == set()


def test_a_failing_run_records_nothing(repo: Path):
    (repo / "server" / "tests" / "test_z.py").write_text(
        "def test_z(): assert 0\n", encoding="utf-8"
    )
    _commit(repo)
    _pytest(repo)
    assert _passed(repo) == set()


@pytest.mark.parametrize("name", ["server/tests/test_a.py", "server/tests/untracked.py"])
def test_uncommitted_code_is_not_the_commit(repo: Path, name):
    (repo / name).write_text("def test_a(): pass\n", encoding="utf-8")
    _pytest(repo)
    assert _passed(repo) == set()
    assert _recorder(repo) is None


def test_code_committed_after_the_run_has_not_passed(repo: Path):
    _pytest(repo)
    (repo / "server" / "pkg.py").write_text("X = 1\n", encoding="utf-8")
    _commit(repo)
    assert _passed(repo) == set()
    assert _covers(repo, "tests/test_a.py").returncode == 1


def test_amending_the_message_keeps_the_pass(repo: Path):
    _pytest(repo)
    _git(repo, "commit", "-q", "--amend", "-m", "other words")
    assert _covers(repo, "tests/test_a.py", "tests/test_c.py").returncode == 0


def test_an_edit_put_back_while_the_tests_ran_records_nothing(repo: Path):
    tree = receipts.committed_tree(repo)
    recorder = receipts.PassRecorder(repo, tree)
    recorder.pytest_runtest_logreport(SimpleNamespace(nodeid="tests/test_a.py::test_a"))
    target = repo / "server" / "tests" / "test_a.py"
    original = target.read_text(encoding="utf-8")
    time.sleep(0.05)
    target.write_text("mutant\n", encoding="utf-8")
    target.write_text(original, encoding="utf-8")
    assert receipts.committed_tree(repo) == tree
    recorder.pytest_sessionfinish(0)
    assert _passed(repo) == set()


def test_code_left_changed_by_the_run_records_nothing(repo: Path):
    recorder = receipts.PassRecorder(repo, receipts.committed_tree(repo))
    recorder.pytest_runtest_logreport(SimpleNamespace(nodeid="tests/test_a.py::test_a"))
    (repo / "left.py").write_text("X = 1\n", encoding="utf-8")
    recorder.pytest_sessionfinish(0)
    (repo / "left.py").unlink()
    assert _passed(repo) == set()


def test_a_dirty_tree_has_not_passed_even_when_its_commit_has(repo: Path):
    _pytest(repo)
    (repo / "server" / "pkg.py").write_text("X = 1\n", encoding="utf-8")
    assert _covers(repo, "tests/test_a.py").returncode == 1


def test_no_files_are_not_covered(repo: Path):
    _pytest(repo)
    assert _covers(repo, "", " ").returncode == 1


def test_a_pass_on_another_python_is_not_a_pass_here(repo: Path):
    _pytest(repo)
    (receipt,) = (repo / ".git" / receipts.RECEIPTS).glob("*.json")
    body = json.loads(receipt.read_text(encoding="utf-8"))
    receipt.write_text(json.dumps(body | {"environment": "other"}), encoding="utf-8")
    assert _passed(repo) == set()


def test_a_pass_is_read_for_a_day_and_then_dropped(repo: Path):
    _pytest(repo)
    (receipt,) = (repo / ".git" / receipts.RECEIPTS).glob("*.json")
    old = time.time() - receipts.MAX_AGE_SECONDS - 60
    os.utime(receipt, (old, old))
    assert _passed(repo) == set()
    receipts.record(repo, "t", {"tests/test_a.py"})
    assert not receipt.exists()


def test_worktrees_share_their_receipts(repo: Path, tmp_path: Path):
    _pytest(repo)
    other = tmp_path / "other"
    _git(repo, "worktree", "add", "-q", "--detach", str(other))
    assert _covers(other, "tests/test_a.py").returncode == 0


def _config(repo: Path, **overrides) -> SimpleNamespace:
    fields = {
        "rootpath": repo / "server",
        "args": ["tests"],
        "getoption": lambda name, default: default,
    }
    return SimpleNamespace(**fields | overrides)


def _recorder(repo: Path, package: str = "server/loregarden/__init__.py", **overrides):
    return receipts.recorder_for(_config(repo, **overrides), repo=repo, package=repo / package)


def test_only_the_repositorys_own_code_is_recorded(repo: Path, tmp_path: Path):
    assert _recorder(repo) is not None
    assert _recorder(repo, "server/mutants/loregarden/__init__.py") is None
    assert _recorder(repo, "../elsewhere/loregarden/__init__.py") is None
    assert _recorder(repo, rootpath=tmp_path) is None
    assert _recorder(repo, workerinput={}) is None


def test_outside_git_nothing_is_recorded_and_nothing_fails(tmp_path: Path):
    (tmp_path / "server").mkdir()
    assert _recorder(tmp_path) is None


# --- the push -----------------------------------------------------------------------


@pytest.fixture
def pushing(repo: Path) -> Path:
    """The same repository, with the push script and a selector that names what it is told."""
    scripts = repo / ".lefthook" / "scripts"
    for name in (
        "push-server-tests.sh",
        "push-base.sh",
        "hook-noninteractive.sh",
        "native-git.sh",
        "server_python.sh",
    ):
        shutil.copy(SCRIPTS / name, scripts)
    (scripts / "server-tests.sh").write_text('echo "server-tests $*"\n', encoding="utf-8")
    (scripts / "select_pytest_targets.py").write_text(
        "import os, sys\nprint(os.environ['SELECTED'])\nsys.exit(int(os.environ['STATUS']))\n",
        encoding="utf-8",
    )
    python = repo / "server" / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    python.chmod(0o755)
    _commit(repo)
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


def _push(repo: Path, *, selected: str = "", status: int = 0, **env: str) -> str:
    done = subprocess.run(
        ["bash", str(repo / ".lefthook" / "scripts" / "push-server-tests.sh"), "echo", "queued"],
        cwd=repo,
        env=_CLEAN_ENV | {"SELECTED": selected, "STATUS": str(status)} | env,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout


A = "server/tests/test_a.py"


def test_a_push_of_code_that_passed_lints_and_does_not_queue(pushing: Path):
    assert _push(pushing, selected=A) == "queued\n"
    _pytest(pushing, "tests/test_a.py")
    said = _push(pushing, selected=A)
    assert "all 1 test file(s) already passed" in said
    assert "server-tests --lint-only" in said
    assert "queued" not in said


def test_a_push_needing_a_test_that_has_not_passed_queues(pushing: Path):
    _pytest(pushing, "tests/test_a.py")
    assert _push(pushing, selected=f"{A}\nserver/tests/test_c.py") == "queued\n"


def test_a_push_that_cannot_be_narrowed_needs_every_test(pushing: Path):
    _pytest(pushing, "tests/test_a.py")
    assert _push(pushing, status=2) == "queued\n"
    _pytest(pushing, "tests/test_c.py")
    assert "all 2 test file(s)" in _push(pushing, status=2)


@pytest.mark.parametrize(
    ("status", "env"), [(1, {}), (0, {"LOREGARDEN_FULL_TESTS": "1"})], ids=["broken", "asked"]
)
def test_a_selector_that_broke_or_a_full_run_asked_for_queues(pushing: Path, status, env):
    _pytest(pushing)
    assert _push(pushing, selected=A, status=status, **env) == "queued\n"


def test_the_hook_asks_before_it_queues_and_lint_only_stops_before_pytest():
    hook = (REPO_ROOT / "lefthook.yml").read_text(encoding="utf-8")
    assert (
        "bash .lefthook/scripts/push-server-tests.sh\n"
        '        bash .lefthook/scripts/capacity-run.sh "pre-push server-tests"' in hook
    )
    script = (SCRIPTS / "server-tests.sh").read_text(encoding="utf-8")
    stop = script.index('if [ -n "$LINT_ONLY" ]; then\n  exit 0')
    assert script.index('py-scripts-ruff.sh" format --check') < stop
    assert stop < script.index("run_and_judge pytest")


def test_a_run_without_the_progress_plugin_hands_no_test_its_progress_file(tmp_path: Path):
    """`capacity run -- pytest` sets it, and tests run suite scripts that write to it."""
    probe = REPO_ROOT / "server" / "tests" / "test_pass_receipt.py"
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-k", "probe_environment"]
        + [str(probe)],
        cwd=REPO_ROOT / "server",
        env=_CLEAN_ENV | {PROGRESS_ENV: str(tmp_path / "progress.json")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert "1 passed" in done.stdout, done.stdout + done.stderr


def test_probe_environment():
    assert PROGRESS_ENV not in os.environ
