"""Run the build a reviewer needs, from one button.

A workspace says what it can run in ``.loregarden/review.yaml``; its format, and
which of it fits a ticket, are `review_config`. ``prepare`` runs to completion
first (optional); ``command`` starts the build, or ``scene_command`` when the
reviewer picked a scene. ``{worktree}`` is the review checkout and ``{scene}`` a
scene path relative to it.

The review checkout is one detached worktree per workspace beside the ticket
worktrees (``.worktrees/review-<slug>``), moved to the requested ref on every
launch. It has no `worktrees` row, so no reaper owns it, and it keeps its
ignored build caches between launches, which is what makes the second launch
fast. One build runs per workspace: a new launch stops the old one first,
because moving a checkout under a running build is worse than restarting it.

State is held in memory. A server restart forgets the handle, not the process;
the next launch from the page starts a fresh one.
"""

from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import threading
import time
from collections import deque
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

from loregarden.config import settings
from loregarden.models.domain import Workspace
from loregarden.services.git_subprocess import run_git
from loregarden.services.path_resolve import expand_path
from loregarden.services.review_config import (
    CONFIG_FILE,
    LaunchError,
    LaunchKind,
    LaunchSpec,
    choose_target,
    load_review_config,
)
from loregarden.services.workspace_paths import resolve_workspace_root
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

PREPARE_TIMEOUT_SECONDS = 900
#: A build that exits non-zero this soon after starting failed to start.
STARTUP_GRACE_SECONDS = 5.0
LOG_TAIL_LINES = 40
_GIT_TIMEOUT_SECONDS = 120
#: Terminal colour/cursor sequences: Godot and most CLIs colour their output.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_SCENE = re.compile(r"^(?!.*\.\.)[A-Za-z0-9_][A-Za-z0-9_./ -]*\.(tscn|scn)$")


class LaunchPhase(StrEnum):
    PREPARING = "preparing"
    RUNNING = "running"
    EXITED = "exited"
    FAILED = "failed"
    STOPPED = "stopped"


class LaunchState(BaseModel):
    workspace_slug: str
    ref: str
    sha: str
    target: str
    kind: LaunchKind
    scene: str = ""
    worktree: str
    phase: LaunchPhase
    pid: int | None = None
    exit_code: int | None = None
    error: str = ""
    log_tail: list[str] = Field(default_factory=list)
    started_at: str


def is_scene_path(value: str) -> bool:
    """Whether ``value`` is a scene file inside the project, safe to put in an argv."""
    return _SCENE.match(value) is not None


def review_worktree_path(workspace: Workspace) -> Path:
    root = resolve_workspace_root(workspace)
    return root.parent / ".worktrees" / f"review-{workspace.slug}"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return run_git(
        list(args), cwd=str(cwd), capture_output=True, text=True,
        timeout=_GIT_TIMEOUT_SECONDS, check=False,
    )  # fmt: skip


