"""Handing work to a background thread, reachable from below.

Starting an orchestration lives in `run_service`, which imports the builtin
driver, which imports most of the orchestrator — so anything lower that wants
to start work closes a cycle by asking for it. `ApprovalService` did exactly
that: approving a gate resumes the ticket, so `orchestration` imported
`run_service`, which imports `orchestration`.

Same shape as `queue_dispatch`, and the same answer: the *capability* is
declared down here, the *implementation* is installed from above at import
time, and the call is resolved at runtime. `run_service` installs itself; every
entry point already imports it transitively through the API, MCP and CLI apps.

This is a seam for the one operation low modules genuinely need — "run this
ticket's pipeline, on a thread, later". It is not a general service locator, and
nothing should be added here that a caller could just import.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class OrchestrationScheduler(Protocol):
    def __call__(self, ticket_id: str, /, **kwargs: Any) -> None: ...


_scheduler: Callable[..., None] | None = None


def set_orchestration_scheduler(scheduler: Callable[..., None] | None) -> None:
    """Install the real scheduler. Called by `run_service` on import."""
    global _scheduler  # noqa: PLW0603 — one process-wide wiring point
    _scheduler = scheduler


def schedule_orchestration(ticket_id: str, **kwargs: Any) -> None:
    """Start this ticket's pipeline in the background.

    Raises when nothing is installed rather than dropping the request: a
    silently unscheduled resume is a ticket that sits at an approved gate
    forever, which is precisely the class of failure this branch exists to
    remove.
    """
    if _scheduler is None:
        raise RuntimeError(
            "No orchestration scheduler is installed; "
            "import loregarden.services.run_service in this entry point"
        )
    _scheduler(ticket_id, **kwargs)
