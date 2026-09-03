"""Stop a detached agent from a server process that may not have spawned it.

Before 317, a stop reached the agent because the supervising process owned the
pipe: `permission_bridge._check_cancel` polled `cancel_requested_at` and killed
its own child. A detached child in its own session is no longer reachable that
way, and after 470 a run can outlive the process that started it entirely — so
stop would become a flag nobody acts on. That is the failure #170 fixed for
external-harness runs, reintroduced by detachment (471).

Every guard here exists to answer one question: is this pid still the process we
think it is? Signalling a reused pid is worse than failing to stop, and the blast
radius is a process *group*, so a wrong answer takes out a stranger and its
children rather than one stray process.

Three checks, and none is redundant:

1. `still_running(pid, identity)` — the pid is alive and its start-time
   fingerprint matches. Fails closed with no recorded identity.
2. `os.getpgid(pid) == pid` — every agent this control plane spawns is a session
   leader (`start_new_session=True` in `cli._spawn_print_process`), so a live pid
   that is *not* its own group leader cannot be one of ours, whatever the
   fingerprint says.
3. The identity is re-read immediately before each signal, to keep the window
   between deciding and signalling as small as it can be made.

That window cannot be closed on POSIX without `pidfd`, which is Linux-only and
this control plane runs on macOS too. What is left is a race between the last
check and the `killpg` a few microseconds later, in which the process would have
to exit *and* its pid be reused *and* the new holder be a session leader. Stated
rather than hidden, because a reader deserves to know the guarantee is "as
narrow as POSIX allows", not "impossible".
"""

from __future__ import annotations

import logging
import os
import signal
import time

from loregarden.models.domain import AgentRun
from loregarden.models.domain.enums import DetachedStopOutcome
from loregarden.services.process_identity import identify, still_running

logger = logging.getLogger(__name__)

#: How long SIGTERM gets before SIGKILL. Long enough for a CLI to flush its
#: output and exit cleanly — that output is the run's durable record (468) —
#: short enough that an operator pressing stop sees it happen.
GRACE_SECONDS = 5.0

_POLL_SECONDS = 0.1


def _is_ours(run: AgentRun) -> bool:
    """Whether this pid is still this run's process, and still one we spawned."""
    if not still_running(run.agent_pid, run.agent_pid_identity):
        return False
    try:
        return os.getpgid(run.agent_pid) == run.agent_pid
    except (OSError, ProcessLookupError):
        return False


def stop_detached_process(
    run: AgentRun, *, grace_seconds: float = GRACE_SECONDS
) -> DetachedStopOutcome:
    """Signal a detached agent's process group. Never raises.

    Returns what happened rather than a bool: an operator whose stop signalled
    nothing needs to know whether the run had already finished or whether this
    control plane refused to signal something it could not vouch for.
    """
    if run.agent_pid is None:
        return DetachedStopOutcome.ALREADY_GONE
    if identify(run.agent_pid) is None:
        # Nothing holds the pid at all. This is the only reading under which
        # "already gone" is true — `still_running` would also answer False for a
        # live pid whose fingerprint does not match, and reporting *that* as
        # gone would tell an operator the run finished when in fact a stranger
        # holds the number and this run's fate is unknown.
        return DetachedStopOutcome.ALREADY_GONE
    if not _is_ours(run):
        logger.warning(
            "Refusing to signal pid %s for run %s: it is alive but not this run's process",
            run.agent_pid,
            run.run_code,
        )
        return DetachedStopOutcome.NOT_OURS

    pgid = run.agent_pid
    _signal_group(run, pgid, signal.SIGTERM)

    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if not still_running(run.agent_pid, run.agent_pid_identity):
            return DetachedStopOutcome.SIGNALLED
        time.sleep(_POLL_SECONDS)

    # Still there after the grace period. Re-check ownership rather than
    # assuming it held: the process may have exited and its pid been reused
    # while we waited, which is exactly the case SIGKILL must not hit.
    if _is_ours(run):
        _signal_group(run, pgid, signal.SIGKILL)
    return DetachedStopOutcome.SIGNALLED


def _signal_group(run: AgentRun, pgid: int, sig: signal.Signals) -> None:
    """Signal the group, tolerating a process that exited in the meantime."""
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        # It exited between the check and the signal, which is the outcome the
        # caller wanted anyway.
        pass
    except OSError as exc:
        logger.warning(
            "Could not %s process group %s for run %s: %s", sig.name, pgid, run.run_code, exc
        )
