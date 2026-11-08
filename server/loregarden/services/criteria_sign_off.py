"""A ticket's gate signs itself off when its criteria check already answered for it.

The Quality Gate of a Loregarden TDD V3 ticket asked a person to approve after
the gatekeeper passed. Since 2026-10-01 a person approved 451 of them, rejected
none, and took 0.2 h on average: the sign-off never changed an outcome. What a
person would have read there is now recorded before the gate runs, criterion by
criterion (`criterion_gate`), so the gate is signed on that record instead.

Narrow, like the other signers in `autopilot_sign_off.signer_for_gate`:

- only the legacy "approve this stage" sign-off, never an authored question,
  an authority grant or a recheck (the caller checks that first);
- only the stage the criteria were checked before;
- only when the newest `criteria_checked` decision names the commit the
  ticket is at now, so no code changed after the check;
- never when the ticket or anything under it carries a decision the pipeline
  took in a person's place (criteria split off, findings deferred). A person
  reviews those, and this gate is where they would;
- never in a workspace whose profile turns it off
  (`gates.sign_off_when_criteria_checked`).
"""

from __future__ import annotations

import json

from loregarden.models.domain import (
    Approval,
    DomainEvent,
    EventType,
    OrchestratorDecision,
    Ticket,
)
from loregarden.services.criterion_gate_stages import gate_of
from loregarden.services.evidence import resolve_head_sha
from loregarden.services.hierarchy_service import descendants_by_root
from loregarden.services.orchestration_profile import resolve_orchestration_profile
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from loregarden.services.ticket_workspace_binding import workspace_for_ticket
from loregarden.services.workflow_service import resolve_ticket_stages
from pydantic import BaseModel
from sqlmodel import Session, col, select

#: `Approval.resolved_by` for a gate signed on its criteria check.
RESOLVED_BY_CRITERIA = "criteria_check"

#: Decisions taken in a person's place that keep a gate for a person to sign.
HOLDS_THE_GATE = frozenset(
    {
        OrchestratorDecision.SPLIT_CRITERIA.value,
        OrchestratorDecision.DEFERRED_UNDECIDED_FINDINGS.value,
    }
)


class CriteriaBasis(BaseModel):
    """The criteria check a gate is signed on."""

    event_id: str
    commit_sha: str
    rerun: int
    not_rerun: int


def _decisions(session: Session, ticket_ids: list[str]) -> list[tuple[DomainEvent, dict]]:
    events = session.exec(
        select(DomainEvent)
        .where(DomainEvent.type == EventType.ORCHESTRATOR_DECISION)
        .where(col(DomainEvent.ticket_id).in_(ticket_ids))
        .order_by(col(DomainEvent.created_at).desc())
    ).all()
    return [(event, json.loads(event.payload_json or "{}")) for event in events]


def decided_in_a_persons_place(session: Session, ticket: Ticket) -> list[str]:
    """The decisions under ``ticket`` that keep its gate a person's, one line each, newest first."""
    tree = {
        ticket.id: ticket,
        **{t.id: t for t in descendants_by_root(session, [ticket.id])[ticket.id]},
    }
    return [
        f"{tree[event.ticket_id or ''].external_id}: {payload.get('reason') or payload['decision']}"
        for event, payload in _decisions(session, list(tree))
        if payload.get("decision") in HOLDS_THE_GATE
    ]


def held_gate_note(session: Session, ticket: Ticket) -> str:
    """What a person signing this ticket's gate should review first, or "" when nothing."""
    held = decided_in_a_persons_place(session, ticket)
    if not held:
        return ""
    return "Decided without you under this ticket, to review before signing:\n" + "\n".join(
        f"- {line}" for line in held
    )


def _latest_check(session: Session, ticket: Ticket, stage_key: str) -> CriteriaBasis | None:
    for event, payload in _decisions(session, [ticket.id]):
        if payload.get("decision") != OrchestratorDecision.CRITERIA_CHECKED.value:
            continue
        if payload.get("stage_key") != stage_key:
            continue
        return CriteriaBasis(
            event_id=event.id,
            commit_sha=payload.get("commit_sha") or "",
            rerun=len(payload.get("rerun") or []),
            not_rerun=len(payload.get("not_rerun") or []),
        )
    return None


def _turned_off(session: Session, ticket: Ticket) -> bool:
    workspace = workspace_for_ticket(session, ticket)
    if workspace is None:
        return True
    return not resolve_orchestration_profile(workspace).gates.sign_off_when_criteria_checked


def criteria_basis(session: Session, ticket: Ticket, approval: Approval) -> CriteriaBasis | None:
    """The criteria check this gate may be signed on, or None when a person signs it."""
    _, stages = resolve_ticket_stages(session, ticket)
    gate = gate_of(stages) if stages else None
    if gate is None or gate.key != approval.stage_key:
        return None
    if _turned_off(session, ticket):
        return None
    basis = _latest_check(session, ticket, gate.key)
    if basis is None or not basis.commit_sha:
        return None
    if basis.commit_sha != resolve_head_sha(session, ticket):
        return None
    if decided_in_a_persons_place(session, ticket):
        return None
    return basis


def record_criteria_sign_off(
    session: Session, ticket: Ticket, approval: Approval, basis: CriteriaBasis
) -> None:
    """Say on the ticket's history that its criteria check, not a person, signed the gate."""
    approval.resolved_by = RESOLVED_BY_CRITERIA
    session.add(approval)
    unrerun = f", {basis.not_rerun} only checked to exist" if basis.not_rerun else ""
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.SIGNED_OFF_ON_CRITERIA,
        stage_key=approval.stage_key,
        reason=(
            f"Signed the '{approval.stage_key}' gate: every acceptance criterion was answered "
            f"for at {basis.commit_sha[:9]} ({basis.rerun} tests rerun{unrerun}), and nothing "
            "under this ticket was decided in a person's place."
        ),
        evidence={
            "approval_id": approval.id,
            "criteria_checked_event_id": basis.event_id,
            "commit_sha": basis.commit_sha,
        },
    )
    session.commit()
