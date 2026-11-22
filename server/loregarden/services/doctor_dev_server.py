"""Doctor check: did the dev server's last reload land?

The dev-server supervisor (`loregarden.dev_supervisor`) records each reload's
outcome in `data/dev-server/status.json` under the checkout it serves. A
**refused** reload leaves the previous build serving, so a fix an agent made is
not live and its test reads like the fix failed; a **failed** one may leave
nothing serving. Either is reported here, with the traceback's last lines and
the log path, rather than only on the terminal that runs the server.

Not in `DISPATCH_PREFLIGHT_CHECKS`: parking every dispatch until someone fixes
the build would stop the agent that is fixing it. `scripts/reload-backend.sh`
reports the same outcome to whoever asked for the reload.
"""

from __future__ import annotations

import os
from pathlib import Path

from loregarden.config import settings
from loregarden.dev_supervisor import STATE_DIR, ReloadState, read_status
from loregarden.models.domain import (
    DoctorCheck,
    DoctorFinding,
    DoctorStatus,
    FollowMainState,
    Workspace,
)
from loregarden.services.follow_main_status import read_follow_status
from sqlmodel import Session

#: Traceback lines carried in the finding; the log holds the rest.
FINDING_TAIL = 8
#: Outcomes that leave the current build serving.
_SERVING = frozenset({ReloadState.OK, ReloadState.STARTING})


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (
        ProcessLookupError
    ):  # silent-ok: no such process is the answer this asks for, not a failure
        return False
    except PermissionError:  # silent-ok: the process exists under another user; that is the answer
        return True
    return True


def check_dev_server_reload(
    _session: Session, _workspace: Workspace, _repo_root: Path
) -> DoctorFinding:
    """The supervisor's last recorded reload, read from the server's own checkout.

    Read from `settings.repo_root` rather than the workspace's root: there is one
    control plane, and a worktree run's root is not where it records anything.
    """
    state_dir = settings.repo_root / STATE_DIR
    try:
        status = read_status(state_dir)
    except ValueError as exc:
        return DoctorFinding(
            check=DoctorCheck.DEV_SERVER_RELOAD,
            status=DoctorStatus.WARN,
            finding=f"The dev server's reload status is unreadable: {exc}",
            remediation="Run ./scripts/reload-backend.sh to record a fresh one.",
        )
    if status is None or not _alive(status.supervisor_pid):
        return DoctorFinding(
            check=DoctorCheck.DEV_SERVER_RELOAD,
            status=DoctorStatus.PASS,
            finding="No dev-server supervisor is running here; not applicable.",
        )
    if status.state in _SERVING:
        return DoctorFinding(
            check=DoctorCheck.DEV_SERVER_RELOAD,
            status=DoctorStatus.PASS,
            finding=f"Last reload: {status.state.value} at {status.at} ({status.detail}).",
        )
    tail = "\n".join(status.log_tail[-FINDING_TAIL:])
    return DoctorFinding(
        check=DoctorCheck.DEV_SERVER_RELOAD,
        status=DoctorStatus.FAIL,
        finding=f"The last dev-server reload was {status.state.value} at {status.at}: "
        f"{status.detail}.\n{tail}",
        remediation=(
            f"Fix the error, then run ./scripts/reload-backend.sh. Full log: {status.log_file}"
        ),
    )


#: What a person does about each way the server stops following main.
_FOLLOW_REMEDIATION = {
    FollowMainState.OFF_BASE: "Check out main in the server's checkout to follow it again, or "
    "set LOREGARDEN_FOLLOW_MAIN=0 if pinning this branch is deliberate.",
    FollowMainState.DIVERGED: "Push or move the local commits off main, then `git reset` main to "
    "origin/main; the next CI poll tick pulls and reloads.",
    FollowMainState.REFUSED: "Commit, move or discard the uncommitted changes to the files main "
    "changed; the next CI poll tick pulls and reloads.",
    FollowMainState.GIT_FAILED: "Run the git command it names in the server's checkout to see "
    "why; a network failure clears on its own.",
}


def check_server_follows_main(
    _session: Session, _workspace: Workspace, _repo_root: Path
) -> DoctorFinding:
    """Is the dev server keeping up with what the merge queue lands?

    `services.follow_main` records each CI poll tick's outcome under the
    server's checkout; one that leaves the server on an old build is a WARN
    naming why, since a fix that landed is not running until it clears.
    """
    try:
        status = read_follow_status(settings.repo_root)
    except ValueError as exc:
        return DoctorFinding(
            check=DoctorCheck.SERVER_FOLLOWS_MAIN,
            status=DoctorStatus.WARN,
            finding=f"The server's follow-main status is unreadable: {exc}",
            remediation="It is rewritten on the next CI poll tick; delete it if it persists.",
        )
    if status is None:
        return DoctorFinding(
            check=DoctorCheck.SERVER_FOLLOWS_MAIN,
            status=DoctorStatus.PASS,
            finding="This server does not follow main (not the dev server, or "
            "LOREGARDEN_FOLLOW_MAIN is off); not applicable.",
        )
    remediation = _FOLLOW_REMEDIATION.get(status.state)
    if remediation is None:
        return DoctorFinding(
            check=DoctorCheck.SERVER_FOLLOWS_MAIN,
            status=DoctorStatus.PASS,
            finding=f"Following main: {status.state.value} at {status.at} ({status.detail}).",
        )
    return DoctorFinding(
        check=DoctorCheck.SERVER_FOLLOWS_MAIN,
        status=DoctorStatus.WARN,
        finding=f"The server is not following main ({status.state.value} at {status.at}): "
        f"{status.detail}. What the merge queue lands is not running here.",
        remediation=remediation,
    )
