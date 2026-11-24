"""The review build launcher against a real git repository and real processes."""

from __future__ import annotations

import time
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import Workspace
from loregarden.services.git_subprocess import run_git
from loregarden.services.review_config import LaunchError, LaunchKind, config_status
from loregarden.services.review_launcher import (
    LaunchPhase,
    LaunchState,
    ReviewLauncher,
    review_worktree_path,
)

TERMINAL = {LaunchPhase.EXITED, LaunchPhase.FAILED, LaunchPhase.STOPPED}


def _git(cwd: Path, *args: str) -> str:
    return run_git(list(args), cwd=str(cwd), capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from loregarden.config import settings

    monkeypatch.setattr(settings, "repo_root", tmp_path)
    root = tmp_path / "ws" / "game"
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / "marker.txt").write_text("main\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "main")
    _git(root, "checkout", "-q", "-b", "integration/root-1")
    (root / "marker.txt").write_text("integration\n")
    _git(root, "commit", "-q", "-am", "integration")
    _git(root, "checkout", "-q", "main")
    return root


def _workspace(root: Path, config: str | None) -> Workspace:
    if config is not None:
        (root / ".loregarden").mkdir(exist_ok=True)
        (root / ".loregarden" / "review.yaml").write_text(config)
    return Workspace(slug="game", name="game", repo_path=str(root))


def _settle(launcher: ReviewLauncher, slug: str, timeout: float = 20.0) -> LaunchState:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = launcher.status(slug)
        if state is not None and state.phase in TERMINAL:
            return state
        time.sleep(0.05)
    raise AssertionError(f"launch never settled: {launcher.status(slug)}")


def _wait_for_pid(launcher: ReviewLauncher, slug: str) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = launcher.status(slug)
        if state is not None and state.pid is not None:
            return
        time.sleep(0.05)
    raise AssertionError("the build never started")


def test_launch_checks_out_the_ref_runs_prepare_then_the_build(repo: Path):
    workspace = _workspace(
        repo,
        'launch:\n  prepare: ["sh", "-c", "echo preparing"]\n'
        '  command: ["sh", "-c", "cat {worktree}/marker.txt"]\n',
    )
    launcher = ReviewLauncher()

    launcher.launch(workspace, ref="integration/root-1")
    state = _settle(launcher, "game")

    worktree = review_worktree_path(workspace)
    assert state.phase is LaunchPhase.EXITED, state
    assert state.exit_code == 0
    assert state.worktree == str(worktree)
    assert (worktree / "marker.txt").read_text() == "integration\n"
    assert state.sha == _git(repo, "rev-parse", "integration/root-1").strip()
    assert "preparing" in state.log_tail
    assert "integration" in state.log_tail


def test_relaunch_moves_the_existing_worktree_to_the_new_ref(repo: Path):
    workspace = _workspace(repo, 'launch:\n  command: ["true"]\n')
    launcher = ReviewLauncher()
    launcher.launch(workspace, ref="integration/root-1")
    _settle(launcher, "game")

    launcher.launch(workspace, ref="main")
    _settle(launcher, "game")

    assert (review_worktree_path(workspace) / "marker.txt").read_text() == "main\n"


def test_scene_launch_uses_scene_command(repo: Path):
    workspace = _workspace(
        repo,
        'launch:\n  command: ["false"]\n  scene_command: ["echo", "scene={scene}"]\n',
    )
    launcher = ReviewLauncher()

    launcher.launch(workspace, ref="main", scene="scenes/levels/a b.tscn")
    state = _settle(launcher, "game")

    assert state.phase is LaunchPhase.EXITED
    assert "scene=scenes/levels/a b.tscn" in state.log_tail


def test_the_log_tail_drops_terminal_colour_codes(repo: Path):
    workspace = _workspace(
        repo, 'launch:\n  command: ["printf", "\\\\033[92m[ DONE ]\\\\033[0m\\\\n"]\n'
    )
    launcher = ReviewLauncher()

    launcher.launch(workspace, ref="main")
    state = _settle(launcher, "game")

    assert "[ DONE ]" in state.log_tail


def test_a_build_that_dies_on_start_is_a_failure_with_its_log(repo: Path):
    workspace = _workspace(repo, 'launch:\n  command: ["sh", "-c", "echo boom >&2; exit 3"]\n')
    launcher = ReviewLauncher()

    launcher.launch(workspace, ref="main")
    state = _settle(launcher, "game")

    assert state.phase is LaunchPhase.FAILED
    assert state.exit_code == 3
    assert "boom" in state.log_tail


def test_a_failing_prepare_never_starts_the_build(repo: Path):
    workspace = _workspace(
        repo,
        'launch:\n  prepare: ["sh", "-c", "exit 4"]\n  command: ["sh", "-c", "echo started"]\n',
    )
    launcher = ReviewLauncher()

    launcher.launch(workspace, ref="main")
    state = _settle(launcher, "game")

    assert state.phase is LaunchPhase.FAILED
    assert "prepare exited 4" in state.error
    assert "started" not in state.log_tail


def test_a_missing_binary_is_a_failure_naming_it(repo: Path):
    workspace = _workspace(repo, 'launch:\n  command: ["no-such-binary-xyz"]\n')
    launcher = ReviewLauncher()

    launcher.launch(workspace, ref="main")
    state = _settle(launcher, "game")

    assert state.phase is LaunchPhase.FAILED
    assert "no-such-binary-xyz" in state.error


def test_stop_kills_a_running_build(repo: Path):
    workspace = _workspace(repo, 'launch:\n  command: ["sleep", "60"]\n')
    launcher = ReviewLauncher()
    launcher.launch(workspace, ref="main")
    _wait_for_pid(launcher, "game")

    launcher.stop("game")
    state = _settle(launcher, "game")

    assert state.phase is LaunchPhase.STOPPED


def test_stop_kills_a_running_prepare(repo: Path):
    workspace = _workspace(
        repo, 'launch:\n  prepare: ["sleep", "60"]\n  command: ["sh", "-c", "echo started"]\n'
    )
    launcher = ReviewLauncher()
    launcher.launch(workspace, ref="main")
    time.sleep(0.3)

    started = time.monotonic()
    launcher.stop("game")
    state = _settle(launcher, "game")

    assert time.monotonic() - started < 15
    assert state.phase is LaunchPhase.STOPPED
    assert "started" not in state.log_tail


TWO_TARGETS = """
targets:
  - name: game
    paths: ["scenes/*"]
    command: ["echo", "the game"]
    scene_command: ["echo", "scene={scene}"]
  - name: backend tests
    kind: check
    paths: ["backend/*"]
    command: ["sh", "-c", "echo the tests"]
"""


def test_a_named_target_is_the_one_that_runs(repo: Path):
    launcher = ReviewLauncher()

    launcher.launch(_workspace(repo, TWO_TARGETS), ref="main", target="backend tests")
    state = _settle(launcher, "game")

    assert (state.phase, state.target, state.kind) == (
        LaunchPhase.EXITED, "backend tests", LaunchKind.CHECK,
    )  # fmt: skip
    assert "the tests" in state.log_tail
    assert "the game" not in state.log_tail


def test_a_scene_opens_in_the_target_that_opens_scenes(repo: Path):
    launcher = ReviewLauncher()

    launcher.launch(_workspace(repo, TWO_TARGETS), ref="main", scene="scenes/a.tscn")
    state = _settle(launcher, "game")

    assert state.target == "game"
    assert "scene=scenes/a.tscn" in state.log_tail


def test_a_target_runs_in_its_own_directory(repo: Path):
    (repo / "backend").mkdir()
    (repo / "backend" / "here.txt").write_text("backend dir\n")
    _git(repo, "add", "backend")
    _git(repo, "commit", "-q", "-m", "backend")
    launcher = ReviewLauncher()

    launcher.launch(
        _workspace(
            repo,
            'launch:\n  cwd: backend\n  prepare: ["test", "-f", "here.txt"]\n'
            '  command: ["cat", "here.txt"]\n',
        ),
        ref="main",
    )
    state = _settle(launcher, "game")

    assert state.phase is LaunchPhase.EXITED, state
    assert "backend dir" in state.log_tail


def test_a_directory_the_ref_lacks_is_a_failure_naming_it(repo: Path):
    launcher = ReviewLauncher()

    launcher.launch(_workspace(repo, 'launch:\n  cwd: backend\n  command: ["true"]\n'), ref="main")
    state = _settle(launcher, "game")

    assert state.phase is LaunchPhase.FAILED
    assert "backend is not a directory at this ref" in state.error


@pytest.mark.parametrize(
    ("kind", "phase"), [("check", LaunchPhase.FAILED), ("app", LaunchPhase.EXITED)]
)
def test_only_a_check_fails_on_a_late_non_zero_exit(repo: Path, kind: str, phase: LaunchPhase):
    """A suite that runs a minute and fails is a failure; an app closed after one is not."""
    launcher = ReviewLauncher()

    with mock.patch("loregarden.services.review_launcher.STARTUP_GRACE_SECONDS", 0):
        launcher.launch(
            _workspace(repo, f'launch:\n  kind: {kind}\n  command: ["sh", "-c", "exit 1"]\n'),
            ref="main",
        )
        state = _settle(launcher, "game")

    assert (state.phase, state.exit_code) == (phase, 1)
    assert bool(state.error) is (phase is LaunchPhase.FAILED)


@pytest.mark.parametrize(
    ("config", "kwargs", "message"),
    [
        (None, {"ref": "main"}, "has no .loregarden/review.yaml"),
        ('launch:\n  command: ["true"]\n', {"ref": "nope"}, "names no commit"),
        ('launch:\n  command: ["true"]\n', {"ref": "--upload-pack=x"}, "is not a ref"),
        ('launch:\n  command: ["true"]\n', {"ref": "main", "scene": "a.tscn"}, "scene_command"),
        (
            'launch:\n  command: ["true"]\n  scene_command: ["true"]\n',
            {"ref": "main", "scene": "../../etc/passwd.tscn"},
            "not a scene path",
        ),
        (TWO_TARGETS, {"ref": "main"}, "name a target to launch: game, backend tests"),
        (TWO_TARGETS, {"ref": "main", "target": "editor"}, "has no target 'editor'"),
        (
            TWO_TARGETS,
            {"ref": "main", "target": "backend tests", "scene": "a.tscn"},
            "no scene_command",
        ),
        ("targets: []\n", {"ref": "main"}, "name something to run"),
        (
            TWO_TARGETS.replace("backend tests", "game"),
            {"ref": "main"},
            "target names repeat: game",
        ),
        ('launch:\n  cwd: ../x\n  command: ["true"]\n', {"ref": "main"}, "inside the repository"),
        ('launch:\n  cwd: /etc\n  command: ["true"]\n', {"ref": "main"}, "inside the repository"),
        ("launch: [1, 2]\n", {"ref": "main"}, "not a valid review config"),
    ],
)
def test_launch_refusals_name_the_fix(repo: Path, config, kwargs, message):
    workspace = _workspace(repo, config)

    with pytest.raises(LaunchError, match=message):
        ReviewLauncher().launch(workspace, **kwargs)


def test_config_status_reports_a_broken_file_instead_of_hiding_the_button(repo: Path):
    assert config_status(_workspace(repo, None)).configured is False

    broken = config_status(_workspace(repo, "launch: {}\n"))

    assert broken.configured is False
    assert "not a valid review config" in broken.error
    assert config_status(_workspace(repo, 'launch:\n  command: ["true"]\n')).configured
