"""`artifacts.error` on the ticket payload names only an error nothing has run since.

Every reader of it — the Timeline's block card, the "Run failed" banner, the
details modal, the Hive panel — treats it as the ticket's current state. Error
artifacts are never cleared, so before this the latest one stayed on screen,
labelled Blocked, after later stages ran green (lg-durable-remote-336).
"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from loregarden.models.domain import ArtifactKind, RunStatus
from loregarden.models.domain.enums import utcnow
from loregarden.testing.factories import make_agent_run, make_artifact, make_ticket, make_workspace
from sqlmodel import Session

BLOCK_MESSAGE = "Stage blocked — the server half is yours"


@pytest.fixture
def blocked_then_moved_on():
    """An implement block, then a later run: the shape of lg-durable-remote-336."""
    from loregarden.db.session import engine

    with Session(engine) as session:
        workspace = make_workspace(session, slug="current-error-ws")
        ticket = make_ticket(session, workspace_id=workspace.id, external_id="current-error-moved")
        now = utcnow()
        make_artifact(
            session,
            ticket_id=ticket.id,
            kind=ArtifactKind.ERROR,
            title="Stage blocked — implement",
            content={"message": BLOCK_MESSAGE, "run_code": "RUN-1", "stage_key": "implement"},
            created_at=now - timedelta(minutes=30),
        )
        later = make_agent_run(
            session,
            workspace_id=workspace.id,
            ticket_id=ticket.id,
            run_code="RUN-2",
            stage_key="test-break",
            status=RunStatus.SUCCEEDED,
        )
        later.created_at = now - timedelta(minutes=10)
        session.add(later)
        session.commit()
        return ticket.id


@pytest.fixture
def blocked_and_still_there():
    """The same block with nothing run since: it is the ticket's state now."""
    from loregarden.db.session import engine

    with Session(engine) as session:
        workspace = make_workspace(session, slug="current-error-ws")
        ticket = make_ticket(session, workspace_id=workspace.id, external_id="current-error-live")
        earlier = make_agent_run(
            session,
            workspace_id=workspace.id,
            ticket_id=ticket.id,
            run_code="RUN-9",
            stage_key="implement",
            status=RunStatus.FAILED,
        )
        earlier.created_at = utcnow() - timedelta(minutes=30)
        session.add(earlier)
        session.commit()
        make_artifact(
            session,
            ticket_id=ticket.id,
            kind=ArtifactKind.ERROR,
            title="Stage blocked — implement",
            content={"message": BLOCK_MESSAGE, "run_code": "RUN-9", "stage_key": "implement"},
            created_at=utcnow() - timedelta(minutes=5),
        )
        return ticket.id


def test_an_error_a_later_run_moved_past_is_not_current(
    client: TestClient, blocked_then_moved_on: str
):
    detail = client.get(f"/api/tickets/{blocked_then_moved_on}").json()

    assert detail["artifacts"]["error"] is None


def test_an_error_nothing_has_run_since_is_current(
    client: TestClient, blocked_and_still_there: str
):
    detail = client.get(f"/api/tickets/{blocked_and_still_there}").json()

    assert detail["artifacts"]["error"]["message"] == BLOCK_MESSAGE
    assert detail["artifacts"]["error"]["run_code"] == "RUN-9"
