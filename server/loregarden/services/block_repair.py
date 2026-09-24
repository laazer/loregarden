"""One repair turn for a block an agent can clear, then never another.

A `harness` or `work` block (749) used to stop the ticket until a person asked
what happened, asked for a fix, and requeued by hand. The orchestrator now
spends one visible turn on it: the blocked stage is re-armed with the `repair`
agent pinned and the block in its brief, and the loop re-runs it inline —
the same move `gate_recovery` makes for a failing gate. `fixed` is that rerun
passing; `reroute` and `escalate` are its ordinary `needs_rework` / `blocked`
reports, and a `blocked` from the repair agent hands the block to a person
with the kind it named (750).

Caps, each a fact rather than a counter: the rerun is a dispatch of the stage,
so the retry budget charges it; a block raised by the repair agent itself is
never repaired again; a stage that already had its repair since the last
human requeue does not get another. Every step is an `OrchestratorDecision`.

The monitor keeps its rule — it never dispatches an agent. This runs inside
an orchestration, on the run's own `auto_repair` dial.

`offer_repair` has exactly one caller, `block_settlement.settle_block`, and
`test_block_settlement` fails any other (802): a block writer that classified
without offering the repair turn is the defect that module exists to close, so
the pair must not come apart. This module decides whether the turn can be armed
and *says why not*; recording that answer is the settlement's job, so there is
exactly one decision per block.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from loregarden.agents.registry import REPAIR_AGENT_ID, get_agent
from loregarden.models.domain import (
    AgentRun,
    Artifact,
    ArtifactKind,
    BlockKind,
    OrchestrationRun,
    OrchestratorDecision,
    StageStatus,
    Ticket,
    WorkflowInstance,
    WorkflowStageDef,
)
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from loregarden.services.studio_routing import repair_pin_applies
from loregarden.services.workflow_routing import apply_stage_route
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

#: The kinds an agent can clear. A decision or a person's hands are not.
REPAIRABLE_KINDS = frozenset({BlockKind.HARNESS, BlockKind.WORK})


@dataclass(frozen=True)
class RepairOffer:
    """Whether the stage was re-armed for its repair turn, and why when it was not.

    `reason` is never empty on a refusal: it is the sentence a person reads on
    the history rail, and the whole point of 802 is that a block which got no
    repair says so out loud instead of looking handled.
    """

    armed: bool
    reason: str = ""


def _latest_repair_run(session: Session, ticket: Ticket, stage_key: str) -> AgentRun | None:
    return session.exec(
        select(AgentRun)
        .where(
            AgentRun.ticket_id == ticket.id,
            AgentRun.stage_key == stage_key,
            AgentRun.agent_id == REPAIR_AGENT_ID,
        )
        .order_by(AgentRun.created_at.desc())
    ).first()


def _latest_human_requeue(session: Session, ticket: Ticket, stage_key: str) -> Artifact | None:
    return session.exec(
        select(Artifact)
        .where(
            Artifact.ticket_id == ticket.id,
            Artifact.kind == ArtifactKind.CONTEXT,
            Artifact.title == f"Requeued — {stage_key}",
        )
        .order_by(Artifact.created_at.desc())
    ).first()


def repair_already_spent(session: Session, ticket: Ticket, stage_key: str) -> bool:
    """One repair per block: a stage keeps its repair until a person requeues it."""
    last = _latest_repair_run(session, ticket, stage_key)
    if last is None:
        return False
    requeue = _latest_human_requeue(session, ticket, stage_key)
    return requeue is None or requeue.created_at <= last.created_at


def repair_turns_spent(session: Session, ticket: Ticket, stage_key: str) -> int:
    """How many repair turns this stage has had since a person last requeued it.

    `repair_already_spent` is this capped at one, which is the block repair's
    own rule. The landing resolver counts instead, because the operator sets
    its cap (`max_conflict_resolve_attempts`) in the git automation panel.
    """
    requeue = _latest_human_requeue(session, ticket, stage_key)
    query = select(AgentRun.id).where(
        AgentRun.ticket_id == ticket.id,
        AgentRun.stage_key == stage_key,
        AgentRun.agent_id == REPAIR_AGENT_ID,
    )
    if requeue is not None:
        query = query.where(AgentRun.created_at > requeue.created_at)
    return len(session.exec(query).all())


def repair_pinned(ticket: Ticket, stage_key: str) -> bool:
    """The loop's question after a dispatch: is a repair rerun of this stage
    waiting? True only until the dispatch consumes the pin."""
    return (
        ticket.workflow_stage_key == stage_key
        and ticket.workflow_stage_status is StageStatus.PENDING
        and (ticket.next_agent or "") == REPAIR_AGENT_ID
    )


def repair_brief(stage_key: str, kind: BlockKind, message: str) -> str:
    return (
        f"The '{stage_key}' stage blocked and the orchestrator classified the block as "
        f"'{kind.value}' — one an agent can clear. You are its one repair turn: reproduce the "
        f"block, fix its cause, then finish this stage's own work and report `pass`. If what "
        f"you find is a decision or needs a person's hands, report `blocked` with that "
        f"`blocked_kind` instead — there will be no second repair.\n\n{message}"
    )


def offer_repair(
    session: Session,
    ticket: Ticket,
    orch_run: OrchestrationRun | None,
    *,
    instance: WorkflowInstance | None,
    stages: list[WorkflowStageDef] | None,
    transitions: list[dict[str, str]],
    stage_key: str,
    kind: BlockKind,
    message: str,
    failed_agent: str,
) -> RepairOffer:
    """Re-arm the stage for its repair turn, or say why not.

    Reached through `block_settlement.settle_block`, which pairs it with the
    classification and records the refusal this returns. Every exit carries a
    sentence, because a silent `False` here was how a block that nothing
    handled came to look handled (802).
    """
    if orch_run is None:
        return RepairOffer(
            False,
            f"The block on '{stage_key}' was recorded outside an orchestration run, so there "
            "is no orchestrator to spend a repair turn; it waits for a person.",
        )
    if not orch_run.auto_repair:
        return RepairOffer(
            False,
            f"Auto-repair is off for this run, so the block on '{stage_key}' goes to a person "
            "rather than taking a repair turn.",
        )
    if failed_agent == REPAIR_AGENT_ID:
        return RepairOffer(
            False,
            f"The repair turn on '{stage_key}' blocked too ({kind.value}); no second repair — "
            "a person reads it from here.",
        )
    if kind not in REPAIRABLE_KINDS:
        return RepairOffer(
            False,
            f"A {kind.value} block on '{stage_key}' is not one an agent can clear; it waits "
            "for a person.",
        )
    if (ticket.next_agent or "") == REPAIR_AGENT_ID:
        # The pin from the last offer was never consumed — the dispatch resolved
        # to someone else. Re-arming would repeat that until the budget ran out.
        return RepairOffer(
            False,
            f"A repair pin on '{stage_key}' was not honoured by the dispatch; not re-armed. "
            "Blocked for a person.",
        )
    if instance is None or stages is None:
        return RepairOffer(
            False,
            f"The workflow behind '{stage_key}' could not be resolved, so the stage cannot be "
            "re-armed for a repair turn; blocked for a person.",
        )
    stage = next((s for s in stages if s.key == stage_key), None)
    if stage is None or not repair_pin_applies(stage):
        # `repair_pin_applies` now admits a parallel stage — the dispatch runs
        # its repair turn as one sequential run under the pin (802) — so what
        # is left here is a gate stage, which runs its own checker, and a stage
        # key no template knows.
        return RepairOffer(
            False,
            f"'{stage_key}' is a {stage.stage_type if stage else 'unknown'} stage, which the "
            "repair turn cannot take over; blocked for a person.",
        )
    if get_agent(REPAIR_AGENT_ID) is None:
        # Pinning an agent that cannot resolve would re-arm the stage for the
        # worker again, forever. Say so once and leave the block for a person.
        return RepairOffer(
            False,
            f"The '{REPAIR_AGENT_ID}' agent is not registered, so the block on '{stage_key}' "
            "cannot take a repair turn; blocked for a person.",
        )
    if repair_already_spent(session, ticket, stage_key):
        return RepairOffer(
            False,
            f"'{stage_key}' already had its repair turn since the last requeue; blocked for a "
            "person rather than repaired again.",
        )

    apply_stage_route(
        ticket,
        instance,
        stages,
        transitions,
        from_key=stage_key,
        outcome="reject",
        next_stage_key=stage_key,
        next_agent=REPAIR_AGENT_ID,
        blocking_issues=repair_brief(stage_key, kind, message),
        orch_run=orch_run,
    )
    session.add(ticket)
    session.add(instance)
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.DISPATCHED_REPAIR,
        stage_key=stage_key,
        reason=(
            f"Block on '{stage_key}' is {kind.value}; re-armed the stage for one repair turn "
            f"under the '{REPAIR_AGENT_ID}' agent instead of waiting for a person."
        ),
        evidence={"block_kind": kind.value, "failed_agent": failed_agent},
    )
    session.commit()
    return RepairOffer(True)


def record_repair_outcome(session: Session, ticket: Ticket, run: AgentRun) -> None:
    """The repair turn passed: the block is gone, in every place it was recorded."""
    ticket.block_kind = None
    session.add(ticket)
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.REPAIRED,
        stage_key=run.stage_key,
        reason=f"The repair turn cleared the block on '{run.stage_key}' and the stage passed.",
        run_id=run.id,
    )
    session.commit()
