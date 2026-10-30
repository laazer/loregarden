"""Vocabulary for a chat wait: outstanding vs settled, and why register refused.

A chat turn that backgrounds work records one wait. The control plane either
starts a continuation AgentRun or writes a Loregarden system message. These
closed sets are the statuses and refuse reasons of that row — not strings.
"""

from __future__ import annotations

from enum import StrEnum


class ChatWaitStatus(StrEnum):
    """Lifecycle of one `chat_waits` row."""

    OUTSTANDING = "outstanding"
    CONTINUED = "continued"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    CAPPED = "capped"
    INTERRUPTED = "interrupted"


class ChatWaitRefuseReason(StrEnum):
    """Why `register` wrote no row. Returned as `error_kind`."""

    NOT_ALIVE = "not_alive"
    SAME_SESSION = "same_session"
    ALREADY_WAITING = "already_waiting"
    NO_IN_FLIGHT_TURN = "no_in_flight_turn"
    MISSING_LOG_PATH = "missing_log_path"
    MISSING_COMMAND = "missing_command"
    INVALID_LOCATOR = "invalid_locator"


class ChatWaitUnknownPolicy(StrEnum):
    """What a poller does when `liveness` returns UNKNOWN."""

    SKIP = "skip"
    INTERRUPT = "interrupt"
