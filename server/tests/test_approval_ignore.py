"""Ignoring an approval closes it with a reason and never strands what waits on it."""

import json
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from loregarden.agents.executors.permission_bridge import poll_approval_resolution
from loregarden.models.domain import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    RunStatus,
    TicketState,
)
from loregarden.services.orchestration import ApprovalService
from loregarden.testing.factories import make_agent_run, make_ticket, make_workspace
from sqlmodel import Session


@pytest.fixture
def session():
    from loregarden.db.session import engine

    with Session(engine) as session:
        yield session


@pytest.fixture
def workspace(session):
    return make_workspace(session, slug="ignore-ws")


def _approval(session, workspace, *, kind, ticket_state=TicketState.IN_PROGRESS, run_id=None):
    ticket = make_ticket(
        session, workspace_id=workspace.id, external_id=f"ign-{kind.value}", state=ticket_state
    )
    approval = Approval(
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        run_id=run_id,
        kind=kind,
        title=f"{kind.value} card",
        stage_key="review",
    )
    session.add(approval)
    session.commit()
    session.refresh(approval)
    return approval


def _view(client, approval_id):
    [view] = [a for a in client.get("/api/inbox/approvals").json() if a["id"] == approval_id]
    return view


def test_a_card_nothing_waits_on_closes_with_its_reason(client: TestClient, session, workspace):
    card = _approval(session, workspace, kind=ApprovalKind.HUMAN_ACTION)
    assert _view(client, card.id)["ignore_effect"] == "none"

    response = client.post(
        f"/api/inbox/approvals/{card.id}", json={"action": "ignore", "response": " Duplicate "}
    )

    assert response.json() == {"id": card.id, "status": "ignored"}
    session.refresh(card)
    assert card.status is ApprovalStatus.IGNORED
    assert card.resolved_at is not None
    assert json.loads(card.response_json) == {"ignored_reason": "Duplicate"}
    assert card.id not in {a["id"] for a in client.get("/api/inbox/approvals").json()}
    history = client.get(f"/api/inbox/approvals/history?ticket_id={card.ticket_id}").json()
    assert [(h["id"], h["status"]) for h in history] == [(card.id, "ignored")]


@pytest.mark.parametrize("reason", ["", "   ", "x" * 501])
def test_a_reason_is_required_and_bounded(client: TestClient, session, workspace, reason):
    card = _approval(session, workspace, kind=ApprovalKind.HUMAN_ACTION)

    response = client.post(
        f"/api/inbox/approvals/{card.id}", json={"action": "ignore", "response": reason}
    )

    assert response.status_code == 400
    session.refresh(card)
    assert card.status is ApprovalStatus.PENDING


def test_an_already_resolved_card_cannot_be_ignored(client: TestClient, session, workspace):
    card = _approval(session, workspace, kind=ApprovalKind.HUMAN_ACTION)
    card.status = ApprovalStatus.APPROVED
    session.add(card)
    session.commit()

    response = client.post(
        f"/api/inbox/approvals/{card.id}", json={"action": "ignore", "response": "stale"}
    )

    assert response.status_code == 400


def test_a_live_agent_on_a_permission_prompt_is_denied_not_left_polling(
    client: TestClient, session, workspace
):
    run = make_agent_run(
        session, workspace_id=workspace.id, run_code="RUN-IGN", status=RunStatus.AWAITING_PERMISSION
    )
    card = _approval(session, workspace, kind=ApprovalKind.CLI_PERMISSION, run_id=run.id)
    assert _view(client, card.id)["ignore_effect"] == "denies_agent"

    client.post(
        f"/api/inbox/approvals/{card.id}", json={"action": "ignore", "response": "Test approval"}
    )

    resolution = poll_approval_resolution(card.id)
    assert resolution is not None, "an ignored prompt must answer the poller"
    assert resolution.approved is False
    assert "Test approval" in resolution.message


def test_a_permission_whose_run_ended_has_nothing_waiting(client: TestClient, session, workspace):
    run = make_agent_run(
        session, workspace_id=workspace.id, run_code="RUN-DONE", status=RunStatus.SUCCEEDED
    )
    card = _approval(session, workspace, kind=ApprovalKind.CLI_PERMISSION, run_id=run.id)

    assert _view(client, card.id)["ignore_effect"] == "none"


def test_a_parked_stage_is_answered_as_reject_answers_it(client: TestClient, session, workspace):
    card = _approval(session, workspace, kind=ApprovalKind.WORKFLOW_GATE)
    assert _view(client, card.id)["ignore_effect"] == "declines_stage"

    with mock.patch.object(ApprovalService, "_apply_ticket_resolution") as apply:
        client.post(
            f"/api/inbox/approvals/{card.id}", json={"action": "ignore", "response": "Superseded"}
        )

    apply.assert_called_once()
    assert apply.call_args.kwargs["approved"] is False
    assert "Superseded" in apply.call_args.kwargs["response_text"]


def test_a_gate_on_a_finished_ticket_moves_nothing(client: TestClient, session, workspace):
    card = _approval(
        session, workspace, kind=ApprovalKind.WORKFLOW_GATE, ticket_state=TicketState.DONE
    )
    view = _view(client, card.id)
    assert (view["ignore_effect"], view["ticket_state"]) == ("none", "done")

    with mock.patch.object(ApprovalService, "_apply_ticket_resolution") as apply:
        client.post(
            f"/api/inbox/approvals/{card.id}",
            json={"action": "ignore", "response": "Ticket completed"},
        )

    apply.assert_not_called()
    session.refresh(card)
    assert card.status is ApprovalStatus.IGNORED
