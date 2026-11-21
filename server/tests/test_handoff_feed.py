"""GET /api/handoffs: the Baxter dock's Handoffs tab."""

from __future__ import annotations

from datetime import timedelta

import pytest
from loregarden.models.domain import ArtifactKind, Ticket, Workspace
from loregarden.models.domain.enums import utcnow
from loregarden.testing.factories import make_artifact, make_ticket, make_workspace
from sqlmodel import Session, select


def _doc(from_agent: str, to_agent: str, *, met: int = 1, status: str = "complete") -> dict:
    return {
        "handoff": {
            "schema_version": "1.0",
            "from_agent": from_agent,
            "to_agent": to_agent,
            "required_items_met": met,
            "total_required_items": 1,
            "checklist": [
                {
                    "item_key": "tests_written",
                    "item": "Tests written",
                    "required": True,
                    "status": status,
                    "evidence": f"{from_agent} wrote them",
                    "certainty": "verified",
                }
            ],
        }
    }


@pytest.fixture(name="feed")
def feed_fixture(client, isolated_db):
    """The seeded ticket with two handoffs, a sibling with one, and another workspace's ticket with one."""
    now = utcnow()
    with Session(isolated_db) as session:
        ticket = session.exec(select(Ticket)).first()
        sibling = make_ticket(
            session,
            workspace_id=ticket.workspace_id,
            external_id="feed-sibling",
            title="Retry budgets",
        )
        elsewhere = make_ticket(
            session,
            workspace_id=make_workspace(session, slug="elsewhere").id,
            external_id="feed-elsewhere",
        )
        for ticket_id, pair, minutes in (
            (ticket.id, ("spec", "test_designer"), 30),
            (ticket.id, ("test_designer", "test_breaker"), 20),
            (sibling.id, ("planner", "spec"), 10),
            (elsewhere.id, ("spec", "verifier"), 5),
        ):
            make_artifact(
                session,
                ticket_id=ticket_id,
                kind=ArtifactKind.HANDOFF,
                title=f"handoff {pair[0]} → {pair[1]}",
                content=_doc(*pair),
                created_at=now - timedelta(minutes=minutes),
            )
        # Not a handoff: never listed.
        make_artifact(
            session,
            ticket_id=ticket.id,
            kind=ArtifactKind.HANDOFF_NOT_VALIDATED,
            title="Handoff not validated",
            content={"message": "no gate"},
        )
        slug = session.get(Workspace, ticket.workspace_id).slug
        ids = {"ticket": ticket.id, "sibling": sibling.id, "slug": slug}
    return client, isolated_db, ids


def _pairs(body: dict) -> list[tuple[str, str]]:
    return [(item["from_agent"], item["to_agent"]) for item in body["items"]]


def test_a_tickets_handoffs_are_its_own_newest_first_with_their_checklists(feed):
    client, _, ids = feed
    body = client.get("/api/handoffs", params={"ticket_id": ids["ticket"]}).json()
    assert _pairs(body) == [("test_designer", "test_breaker"), ("spec", "test_designer")]
    assert body["total"] == 2
    first = body["items"][0]
    assert first["ticket_external_id"]
    assert first["unreadable"] == ""
    assert first["checklist"][0]["status"] == "complete"
    assert first["checklist"][0]["certainty"] == "verified"


def test_a_workspace_feed_leaves_out_other_workspaces_and_counts_past_the_limit(feed):
    client, _, ids = feed
    body = client.get("/api/handoffs", params={"workspace": ids["slug"], "limit": 2}).json()
    assert _pairs(body) == [("planner", "spec"), ("test_designer", "test_breaker")]
    assert body["total"] == 3


def test_the_unscoped_feed_spans_every_workspace(feed):
    client, _, _ = feed
    body = client.get("/api/handoffs").json()
    assert body["total"] == 4
    assert body["items"][0]["workspace_slug"] == "elsewhere"


def test_an_unreadable_handoff_keeps_its_place_and_says_why(feed):
    client, isolated_db, ids = feed
    with Session(isolated_db) as session:
        make_artifact(
            session,
            ticket_id=ids["sibling"],
            kind=ArtifactKind.HANDOFF,
            title="handoff ?",
            content={"something": "else"},
        )
        # Attached by hand as the bare body, as two live rows were: still readable.
        make_artifact(
            session,
            ticket_id=ids["sibling"],
            kind=ArtifactKind.HANDOFF,
            title="handoff ui → spec",
            content={"from_agent": "ui-design-decision", "to_agent": "spec", "note": "by hand"},
            created_at=utcnow() - timedelta(minutes=1),
        )
    body = client.get("/api/handoffs", params={"ticket_id": ids["sibling"]}).json()
    unreadable, by_hand, _ = body["items"]
    assert unreadable["unreadable"].startswith("not a handoff document")
    assert (unreadable["from_agent"], unreadable["to_agent"]) == ("", "")
    assert (by_hand["from_agent"], by_hand["note"], by_hand["unreadable"]) == (
        "ui-design-decision",
        "by hand",
        "",
    )
    assert by_hand["required_items_met"] is None


@pytest.mark.parametrize("params", [{"ticket_id": "no-such-ticket"}, {"workspace": "no-such"}])
def test_an_unknown_scope_is_a_404_not_an_empty_feed(feed, params):
    client, _, _ = feed
    assert client.get("/api/handoffs", params=params).status_code == 404


def test_a_search_narrows_the_whole_workspace_not_just_the_newest_page(feed):
    client, _, ids = feed
    # The oldest handoff in the workspace: off the page at limit=1, found by the search.
    body = client.get(
        "/api/handoffs", params={"workspace": ids["slug"], "limit": 1, "q": "SPEC"}
    ).json()
    assert body["total"] == 2  # spec → test_designer, planner → spec
    assert _pairs(body) == [("planner", "spec")]
    oldest = client.get("/api/handoffs", params={"workspace": ids["slug"], "q": "test_designer"})
    assert _pairs(oldest.json()) == [("test_designer", "test_breaker"), ("spec", "test_designer")]


def test_a_search_matches_the_ticket_id_and_title_too(feed):
    client, _, ids = feed
    by_id = client.get("/api/handoffs", params={"workspace": ids["slug"], "q": "feed-sib"}).json()
    by_title = client.get("/api/handoffs", params={"workspace": ids["slug"], "q": "BUDGET"}).json()
    assert _pairs(by_id) == _pairs(by_title) == [("planner", "spec")]


def test_a_search_reads_a_bare_body_handoff_and_takes_wildcards_literally(feed):
    client, isolated_db, ids = feed
    with Session(isolated_db) as session:
        make_artifact(
            session,
            ticket_id=ids["sibling"],
            kind=ArtifactKind.HANDOFF,
            title="handoff by hand",
            content={"from_agent": "ui-design-decision", "to_agent": "spec"},
        )
    body = client.get("/api/handoffs", params={"q": "ui-design"}).json()
    assert _pairs(body) == [("ui-design-decision", "spec")]
    # LIKE's wildcards are text here: "%" matches nothing, rather than everything.
    assert client.get("/api/handoffs", params={"q": "%"}).json() == {"items": [], "total": 0}
