"""A pid, plus enough to know it is still the same process.

317 detaches the agent subprocess so a server restart stops killing live turns.
The moment a run outlives the process that spawned it, "is pid 4821 alive?"
stops being a useful question: pids are recycled, and on a busy machine the
number that named an agent an hour ago may name a shell, a test runner, or
nothing at all.

Reattaching to a stranger is worse than declaring the run dead. A wrong
"orphaned" costs one re-run; a wrong "still mine" means the control plane
adopts, reports on, and eventually signals a process it does not own.

So a pid is recorded together with a fingerprint the OS will not repeat: the
process start time, which the kernel assigns and which no later process with the
same pid can share. `identify` reads it, `still_running` re-reads it and compares.

`ps` rather than `/proc`, because this control plane runs on macOS as well as
Linux and `/proc` does not exist there. `psutil` would be the obvious answer and
is not a dependency; adding one for two fields is not worth it while `ps -o
lstart=` answers on both platforms.
"""

from __future__ import annotations

import logging
import subprocess

from loregarden.db.session import engine
from loregarden.models.domain import AgentRun
from sqlmodel import Session

logger = logging.getLogger(__name__)

#: Long enough that a loaded machine still answers, short enough that a wedged
#: `ps` cannot stall a boot sequence that is deciding what to reattach to.
_PS_TIMEOUT_SECONDS = 5


def identify(pid: int) -> str | None:
    """A fingerprint for `pid` that a later process reusing it cannot match.

    The process start time as the kernel reports it. Returns None when the
    process is already gone or `ps` cannot answer — callers treat that as "no
    identity", never as "matches".
    """
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True,
            text=True,
            timeout=_PS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        logger.warning("Could not read process identity for pid %s", pid, exc_info=True)
        return None
    if result.returncode != 0:
        return None
    stamp = result.stdout.strip()
    return stamp or None


def still_running(pid: int | None, identity: str | None) -> bool:
    """Whether `pid` is alive *and* is still the process `identity` came from.

    Fails closed in the direction that matters. Without a recorded identity this
    answers False rather than falling back to a bare liveness check: a run from
    before identity was recorded is one whose pid cannot be trusted, and
    treating it as alive is the adoption mistake this module exists to prevent.
    """
    if pid is None or not identity:
        return False
    current = identify(pid)
    if current is None:
        return False
    return current == identity


def record_process_identity(run_id: str, pid: int) -> None:
    """Store the pid and its fingerprint on the run, in one write.

    Its own short-lived session: the session driving the run may sit in a
    transaction old enough that a reader on another connection — which is
    exactly who reattaches — would not see this.

    Best-effort. A run whose identity could not be recorded still runs; it is
    simply one that a later process will decline to adopt, which is the safe
    direction.
    """
    identity = identify(pid)
    try:
        with Session(engine) as session:
            run = session.get(AgentRun, run_id)
            if run is None:
                return
            run.agent_pid = pid
            run.agent_pid_identity = identity or ""
            session.add(run)
            session.commit()
    except Exception:  # noqa: BLE001 — never fail a run over its own bookkeeping
        logger.warning("Could not record process identity for run %s", run_id, exc_info=True)
