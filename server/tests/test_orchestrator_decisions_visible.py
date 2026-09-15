"""The orchestrator's decisions reach the ticket history, where a person can see them.

`lg-workflow-integrity-734`. Asked directly after blob-procedural-sdf-31: "do we
have a way to view orchestrator logs in the UI?" For agents, yes. For the
orchestrator — the component making the most consequential decisions — no.
Refusing a dispatch, settling a run it did not start, overruling a workspace
gate: each was a `logger.warning` to server stdout, which nothing in the UI reads.

Every assertion here goes through `event_bus.ticket_history`, not `publish`.
That endpoint filters to `TRANSITION_EVENTS`, so an event that is published but
not in that tuple is written and never shown — which is precisely the failure
684 found for GATE_EVALUATED, and precisely what a `publish`-was-called mock
would miss.
"""

from __future__ import annotations

import pytest
from loregarden.core.event_bus import event_bus
from loregarden.models.domain import (
    EventType,
    OrchestrationRun,
    OrchestrationRunStatus,
    OrchestratorDecision,
    RunStatus,
    StageStatus,
    Ticket,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.run_service import settle_orphaned_agent_runs, settle_stranded_stages
from loregarden.services.seed import seed_database
from sqlmodel import Session, select
from tests.factories import make_agent_run, make_workspace_ticket


def _decisions(session: Session, ticket_id: str) -> list:
    return [
        e
        for e in event_bus.ticket_history(session, ticket_id, limit=100)
        if e.type == EventType.ORCHESTRATOR_DECISION
    ]


def _payload(event) -> dict:
    import json

    return json.loads(event.payload_json or "{}")


def test_a_refused_dispatch_reaches_the_history(db_session: Session):
    """688's guard. It raises before any run row exists, so the event has no
    run_id — it attaches to the ticket, which is where a person looks."""
    seed_database(db_session)
    ticket = make_workspace_ticket(db_session, "decision-688")
    parent = OrchestrationRun(
        run_code="orch_dead",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        status=OrchestrationRunStatus.FAILED,
    )
    db_session.add(parent)
    db_session.commit()
    db_session.refresh(parent)

    with pytest.raises(ValueError, match="already|is failed"):
        OrchestrationService(db_session).start_run(
            ticket, stage_key="implement", orchestration_run_id=parent.id
        )

    found = _decisions(db_session, ticket.id)
    assert len(found) == 1
    payload = _payload(found[0])
    assert payload["decision"] == OrchestratorDecision.REFUSED_DISPATCH_TERMINAL_PARENT.value
    assert payload["stage_key"] == "implement"
    assert "orch_dead" in payload["reason"]


def test_a_settled_orphan_reaches_the_history_and_says_it_refunded(db_session: Session):
    """697's sweeper. The decision names the run, the parent, and whether the
    attempt came back — the three facts a person would otherwise dig for."""
    seed_database(db_session)
    ticket = make_workspace_ticket(db_session, "decision-697")
    parent = OrchestrationRun(
        run_code="orch_blocked",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        status=OrchestrationRunStatus.BLOCKED,
    )
    db_session.add(parent)
    db_session.commit()
    db_session.refresh(parent)
    run = make_agent_run(
        db_session,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        run_code="run_orphan",
        stage_key="implement",
        status=RunStatus.RUNNING,
        orchestration_run_id=parent.id,
    )

    settle_orphaned_agent_runs(db_session)

    found = _decisions(db_session, ticket.id)
    assert len(found) == 1
    payload = _payload(found[0])
    assert payload["decision"] == OrchestratorDecision.SETTLED_ORPHANED_RUN.value
    assert payload["run_code"] == "run_orphan"
    assert payload["parent_status"] == "blocked"
    assert found[0].run_id == run.id


def test_a_stranded_stage_settle_reaches_the_history(db_session: Session):
    """settle_stranded_stages: a stage RUNNING with nothing behind it."""
    seed_database(db_session)
    ticket = db_session.exec(
        select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
    ).first()
    OrchestrationService(db_session).ensure_workflow_instance(ticket, commit=True)
    ticket.workflow_stage_key = "plan"
    ticket.workflow_stage_status = StageStatus.RUNNING
    db_session.add(ticket)
    db_session.commit()

    settle_stranded_stages(db_session, ticket_id=ticket.id)

    found = _decisions(db_session, ticket.id)
    assert len(found) == 1
    payload = _payload(found[0])
    assert payload["decision"] == OrchestratorDecision.SETTLED_STRANDED_STAGE.value
    assert payload["stage_key"] == "plan"


def test_the_decision_kind_is_closed(db_session: Session):
    """AC2. A kind cannot be added without the readers knowing — the
    client's tone() and the enum must agree on the members."""
    kinds = {d.value for d in OrchestratorDecision}
    assert kinds == {
        "refused_dispatch_terminal_parent",
        "settled_orphaned_run",
        "settled_stranded_stage",
        "overruled_stale_gate",
        "approved_design_plan",
    }, "a decision kind changed — update client/src/utils/ticketHistory.ts to match"


def test_the_event_is_in_the_history_filter():
    """AC5's premise, pinned directly: published-but-filtered is the exact
    failure 684 found. Everything else here goes through ticket_history, but
    this names the mechanism so the failure message says why."""
    from loregarden.core.event_bus import TRANSITION_EVENTS

    assert EventType.ORCHESTRATOR_DECISION in TRANSITION_EVENTS
