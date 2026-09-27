"""Whether a workspace carries loregarden's rules and tools, and installing them.

Two installers, each a script in this checkout that writes a marker-delimited
block into another repo and has a read-only ``--check``:

- **hooks** — `scripts/install-workspace-hooks.sh`, the organization and
  silent-failure gates in that repo's `lefthook.yml`, for commits made by hand.
- **docs** — `scripts/install-workspace-docs.sh`, the section of that repo's
  `AGENTS.md` telling its agents their ticket is in a database, not a file.

Both bake absolute paths back into this checkout, so both run from the
*primary* checkout. A server running from a linked worktree — a branch server,
or `task server` started in one — would otherwise leave every workspace
pointing at a directory that disappears when the branch merges.

Each script reports one line per target, ``<state>: <path> …``; that first word
is what is parsed here. A script that refuses (no `lefthook.yml`, not a git
repository) says why on stderr, and that reason is what the page shows.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from loregarden.config import settings
from loregarden.models.domain import Workspace
from loregarden.services.git_subprocess import run_git
from loregarden.services.workspace_paths import resolve_workspace_root

logger = logging.getLogger(__name__)

INSTALLER_TIMEOUT_SECONDS = 60


class Installer(StrEnum):
    HOOKS = "hooks"
    DOCS = "docs"


class InstallState(StrEnum):
    #: The managed block is present and matches what this checkout would write.
    CURRENT = "current"
    MISSING = "missing"
    #: Present, but written by an older loregarden or another checkout.
    OUTDATED = "outdated"
    #: The installer cannot run here; ``detail`` says why.
    UNAVAILABLE = "unavailable"


#: The first word of an installer's report line. Ours, but printed by a script.
_REPORTED: dict[str, InstallState] = {
    "ok": InstallState.CURRENT,
    "installed": InstallState.CURRENT,
    "refreshed": InstallState.CURRENT,
    "created": InstallState.CURRENT,
    "missing": InstallState.MISSING,
    "outdated": InstallState.OUTDATED,
}

_SCRIPTS: dict[Installer, str] = {
    Installer.HOOKS: "install-workspace-hooks.sh",
    Installer.DOCS: "install-workspace-docs.sh",
}


@dataclass(frozen=True)
class InstallerStatus:
    installer: Installer
    state: InstallState
    detail: str


class InstallerError(RuntimeError):
    """An install the script refused or could not run; the message says why."""


def primary_checkout() -> Path | None:
    """The checkout the installed blocks should point at, or None outside git."""
    result = run_git(
        ["rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=settings.repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        logger.warning(
            "cannot find loregarden's primary checkout from %s: %s",
            settings.repo_root,
            result.stderr.strip(),
        )
        return None
    return Path(result.stdout.strip()).parent


def _argv(checkout: Path, installer: Installer, workspace: Workspace, *, check: bool) -> list[str]:
    argv = [str(checkout / "scripts" / _SCRIPTS[installer])]
    if check:
        argv.append("--check")
    if installer is Installer.DOCS:
        argv += ["--slug", workspace.slug]
    argv.append(str(resolve_workspace_root(workspace)))
    return argv


def _first_line(text: str) -> str:
    return next((line for line in text.splitlines() if line.strip()), "")


def _run(installer: Installer, workspace: Workspace, *, check: bool) -> InstallerStatus:
    checkout = primary_checkout()
    if checkout is None:
        return InstallerStatus(
            installer,
            InstallState.UNAVAILABLE,
            "loregarden is not running from a git checkout, so there is no path to install",
        )
    try:
        completed = subprocess.run(
            _argv(checkout, installer, workspace, check=check),
            capture_output=True,
            text=True,
            timeout=INSTALLER_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("%s installer failed to run for %s: %s", installer, workspace.slug, exc)
        return InstallerStatus(installer, InstallState.UNAVAILABLE, str(exc))
    reported = _first_line(completed.stdout)
    state = _REPORTED.get(reported.split(":", 1)[0].split(" ", 1)[0])
    if state is None:
        detail = _first_line(completed.stderr) or reported or f"exit {completed.returncode}"
        return InstallerStatus(installer, InstallState.UNAVAILABLE, detail.removeprefix("skip: "))
    return InstallerStatus(installer, state, reported)


def installer_status(workspace: Workspace, installer: Installer) -> InstallerStatus:
    """What ``--check`` says. Writes nothing."""
    return _run(installer, workspace, check=True)


def install(workspace: Workspace, installer: Installer) -> InstallerStatus:
    """Write or refresh the block, then report what ``--check`` says now."""
    result = _run(installer, workspace, check=False)
    if result.state is InstallState.UNAVAILABLE:
        raise InstallerError(result.detail)
    return installer_status(workspace, installer)
