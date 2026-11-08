"""Every path that starts or finishes a stage the criteria check guards asks for it first.

The two drivers check before they choose a stage (`criterion_gate.before_next_stage`).
A stage started by hand (MCP `start_stage`, an HTTP start through `start_run`),
a fan-out, and the terminal sweep's `finalize_workflow` did not, so a ticket
reaching its gate by one of them skipped the check, and only the report-only
`done_tickets_have_settled_criteria` invariant noticed, after it was done.

Each now calls `refuse_unchecked_gate` before its first write. The check itself
is installed by `criterion_gate` on import, because it sends gaps back and
blocks through `orchestration_callbacks`, which imports `orchestration`, one of
the callers here (the `criterion_split` creator pattern). Every entry point
imports `criterion_gate` through `builtin_orchestrator`; a process that did not
fails loudly rather than skipping the check.
"""

from __future__ import annotations

from collections.abc import Callable

from loregarden.models.domain import OrchestrationRun, Ticket
from sqlmodel import Session

#: Arguments: session, ticket, the stage about to start (or finish), and the
#: orchestration run starting it, when there is one.
GateCheck = Callable[[Session, Ticket, str, OrchestrationRun | None], None]

_check: GateCheck | None = None


class GateCriteriaUnanswered(ValueError):
    """The stage is one the criteria check guards, and the check did not pass.

    What it did instead (sent the gaps back, split them off, blocked the ticket)
    is already recorded on the ticket's history when this is raised.
    """


def set_gate_check(check: GateCheck | None) -> None:
    """Install the real check. Called by `criterion_gate` on import."""
    global _check  # noqa: PLW0603 — one process-wide wiring point, as in `criterion_split`
    _check = check


def refuse_unchecked_gate(
    session: Session,
    ticket: Ticket,
    *,
    stage_key: str,
    orchestration_run_id: str | None = None,
) -> None:
    """Raise `GateCriteriaUnanswered` unless ``stage_key`` may start as far as the criteria go.

    ``orchestration_run_id`` is the run starting it, which a ticket the check
    blocks is blocked under; without one, that block refuses the start instead.
    """
    if _check is None:
        raise RuntimeError(
            "No criteria check is installed; import loregarden.services.criterion_gate "
            "in this entry point"
        )
    orch_run = session.get(OrchestrationRun, orchestration_run_id) if orchestration_run_id else None
    _check(session, ticket, stage_key, orch_run)
