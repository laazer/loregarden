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
from loregarden.dev_supervisor import ReloadState, read_status
from loregarden.models.domain import DoctorCheck, DoctorFinding, DoctorStatus, Workspace
from sqlmodel import Session

#: Where `scripts/dev-server.sh` points the supervisor, under the server's checkout.
STATE_DIR = Path("data") / "dev-server"
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
