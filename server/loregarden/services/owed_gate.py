"""A finished stage whose transition gate nobody ran.

The builtin driver runs a stage's gate in the pass that ran the stage
(`BuiltinOrchestrator._advance_after_stage`). A stage can finish outside that
pass: the server reloads and its agent completes with no driver left, or the
stage parks for a sign-off and a person's approval marks it done. The next run
sees DONE, picks the stage after it, and the gate is never evaluated.

That is how lmkr-execution-leaves-11's `implement` reached Testing on
2026-11-23: a reload killed orch_000910 before the agent finished, and
orch_539be3 started at `testing`, past a gate that failed when it finally ran.

So before it picks a stage the driver asks `owed_gate` whether the last stage an
agent finished still owes its gate, and runs it first. Only runs under the
builtin driver count: an external driver gates nothing, and a chat turn or a
stage run by hand carries no orchestration of this kind.
"""

from __future__ import annotations

from dataclasses import dataclass

from loregarden.models.domain import (
    AgentRun,
    Artifact,
    ArtifactKind,
    DomainEvent,
    EventType,
    GateOutcome,
    OrchestrationDriver,
    OrchestrationRun,
    RunStatus,
    StageStatus,
    Ticket,
    WorkflowStageDef,
)
from loregarden.services.gate_runner import GateRunResult
from loregarden.services.stage_retry_budget import (
    foreign_gate_failure_artifact_title,
    gate_failure_artifact_title,
)
from loregarden.services.studio_routing import is_terminal_stage
from loregarden.services.ticket_workspace_binding import workspace_for_ticket
from loregarden.services.ticket_worktree import resolve_ticket_root
from loregarden.services.workspace_paths import resolve_workspace_root
from pydantic import BaseModel
from sqlmodel import Session, col, select

_LET_THROUGH = frozenset({GateOutcome.PASSED, GateOutcome.SKIPPED, GateOutcome.DISABLED})


class _GatePayload(BaseModel):
    outcome: GateOutcome
    from_stage: str = ""


@dataclass(frozen=True)
class OwedGate:
    """The stage whose gate is owed, and the run whose work it would judge."""

    stage: WorkflowStageDef
    run: AgentRun


def finished_runs(session: Session, ticket_id: str) -> list[AgentRun]:
    """The ticket's succeeded stage runs under the builtin driver, newest first.

    An ask (`asked_by_run_id`) resumes an earlier stage's session to answer a
    question; it finishes no stage.
    """
    return list(
        session.exec(
            select(AgentRun)
            .join(OrchestrationRun, col(OrchestrationRun.id) == col(AgentRun.orchestration_run_id))
            .where(
                AgentRun.ticket_id == ticket_id,
                AgentRun.status == RunStatus.SUCCEEDED,
                AgentRun.asked_by_run_id == "",
                col(AgentRun.finished_at).is_not(None),
                OrchestrationRun.driver == OrchestrationDriver.BUILTIN_AUTOPILOT,
            )
            .order_by(col(AgentRun.finished_at).desc(), col(AgentRun.id).desc())
        ).all()
    )


def owed_gate(
    session: Session,
    ticket: Ticket,
    stages: list[WorkflowStageDef],
    stage_map: dict[str, StageStatus],
) -> OwedGate | None:
    """The gate the last finished stage still owes, or None.

    Only the last one: re-judging a stage the ticket has moved past would send
    it back over work a later gate has already read. The invariant
    `no_stage_runs_past_an_unrun_gate` reports one left behind.
    """
    last = next(iter(finished_runs(session, ticket.id)), None)
    if last is None:
        return None
    stage = next((s for s in stages if s.key == last.stage_key), None)
    if stage is None or is_terminal_stage(stage):
        return None
    if stage_map.get(stage.key) is not StageStatus.DONE or gate_settled(session, last):
        return None
    return OwedGate(stage=stage, run=last)


def gate_settled(session: Session, run: AgentRun) -> bool:
    """Whether a gate judged ``run``'s stage after it finished, to a conclusion.

    A failing evaluation concludes when the driver acted on it: it files an error
    artifact as it reroutes or blocks, or lets the ticket past somebody else's
    violation. A failing one with no such artifact was cut off part-way (a
    driver killed while the mechanical fixers ran), so the gate is still owed.
    """
    latest = _latest_evaluation(session, run)
    if latest is None:
        return False
    event, payload = latest
    if payload.outcome in _LET_THROUGH:
        return True
    acted = session.exec(
        select(Artifact.id).where(
            Artifact.ticket_id == run.ticket_id,
            Artifact.kind == ArtifactKind.ERROR,
            col(Artifact.title).in_(
                [
                    gate_failure_artifact_title(run.stage_key),
                    foreign_gate_failure_artifact_title(run.stage_key),
                ]
            ),
            col(Artifact.created_at) >= event.created_at,
        )
    ).first()
    return acted is not None


def tree_passed_since(session: Session, run: AgentRun) -> bool:
    """Whether any stage's gate has passed on the ticket since ``run`` finished."""
    return any(p.outcome is GateOutcome.PASSED for _, p in _evaluations_since(session, run))


def _latest_evaluation(session: Session, run: AgentRun) -> tuple[DomainEvent, _GatePayload] | None:
    return next(
        (
            found
            for found in _evaluations_since(session, run)
            if found[1].from_stage == run.stage_key
        ),
        None,
    )


def _evaluations_since(session: Session, run: AgentRun) -> list[tuple[DomainEvent, _GatePayload]]:
    """Gate evaluations on the run's ticket after it finished, newest first."""
    events = session.exec(
        select(DomainEvent)
        .where(
            DomainEvent.ticket_id == run.ticket_id,
            DomainEvent.type == EventType.GATE_EVALUATED,
            col(DomainEvent.created_at) >= run.finished_at,
        )
        .order_by(col(DomainEvent.created_at).desc())
    ).all()
    return [(event, _GatePayload.model_validate_json(event.payload_json)) for event in events]


def unrunnable_owed_gate(session: Session, ticket: Ticket, owed: OwedGate) -> GateRunResult | None:
    """A skip to record when the tree the run wrote in is gone, or None.

    With the ticket's worktree removed, the gate would run in the shared
    checkout and pass on a tree holding none of this work. The next dispatch
    cuts the worktree again from the ticket's branch, and that stage's own gate
    judges it.
    """
    workspace = workspace_for_ticket(session, ticket)
    if workspace is None or not owed.run.worktree_id:
        return None
    if resolve_ticket_root(session, ticket, workspace) != resolve_workspace_root(workspace):
        return None
    return GateRunResult(
        ok=True,
        outcome=GateOutcome.SKIPPED,
        message=(
            f"the worktree {owed.run.run_code} wrote in is gone, so the gate it owed was not "
            "run; the next stage's gate judges the ticket's branch"
        ),
    )
