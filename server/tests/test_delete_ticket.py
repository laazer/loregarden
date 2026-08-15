from fastapi.testclient import TestClient
from loregarden.models.domain import (
    AgentRun,
    Artifact,
    BtwExchange,
    RunMessage,
    StageFanoutAttempt,
    StageFanoutGroup,
    Ticket,
    TicketDependency,
    TicketRelation,
    TicketStudioSession,
)
from sqlalchemy import text
from sqlmodel import Session


def _create(client: TestClient, **overrides) -> dict:
    body = {
        "workspace_slug": "loregarden",
        "title": "Delete me",
        "work_item_type": "feature",
        **overrides,
    }
    res = client.post("/api/tickets", json=body)
    assert res.status_code == 201, res.text
    return res.json()


def test_delete_ticket_removes_it(client: TestClient):
    milestone_id = next(
        t["id"]
        for t in client.get("/api/tickets?workspace=loregarden").json()
        if t["work_item_type"] == "milestone"
    )
    feature = _create(client, parent_ticket_id=milestone_id)

    res = client.delete(f"/api/tickets/{feature['id']}")
    assert res.status_code == 200
    assert res.json() == {"ok": True}

    res = client.get(f"/api/tickets/{feature['id']}")
    assert res.status_code == 404


def test_delete_ticket_blocks_when_children_exist(client: TestClient):
    milestone_id = next(
        t["id"]
        for t in client.get("/api/tickets?workspace=loregarden").json()
        if t["work_item_type"] == "milestone"
    )
    feature = _create(client, parent_ticket_id=milestone_id)
    _create(
        client,
        title="Child capability",
        work_item_type="capability",
        parent_ticket_id=feature["id"],
    )

    res = client.delete(f"/api/tickets/{feature['id']}")
    assert res.status_code == 400
    assert "child" in res.json()["detail"].lower()

    res = client.get(f"/api/tickets/{feature['id']}")
    assert res.status_code == 200


def test_delete_ticket_missing_returns_404(client: TestClient):
    res = client.delete("/api/tickets/does-not-exist")
    assert res.status_code == 404


def test_delete_ticket_leaves_no_row_pointing_at_it(client: TestClient, isolated_db):
    """Every table referencing a ticket has to be swept, in an order SQLite would
    accept with foreign keys enforced.

    The sweep is by hand — SQLite does not enforce these references at runtime,
    so a table left out of it goes unnoticed until something reads a row whose
    ticket is gone. Two ways to miss one: never listing the table, and listing it
    but matching only on `ticket_id` when the edge points inbound
    (`depends_on_ticket_id`, `related_ticket_id`).
    """
    milestone_id = next(
        t["id"]
        for t in client.get("/api/tickets?workspace=loregarden").json()
        if t["work_item_type"] == "milestone"
    )
    doomed = _create(
        client, title="Ticket with a full spread of children", parent_ticket_id=milestone_id
    )
    neighbour = _create(client, title="Ticket that outlives it", parent_ticket_id=milestone_id)

    with Session(isolated_db) as session:
        ticket = session.get(Ticket, doomed["id"])
        run = AgentRun(
            run_code="RUN-1",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="backend_implementer",
        )
        session.add(run)
        session.commit()

        group = StageFanoutGroup(
            workspace_id=ticket.workspace_id, ticket_id=ticket.id, stage_key="implement"
        )
        session.add(group)
        session.commit()

        session.add_all(
            [
                Artifact(ticket_id=ticket.id, run_id=run.id, kind="log", title="Run log"),
                RunMessage(run_id=run.id, ticket_id=ticket.id, content="steer"),
                BtwExchange(ticket_id=ticket.id, observed_run_id=run.id, question="why?"),
                StageFanoutAttempt(group_id=group.id, attempt_index=0, agent_run_id=run.id),
                # Both directions: the ticket depends on its neighbour, and the
                # neighbour depends on the ticket.
                TicketDependency(ticket_id=ticket.id, depends_on_ticket_id=neighbour["id"]),
                TicketDependency(ticket_id=neighbour["id"], depends_on_ticket_id=ticket.id),
                TicketRelation(ticket_id=ticket.id, related_ticket_id=neighbour["id"]),
                TicketRelation(ticket_id=neighbour["id"], related_ticket_id=ticket.id),
                TicketStudioSession(workspace_id=ticket.workspace_id, parent_ticket_id=ticket.id),
            ]
        )
        session.commit()

    assert client.delete(f"/api/tickets/{doomed['id']}").status_code == 200

    with Session(isolated_db) as session:
        dangling = session.exec(text("PRAGMA foreign_key_check")).all()
        assert dangling == []
        # The neighbour is untouched — the sweep removes edges to the deleted
        # ticket, not the tickets on the other end of them.
        assert session.get(Ticket, neighbour["id"]) is not None
