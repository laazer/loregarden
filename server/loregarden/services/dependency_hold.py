"""A ticket whose prerequisites are unmet is held at every dispatch, not only at queue entry.

`dependency_readiness` judged readiness in two places: the initiative
autopilot before it queued a ticket, and the builtin driver before it chose a
child. Nothing checked again before a stage started, so an edge added after
the ticket was queued changed nothing. lg-milestone-that-1315 was filed with
its prerequisite only in prose; a planner linked the edge mid-run, and triage
was dispatched three more times past it, then given a repair turn for a wait
no agent could shorten.

Now `OrchestrationService.start_run`, the builtin driver's loop, and the two
dispatch paths that never reach `start_run` (MCP `start_stage`, an external
harness's `begin_external_stage`) all ask `refuse_held_dispatch` first. A held
dispatch records `HELD_ON_PREREQUISITES` on the ticket's history and raises
`DependencyHold`. Nothing is blocked: the ticket waits on another ticket, which
is not a failure of its own, and `release_held_tickets` resumes it when the
prerequisites land.

A person can still start it. "A named start is never held" (676) existed so a
stale edge could not wedge a ticket with no way out; the way out is now
explicit: `override_prerequisites` records `PREREQUISITES_OVERRIDDEN`, and the
prerequisites unmet at that moment stop holding the ticket. An edge added
after the override holds it again, since nobody has decided about that one.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import datetime

from loregarden.models.domain import (
    AgentRun,
    DomainEvent,
    EventType,
    OrchestratorDecision,
    Ticket,
    TicketDependency,
)
from loregarden.services.dependency_readiness import (
    UnmetPrerequisite,
    UnmetReason,
    unmet_prerequisites_for_start,
)
from loregarden.services.orchestrator_decisions import record_orchestrator_decision
from loregarden.services.ticket_dependencies import (
    TicketDependencyService,
    resolve_prerequisites,
)
from loregarden.services.ticket_workspace_binding import workspace_for_ticket
from sqlalchemy import func
from sqlmodel import Session, col, select

#: Prerequisites named in a hold's sentence before the rest become a count.
_NAMED = 3


class DependencyHold(ValueError):
    """A dispatch refused because the ticket waits on unmet prerequisites.

    A `ValueError` so every dispatch path that already turns a refusal into a
    409 or a queue entry's failure reason does so here too.
    """

    def __init__(self, ticket: Ticket, unmet: Sequence[UnmetPrerequisite]) -> None:
        super().__init__(hold_reason(ticket, unmet))
        self.ticket = ticket
        self.unmet = list(unmet)


def describe_unmet(entry: UnmetPrerequisite) -> str:
    """ "lg-x-12 (in progress)" or "lg-x-12 (done, not landed on integration/lg-x-3)"."""
    if entry.reason is UnmetReason.NOT_LANDED:
        where = f" on {entry.target}" if entry.target else ""
        return f"{entry.ticket.external_id} (done, not landed{where})"
    return f"{entry.ticket.external_id} ({entry.ticket.state.value.replace('_', ' ')})"


def hold_reason(ticket: Ticket, unmet: Sequence[UnmetPrerequisite]) -> str:
    """One sentence a person reads on the history rail."""
    named = ", ".join(describe_unmet(entry) for entry in unmet[:_NAMED])
    more = f" and {len(unmet) - _NAMED} more" if len(unmet) > _NAMED else ""
    return (
        f"Held {ticket.external_id}: it waits on {named}{more}. It starts when "
        "they are done and landed."
    )


def _evidence(unmet: Iterable[UnmetPrerequisite]) -> dict[str, object]:
    return {
        "prerequisites": [
            {
                "ticket_id": entry.ticket.id,
                "external_id": entry.ticket.external_id,
                "reason": entry.reason.value,
                "target": entry.target,
            }
            for entry in unmet
        ]
    }


def _decision_events(
    session: Session, ticket_id: str, decision: OrchestratorDecision
) -> list[DomainEvent]:
    return list(
        session.exec(
            select(DomainEvent)
            .where(
                DomainEvent.ticket_id == ticket_id,
                DomainEvent.type == EventType.ORCHESTRATOR_DECISION,
                func.json_extract(col(DomainEvent.payload_json), "$.decision") == decision.value,
            )
            .order_by(col(DomainEvent.created_at).desc())
        ).all()
    )


def _overridden_ids(session: Session, ticket_id: str) -> set[str]:
    """Prerequisites a person has started this ticket past, every override counted."""
    overridden: set[str] = set()
    for event in _decision_events(
        session, ticket_id, OrchestratorDecision.PREREQUISITES_OVERRIDDEN
    ):
        payload = json.loads(event.payload_json or "{}")
        overridden.update(item.get("ticket_id", "") for item in payload.get("prerequisites", []))
    return overridden - {""}


def _edge_times(session: Session, ticket_id: str) -> dict[str, datetime]:
    rows = session.exec(
        select(TicketDependency.depends_on_ticket_id, TicketDependency.created_at).where(
            TicketDependency.ticket_id == ticket_id
        )
    ).all()
    return dict(rows)


def unmet_prerequisites(session: Session, ticket: Ticket) -> list[UnmetPrerequisite]:
    """What holds ``ticket``, before any override: landed-readiness where there is
    a repository to ask, state alone where there is not (an initiative)."""
    workspace = workspace_for_ticket(session, ticket)
    if workspace is not None:
        return unmet_prerequisites_for_start(session, ticket, workspace)
    return [
        UnmetPrerequisite(prereq, UnmetReason.NOT_DONE)
        for prereq in TicketDependencyService(session).unmet_prerequisites(ticket.id)
    ]


def holding_prerequisites(session: Session, ticket: Ticket) -> list[UnmetPrerequisite]:
    """The unmet prerequisites that still hold ``ticket``: those a person has not
    started it past. An override covers the edges that existed when it was
    made; an edge re-added since holds again."""
    unmet = unmet_prerequisites(session, ticket)
    if not unmet:
        return []
    overrides = _decision_events(session, ticket.id, OrchestratorDecision.PREREQUISITES_OVERRIDDEN)
    if not overrides:
        return unmet
    overridden = _overridden_ids(session, ticket.id)
    latest_override = overrides[0].created_at
    edges = _edge_times(session, ticket.id)
    return [
        entry
        for entry in unmet
        if entry.ticket.id not in overridden
        or edges.get(entry.ticket.id, latest_override) > latest_override
    ]


def record_hold(
    session: Session, ticket: Ticket, unmet: Sequence[UnmetPrerequisite], *, stage_key: str
) -> None:
    """Put a hold on the ticket's history, committed."""
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.HELD_ON_PREREQUISITES,
        stage_key=stage_key,
        reason=hold_reason(ticket, unmet),
        evidence=_evidence(unmet),
    )
    session.commit()


