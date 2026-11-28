"""A write this build is too old for runs on main's build instead.

`mcp_tools.refuse_stale_write` refuses a write from a build that does not know
every migration the database has applied, and it is right to: a build that
predates a column writes rows without it. But its only remedy was "update this
checkout", and every session's worktree is cut from an older main, so every
session met the refusal whenever main shipped a migration, and stopped there.

A build that knows the database always exists. `shared_database_guard` lets no
migration reach the shared database unless `origin/main` ships it, and refs are
shared by every worktree of the repository, so the local `origin/main` ref
names a build the database cannot be ahead of. This module keeps one checkout
of it, `data/cli-main-build` in the primary checkout (a detached worktree;
`data/` is ignored), moves it to that ref when the ref has moved, and runs the
refused call there through that build's own `scripts/loregarden-cli.sh`.

The checkout belongs to the CLI. It is moved only when clean: local edits
there are somebody's, and this refuses rather than discard them. Moves take an
exclusive lock and runs a shared one, so a call never runs on a tree being
checked out under it.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loregarden.cli.errors import UsageError
from loregarden.services.git_subprocess import run_git
from loregarden.services.own_tree_files import excluding_own_files

#: Where main's build is kept, under the primary checkout.
BUILD_DIR = Path("data") / "cli-main-build"
LOCK_FILE = Path("data") / "cli-main-build.lock"
SHIPPED_REF = "origin/main"
#: Set on the call this module makes, so main's build never hands it on again.
ON_MAIN_BUILD_ENV = "LOREGARDEN_CLI_ON_MAIN_BUILD"


@dataclass(frozen=True)
class MainBuild:
    path: Path
    sha: str


def _git(cwd: Path, *args: str) -> str:
    done = run_git(list(args), cwd=cwd, check=False, capture_output=True, text=True)
    if done.returncode != 0:
        raise UsageError(
            f"main's build: `git {' '.join(args)}` in {cwd} failed: "
            f"{(done.stderr or done.stdout).strip()}"
        )
    return done.stdout.strip()


@contextmanager
def _ready(primary: Path) -> Iterator[MainBuild]:
    """Main's build at `origin/main`, held so nobody moves it while the caller runs it.

    Exclusive while it is created or moved, then converted to shared on the same
    handle, so no other call can move it in between.
    """
    lock = primary / LOCK_FILE
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            build = _at_ref(primary)
            fcntl.flock(handle, fcntl.LOCK_SH)
            yield build
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _at_ref(primary: Path) -> MainBuild:
    """Main's build, created or moved to `origin/main` now. The caller holds the lock."""
    sha = _git(primary, "rev-parse", "--verify", f"{SHIPPED_REF}^{{commit}}")
    path = primary / BUILD_DIR
    if not (path / ".git").exists():
        # --force: a directory removed by hand leaves git's registration behind.
        _git(primary, "worktree", "add", "--force", "--detach", str(path), sha)
        return MainBuild(path, sha)
    if _git(path, "rev-parse", "HEAD") == sha:
        return MainBuild(path, sha)
    dirty = _git(path, "status", "--porcelain", "--untracked-files=no", *excluding_own_files(path))
    if dirty:
        raise UsageError(
            f"main's build at {path} has local changes, so it cannot be moved to "
            f"{SHIPPED_REF} ({sha[:12]}); it belongs to the CLI, so whoever made them "
            f"should commit or remove them:\n{dirty}"
        )
    _git(path, "checkout", "--quiet", "--detach", sha)
    return MainBuild(path, sha)


def _command(build: MainBuild, tool: str, *, orchestrated: bool) -> list[str]:
    command = [str(build.path / "scripts" / "loregarden-cli.sh"), "mcp", "call", tool]
    command += ["--json", "-"]
    return command + (["--orchestrated"] if orchestrated else [])


#: This process's `uv run` environment, which would point main's build at this build's venv.
_NOT_INHERITED = frozenset({"VIRTUAL_ENV"})


def _env(primary: Path, environ: Mapping[str, str]) -> dict[str, str]:
    kept = {k: v for k, v in environ.items() if k not in _NOT_INHERITED}
    return {**kept, ON_MAIN_BUILD_ENV: "1", "LOREGARDEN_REPO_ROOT": str(primary)}


def _run_cli(command: list[str], *, stdin: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        command, input=stdin, capture_output=True, text=True, env=env, check=False
    )


def run_on_main_build(
    primary: Path,
    tool: str,
    arguments: dict[str, Any],
    *,
    orchestrated: bool,
    why: str,
) -> str:
    """Run `mcp call tool` on main's build and return what it printed.

    `arguments` go as JSON on stdin, already built: a `key=@-` value has been
    read from this process's stdin and cannot be read twice. `why` is the
    refusal, said with the notice so nobody wonders whose code wrote.
    """
    if os.environ.get(ON_MAIN_BUILD_ENV):
        # Main's build refused too: the database is ahead of `origin/main`,
        # which the guard exists to prevent. Nothing newer to hand it to.
        raise UsageError(f"{why} This is main's build already ({SHIPPED_REF}).")
    with _ready(primary) as build:
        print(
            f"loregarden: {why} Running {tool} on main's build instead "
            f"({SHIPPED_REF} at {build.sha[:12]}, {build.path}).",
            file=sys.stderr,
            flush=True,
        )
        done = _run_cli(
            _command(build, tool, orchestrated=orchestrated),
            stdin=json.dumps(arguments),
            env=_env(primary, os.environ),
        )
    if done.stderr:
        sys.stderr.write(done.stderr)
    if done.returncode != 0:
        raise UsageError(
            f"{tool} on main's build ({build.sha[:12]}) exited {done.returncode}; "
            "its own words are above."
        )
    return done.stdout.removesuffix("\n")
