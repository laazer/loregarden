"""Vocabulary for an agent's OS process: what `ps` said, and what a stop did.

Split out of `enums` so each process-facing answer sits beside the other —
both exist because a bool collapsed outcomes the operator must tell apart.
"""

from __future__ import annotations

from enum import StrEnum


class ProcessState(StrEnum):
    """What `ps` said about a pid — including that it could not say.

    UNKNOWN is the reason this is not a bool. A `ps` that timed out on a loaded
    machine answered nothing about the process, and reading that as GONE is how a
    slow boot got a live agent's run failed (lg-run-durability-862).
    """

    #: The pid is held — and, for `liveness`, held by this run's own process.
    ALIVE = "alive"
    #: Nothing holds the pid — or, for `liveness`, a stranger does.
    GONE = "gone"
    #: `ps` could not answer: timed out, could not run, or said nothing usable.
    UNKNOWN = "unknown"


class DetachedStopOutcome(StrEnum):
    """What an operator stop did to a detached agent process.

    Recorded rather than returned as a bool because the three failures are not
    the same event and the operator's next move differs for each. "Nothing was
    signalled" is only good news if the process was already gone.
    """

    #: SIGTERM (then SIGKILL if needed) reached the process group.
    SIGNALLED = "signalled"
    #: The process is already gone; the run settles as it does today.
    ALREADY_GONE = "already_gone"
    #: A live process holds the pid, but it is not this run's — a reused pid, or
    #: not a session leader as every agent this control plane spawns is. Nothing
    #: is signalled: killing a stranger is worse than failing to stop.
    NOT_OURS = "not_ours"


__all__ = ["DetachedStopOutcome", "ProcessState"]
