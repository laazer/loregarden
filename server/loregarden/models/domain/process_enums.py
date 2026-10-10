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


class AgentTransport(StrEnum):
    """How an agent process was detached from the server that spawned it.

    The member is FILE rather than SETSID because this value is read by a
    person: it crosses the wire verbatim and the run log modal renders it as
    one word, so server and client share one vocabulary instead of a server
    word translated at the boundary.

    The mechanism is the same either way — `start_new_session=True` plus the
    `sh -c` wrapper of `services.run_output_files`, which both transports run
    identically. What TMUX buys is operator attach: a human can `tmux attach`
    to a live agent. It is also strictly *less* reliable, since its server can
    die independently of both the agent and loregarden, which is why survival
    rests on the wrapper rather than on it.
    """

    #: The wrapper is a tmux pane, so the run has a session to attach to.
    TMUX = "tmux"
    #: The wrapper is a plain session-leader subprocess; output is the files only.
    FILE = "file"


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


__all__ = ["AgentTransport", "DetachedStopOutcome", "ProcessState"]
