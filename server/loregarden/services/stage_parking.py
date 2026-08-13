"""Park a stage on a question instead of blocking the ticket.

Two pre-dispatch checks can decide a stage should not start yet — the boundary
check (`services.handoff_boundary`) and the environment preflight
(`services.doctor`) — and neither is looking at something wrong with the *ticket*.
A checkout on the wrong branch or with `core.bare` set is a fact about this
machine, and the ticket is fine.

Blocking is the wrong shape for that. It costs a deliberate `requeue` to undo and
spends the stage's retry budget on the way; an approval costs a click. The budget
is refunded for the same reason: parking to ask a question is not an attempt at
the work, and charging for it would walk a ticket toward its breaker every time a
human touched the tree.
"""

from __future__ import annotations

from loregarden.models.domain import AgentRun, Approval, Ticket
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.stage_retry_budget import clear_stage_dispatches
from sqlmodel import Session


def park_stage(
    session: Session,
    *,
    run: AgentRun,
    ticket: Ticket,
    title: str,
    impact: str,
) -> Approval:
    """Raise an inbox approval for `run`'s stage and refund its dispatch budget.

    The stage moves to AWAITING, which is what the orchestrator reads to pause
    rather than block. `impact` is what the person opening the inbox will have to
    act on, so it should name the remediation and not only the diagnosis.
    """
    approval = OrchestrationCallbackService(session).request_approval(
        ticket,
        stage_key=run.stage_key,
        title=title,
        impact=impact,
        level="high",
    )
    clear_stage_dispatches(session, ticket.id, run.stage_key)
    return approval