def resolve_ref_sha(repo_root: Path, ref: str) -> str:
    if ref.startswith("-"):
        raise LaunchError(f"{ref!r} is not a ref")
    result = _git(repo_root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    sha = result.stdout.strip()
    if result.returncode != 0 or not sha:
        raise LaunchError(f"{ref!r} names no commit in {repo_root}")
    return sha


def checkout_review_worktree(workspace: Workspace, ref: str) -> tuple[Path, str]:
    """The review worktree, detached at ``ref``'s tip, created on first use."""
    repo_root = resolve_workspace_root(workspace)
    sha = resolve_ref_sha(repo_root, ref)
    path = review_worktree_path(workspace)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        result = _git(repo_root, "worktree", "add", "--detach", str(path), sha)
        if result.returncode != 0:
            raise LaunchError(f"git worktree add failed: {result.stderr.strip()}")
        return path, sha
    if not (path / ".git").exists():
        raise LaunchError(f"{path} exists but is not a git worktree; move it aside")
    # --force: the checkout is the control plane's, never hand-edited, and a
    # build that rewrote a tracked file must not stop the next launch.
    result = _git(path, "checkout", "--detach", "--force", sha)
    if result.returncode != 0:
        raise LaunchError(f"git checkout {sha[:10]} failed: {result.stderr.strip()}")
    return path, sha


def _render(argv: list[str], *, worktree: Path, scene: str) -> list[str]:
    return [part.replace("{worktree}", str(worktree)).replace("{scene}", scene) for part in argv]


def _logs_dir() -> Path:
    path = expand_path(Path("data/review-launches"), repo_root=settings.repo_root)
    path.mkdir(parents=True, exist_ok=True)
    return path


class _Launch:
    def __init__(self, state: LaunchState, log_path: Path) -> None:
        self.state = state
        self.log_path = log_path
        self.process: subprocess.Popen | None = None
        self.stop_requested = False


class ReviewLauncher:
    """One launch per workspace, tracked in memory."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._launches: dict[str, _Launch] = {}

    def status(self, workspace_slug: str) -> LaunchState | None:
        with self._lock:
            launch = self._launches.get(workspace_slug)
            if launch is None:
                return None
            launch.state.log_tail = _tail(launch.log_path)
            return launch.state.model_copy()

    def launch(
        self, workspace: Workspace, *, ref: str, scene: str = "", target: str = ""
    ) -> LaunchState:
        config = load_review_config(workspace)
        if config is None:
            raise LaunchError(
                f"{workspace.slug} has no {CONFIG_FILE}; add one with a launch.command "
                "to run its build from the review page"
            )
        if scene and not is_scene_path(scene):
            raise LaunchError(f"{scene!r} is not a scene path inside the project")
        name, spec = choose_target(config, target, scene)
        self.stop(workspace.slug)
        worktree, sha = checkout_review_worktree(workspace, ref)

        log_path = _logs_dir() / f"{workspace.slug}.log"
        log_path.write_text("", encoding="utf-8")
        state = LaunchState(
            workspace_slug=workspace.slug, ref=ref, sha=sha, target=name, kind=spec.kind,
            scene=scene, worktree=str(worktree),
            phase=LaunchPhase.PREPARING if spec.prepare else LaunchPhase.RUNNING,
            started_at=datetime.now(timezone.utc).isoformat(),
        )  # fmt: skip
        launch = _Launch(state, log_path)
        with self._lock:
            self._launches[workspace.slug] = launch
        threading.Thread(
            target=self._run,
            args=(launch, spec, worktree, scene),
            name=f"review-launch-{workspace.slug}",
            daemon=True,
        ).start()
        return state.model_copy()

    def stop(self, workspace_slug: str) -> LaunchState | None:
        with self._lock:
            launch = self._launches.get(workspace_slug)
        if launch is None:
            return None
        launch.stop_requested = True
        process = launch.process
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)
            except ProcessLookupError:  # silent-ok: it exited between poll and kill
                pass
            except subprocess.TimeoutExpired:
                logger.warning("review build %s ignored SIGTERM; killing", workspace_slug)
                os.killpg(process.pid, signal.SIGKILL)
        return self.status(workspace_slug)

    def _run(
        self,
        launch: _Launch,
        spec: LaunchSpec,
        worktree: Path,
        scene: str,
    ) -> None:
        cwd = worktree / spec.cwd
        argv = spec.scene_command if scene else spec.command
        try:
            if not cwd.is_dir():
                raise LaunchError(f"{spec.cwd} is not a directory at this ref")
            if spec.prepare:
                self._prepare(launch, _render(spec.prepare, worktree=worktree, scene=scene), cwd)
                if launch.stop_requested:
                    self._finish(launch, LaunchPhase.STOPPED)
                    return
            self._start(launch, _render(argv, worktree=worktree, scene=scene), cwd)
        except (LaunchError, OSError) as exc:
            logger.warning("review build for %s failed: %s", launch.state.workspace_slug, exc)
            self._finish(launch, LaunchPhase.FAILED, error=str(exc))

    def _prepare(self, launch: _Launch, argv: list[str], cwd: Path) -> None:
        # Popen rather than run: a stop (or a newer launch) must be able to kill
        # a ten-minute import, not wait it out with the checkout half-moved.
        with launch.log_path.open("a", encoding="utf-8") as log:
            log.write(f"$ {' '.join(argv)}\n")
            log.flush()
            try:
                process = subprocess.Popen(  # noqa: S603 - argv from the workspace's committed config
                    argv, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True,
                )  # fmt: skip
            except FileNotFoundError as exc:
                raise LaunchError(f"prepare command not found: {argv[0]}") from exc
            with self._lock:
                launch.process = process
            try:
                code = process.wait(timeout=PREPARE_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as exc:
                os.killpg(process.pid, signal.SIGKILL)
                raise LaunchError(f"prepare ran past {PREPARE_TIMEOUT_SECONDS}s") from exc
        if launch.stop_requested:
            return
        if code != 0:
            raise LaunchError(f"prepare exited {code}; see the log below")
        with self._lock:
            launch.state.phase = LaunchPhase.RUNNING

    def _start(self, launch: _Launch, argv: list[str], cwd: Path) -> None:
        log = launch.log_path.open("a", encoding="utf-8")
        log.write(f"$ {' '.join(argv)}\n")
        log.flush()
        try:
            process = subprocess.Popen(  # noqa: S603 - argv from the workspace's committed config
                argv, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,
            )  # fmt: skip
        except FileNotFoundError as exc:
            log.close()
            raise LaunchError(f"launch command not found: {argv[0]}") from exc
        started = time.monotonic()
        with self._lock:
            launch.process = process
            launch.state.pid = process.pid
            launch.state.phase = LaunchPhase.RUNNING
        code = process.wait()
        log.close()
        if launch.stop_requested:
            self._finish(launch, LaunchPhase.STOPPED, exit_code=code)
        elif code != 0 and launch.state.kind is LaunchKind.CHECK:
            self._finish(
                launch, LaunchPhase.FAILED, exit_code=code,
                error=f"{launch.state.target} exited {code}; see the log below",
            )  # fmt: skip
        elif code != 0 and time.monotonic() - started < STARTUP_GRACE_SECONDS:
            self._finish(
                launch, LaunchPhase.FAILED, exit_code=code,
                error=f"the build exited {code} right after starting; see the log below",
            )  # fmt: skip
        else:
            self._finish(launch, LaunchPhase.EXITED, exit_code=code)

    def _finish(
        self, launch: _Launch, phase: LaunchPhase, *, exit_code: int | None = None, error: str = ""
    ) -> None:
        with self._lock:
            launch.state.phase = phase
            launch.state.exit_code = exit_code
            launch.state.error = error


def _tail(path: Path) -> list[str]:
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            return [
                _ANSI.sub("", line.rstrip("\n")) for line in deque(handle, maxlen=LOG_TAIL_LINES)
            ]
    except FileNotFoundError:  # silent-ok: no launch has written a log yet; status shows none
        return []


review_launcher = ReviewLauncher()
