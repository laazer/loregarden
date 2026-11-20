"""GET /api/tickets/{id}/sibling_claims: the ticket page's shared-files card."""

from __future__ import annotations

import json

import pytest
from loregarden.agents.plan_context import ARTIFACT_KIND as PLAN_KIND
from loregarden.models.domain import AgentRun, Ticket, TicketState, WorkItemType
from loregarden.models.domain.enums import utcnow
from loregarden.services.ticket_dependencies import TicketDependencyService
from loregarden.testing.factories import make_artifact, make_ticket
from sqlmodel import Session, select

LAND = "server/loregarden/services/land_ticket.py"


@pytest.fixture(name="tree")
def tree_fixture(client, isolated_db):
    """The seeded ticket planning LAND and a.py, and three open siblings under one milestone."""
    with Session(isolated_db) as session:
        ticket = session.exec(select(Ticket)).first()
        milestone = make_ticket(
            session,
            workspace_id=ticket.workspace_id,
            external_id="ms-claims-view",
            work_item_type=WorkItemType.MILESTONE,
        )
        ticket.parent_ticket_id = milestone.id
        session.add(ticket)
        session.commit()
        siblings = {
            name: make_ticket(
                session,
                workspace_id=ticket.workspace_id,
                external_id=f"sib-{name}",
                title=f"Sibling {name}",
                parent_ticket_id=milestone.id,
                state=TicketState.IN_PROGRESS,
            )
            for name in ("a", "b", "c")
        }
        ids = {name: t.id for name, t in siblings.items()} | {"ticket": ticket.id}
        external = ticket.external_id
    return client, isolated_db, ids, external


def _plan(session: Session, ticket_id: str, files: list[str]) -> None:
    make_artifact(
        session,
        ticket_id=ticket_id,
        kind=PLAN_KIND,
        title="Plan",
        content={"approach": "x", "modules_touched": files},
    )


def _changed(session: Session, ticket_id: str, *paths: str) -> None:
    ticket = session.get(Ticket, ticket_id)
    session.add(
        AgentRun(
            run_code=f"changed-{ticket.external_id}",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="backend_implementer",
            stage_key="implement",
            changed_paths_json=json.dumps(list(paths)),
            changed_paths_recorded_at=utcnow(),
        )
    )
    session.commit()


def test_a_ticket_claiming_nothing_says_so(tree):
    client, _db, ids, _ = tree

    reply = client.get(f"/api/tickets/{ids['ticket']}/sibling_claims")

    assert reply.status_code == 200
    assert reply.json() == {"claimed_paths": 0, "siblings": []}


def test_each_sibling_lists_the_shared_files_strongest_claim_first(tree):
    client, db, ids, external = tree
    with Session(db) as session:
        _plan(session, ids["ticket"], [LAND, "a.py", "server/only_mine.py"])
        _plan(session, ids["a"], ["a.py", LAND, "server/unrelated.py"])
        _changed(session, ids["a"], LAND)

    # The shareable id resolves too, as on the ticket route.
    reply = client.get(f"/api/tickets/{external}/sibling_claims").json()

    assert reply["claimed_paths"] == 3
    assert reply["siblings"] == [
        {
            "ticket_id": ids["a"],
            "external_id": "sib-a",
            "title": "Sibling a",
            "state": "in_progress",
            "ordered": False,
            "paths": [
                {"path": LAND, "source": "observed"},
                {"path": "a.py", "source": "planned"},
            ],
        }
    ]


def test_siblings_not_yet_ordered_lead(tree):
    client, db, ids, _ = tree
    with Session(db) as session:
        _plan(session, ids["ticket"], [LAND])
        for name in ("a", "b", "c"):
            _plan(session, ids[name], [LAND])
        TicketDependencyService(session).add_dependency(ids["ticket"], ids["a"])

    reply = client.get(f"/api/tickets/{ids['ticket']}/sibling_claims").json()

    assert [(s["external_id"], s["ordered"]) for s in reply["siblings"]] == [
        ("sib-b", False),
        ("sib-c", False),
        ("sib-a", True),
    ]


def test_an_unknown_ticket_is_not_found(client):
    assert client.get("/api/tickets/no-such-ticket-9/sibling_claims").status_code == 404
