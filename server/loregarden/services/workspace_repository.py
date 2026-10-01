"""Whether a workspace's path is a usable repository, and creating one where it is not.

Registering a workspace used to record a path and nothing else, so a workspace
could be added for a project whose directory did not exist yet — and then sit
there with every installer reporting "not a git repository". Initializing makes
the repository a workspace needs in one step: ``git init``, a ``lefthook.yml``
for the gates to live in, loregarden's hooks and AGENTS.md blocks, and an
initial commit holding all three. The commit matters: agent runs branch
worktrees off ``main``, and a worktree of an unborn branch, or of a commit that
predates AGENTS.md, gives the agent none of it.

Only a missing or empty directory is initialized. A directory with files in it
is someone's work, and a directory inside another repository would become a
nested repository — both are refused with the reason, never written to.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from loregarden.models.domain import Workspace
from loregarden.services import workspace_integration
from loregarden.services.git_subprocess import run_git
from loregarden.services.workspace_integration import Installer
from loregarden.services.workspace_paths import resolve_repo_path, resolve_workspace_root

logger = logging.getLogger(__name__)

#: The smallest config the hooks installer accepts: a pre-commit commands map to insert into.
SEED_LEFTHOOK = "pre-commit:\n  commands:\n"
INITIAL_BRANCH = "main"
INITIAL_COMMIT_MESSAGE = "Initialize repository for loregarden"
LEFTHOOK_TIMEOUT_SECONDS = 60


class RepositoryState(StrEnum):
    REPOSITORY = "repository"
    #: Nothing at the path yet; initializing creates it.
    MISSING = "missing"
    #: An empty directory; initializing fills it.
    EMPTY = "empty"
    #: Something is there and it is not a git repository; never written to.
    NOT_A_REPOSITORY = "not_a_repository"
    #: Inside another repository's work tree; initializing would nest one.
    INSIDE_REPOSITORY = "inside_repository"


INITIALIZABLE = frozenset({RepositoryState.MISSING, RepositoryState.EMPTY})


@dataclass(frozen=True)
class RepositoryInspection:
    root: Path
    state: RepositoryState
    detail: str


@dataclass(frozen=True)
class InitializedRepository:
    root: Path
    #: Empty when the git hooks are active; otherwise what the operator still has to run.
    follow_up: str


class RepositoryInitError(RuntimeError):
    """Initializing was refused or failed; the message says why, and nothing was left behind."""


def _enclosing_repository(path: Path) -> Path | None:
    """The top of the git work tree containing ``path``'s nearest existing ancestor."""
    existing = next((p for p in (path, *path.parents) if p.is_dir()), None)
    if existing is None:
        return None
    result = run_git(
        ["rev-parse", "--show-toplevel"],
        cwd=existing,
        capture_output=True,
        text=True,
        check=False,
    )
    # Non-zero is git's answer "not inside a work tree", the common case here.
    return Path(result.stdout.strip()).resolve() if result.returncode == 0 else None


def inspect_path(root: Path) -> RepositoryInspection:
    if (root / ".git").exists():
        return RepositoryInspection(root, RepositoryState.REPOSITORY, f"{root} is a git repository")
    if root.exists() and not root.is_dir():
        return RepositoryInspection(
            root, RepositoryState.NOT_A_REPOSITORY, f"{root} is a file, not a directory"
        )
    enclosing = _enclosing_repository(root)
    if enclosing is not None:
        return RepositoryInspection(
            root,
            RepositoryState.INSIDE_REPOSITORY,
            f"{root} is inside the repository at {enclosing}; pick that repository, or a path outside it",
        )
    if not root.exists():
        return RepositoryInspection(
            root,
            RepositoryState.MISSING,
            f"{root} does not exist; a new repository can be created there",
        )
    if not any(root.iterdir()):
        return RepositoryInspection(
            root, RepositoryState.EMPTY, f"{root} is empty; it can be initialized as a repository"
        )
    return RepositoryInspection(
        root,
        RepositoryState.NOT_A_REPOSITORY,
        f"{root} has files but is not a git repository; run `git init` there yourself, or pick another path",
    )


def inspect_repo_path(raw: str) -> RepositoryInspection:
    """Inspect a ``repo_path`` as a workspace would resolve it, before one exists."""
    return inspect_path(resolve_repo_path(raw))


def inspect_workspace(workspace: Workspace) -> RepositoryInspection:
    return inspect_path(resolve_workspace_root(workspace))


def _git(root: Path, args: list[str]) -> None:
    result = run_git(args, cwd=root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RepositoryInitError(
            f"git {args[0]} failed in {root}: {result.stderr.strip() or f'exit {result.returncode}'}"
        )


def _activate_hooks(root: Path) -> str:
    """Run ``lefthook install``, or say why it did not run. Returns the follow-up, if any."""
    lefthook = shutil.which("lefthook")
    if lefthook is None:
        return f"lefthook is not on loregarden's PATH, so the gates will not run on commit until you run `lefthook install` in {root}."
    try:
        result = subprocess.run(
            [lefthook, "install"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=LEFTHOOK_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("lefthook install failed to run in %s: %s", root, exc)
        return f"`lefthook install` could not run ({exc}); run it in {root} so the gates run on commit."
    if result.returncode != 0:
        logger.warning("lefthook install failed in %s: %s", root, result.stderr.strip())
        return f"`lefthook install` failed ({result.stderr.strip() or f'exit {result.returncode}'}); run it in {root}."
    return ""


def _populate(workspace: Workspace, root: Path) -> None:
    _git(root, ["init", "-q", "-b", INITIAL_BRANCH])
    (root / "lefthook.yml").write_text(SEED_LEFTHOOK, encoding="utf-8")
    for installer in Installer:
        try:
            workspace_integration.install(workspace, installer)
        except workspace_integration.InstallerError as exc:
            raise RepositoryInitError(f"installing {installer} failed: {exc}") from exc
    _git(root, ["add", "--all"])
    _git(root, ["commit", "-q", "-m", INITIAL_COMMIT_MESSAGE])


def _roll_back(root: Path, created: Path | None) -> None:
    """Remove what initializing wrote: the directories it made, or the contents of the empty one."""
    targets = [created] if created is not None else list(root.iterdir())
    for target in targets:
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        except OSError:
            logger.exception("could not remove %s after a failed repository init", target)


def initialize_repository(workspace: Workspace) -> InitializedRepository:
    """Create and commit the workspace's repository. Raises ``RepositoryInitError`` and writes nothing when refused."""
    found = inspect_workspace(workspace)
    if found.state not in INITIALIZABLE:
        raise RepositoryInitError(found.detail)
    root = found.root
    created = next(
        (p for p in reversed((root, *root.parents)) if not p.exists()),
        None,
    )
    root.mkdir(parents=True, exist_ok=True)
    try:
        _populate(workspace, root)
    except (RepositoryInitError, OSError) as exc:
        logger.warning("initializing %s for workspace %s failed: %s", root, workspace.slug, exc)
        _roll_back(root, created)
        raise RepositoryInitError(str(exc)) from exc
    return InitializedRepository(root, _activate_hooks(root))