def link_reported_prerequisites(session: Session, ticket: Ticket, refs: Sequence[str]) -> list[str]:
    """Link each ticket a stage report says this one waits on. Returns what
    could not be linked, one line each; empty when every ref was linked."""
    problems: list[str] = []
    dependencies = TicketDependencyService(session)
    for ref in refs:
        try:
            (prerequisite,) = resolve_prerequisites(session, [ref])
            dependencies.add_dependency(ticket.id, prerequisite.id, created_by="agent")
        except ValueError as exc:
            problems.append(f"{ref}: {exc}")
    return problems


def refuse_held_dispatch(session: Session, ticket: Ticket, *, stage_key: str) -> None:
    """Raise `DependencyHold` when ``ticket`` may not start ``stage_key`` yet.

    The hold is recorded before the raise and committed, so the rollback a
    caller does on the way out cannot take the record with it.
    """
    unmet = holding_prerequisites(session, ticket)
    if not unmet:
        return
    record_hold(session, ticket, unmet, stage_key=stage_key)
    raise DependencyHold(ticket, unmet)


def override_prerequisites(
    session: Session, ticket: Ticket, *, stage_key: str, actor: str
) -> list[UnmetPrerequisite]:
    """Record that ``actor`` starts ``ticket`` past its unmet prerequisites.

    Returns what was overridden; nothing is recorded when nothing holds it.
    """
    unmet = holding_prerequisites(session, ticket)
    if not unmet:
        return []
    named = ", ".join(describe_unmet(entry) for entry in unmet[:_NAMED])
    more = f" and {len(unmet) - _NAMED} more" if len(unmet) > _NAMED else ""
    record_orchestrator_decision(
        session,
        ticket,
        decision=OrchestratorDecision.PREREQUISITES_OVERRIDDEN,
        stage_key=stage_key,
        reason=f"Started {ticket.external_id} anyway ({actor}), past {named}{more}.",
        evidence={**_evidence(unmet), "actor": actor},
    )
    session.commit()
    return unmet


def admit_named_start(
    session: Session,
    ticket: Ticket,
    *,
    stage_key: str,
    ignore_prerequisites: bool,
    actor: str,
) -> None:
    """A start someone asked for by name: held like any other, unless they said
    to start it anyway, which is recorded."""
    if ignore_prerequisites:
        override_prerequisites(session, ticket, stage_key=stage_key, actor=actor)
        return
    refuse_held_dispatch(session, ticket, stage_key=stage_key)


def latest_hold_at(session: Session, ticket_id: str) -> datetime | None:
    """When ``ticket_id`` was last held, if it ever was."""
    events = _decision_events(session, ticket_id, OrchestratorDecision.HELD_ON_PREREQUISITES)
    return events[0].created_at if events else None


def ran_since(session: Session, ticket_id: str, moment: datetime) -> bool:
    """Whether any agent run started on ``ticket_id`` after ``moment``."""
    return (
        session.exec(
            select(AgentRun.id).where(AgentRun.ticket_id == ticket_id, AgentRun.created_at > moment)
        ).first()
        is not None
    )
