"""The production-shaped scenario has the proportions the UI must survive."""

from __future__ import annotations

from collections import Counter

import pytest
from fastapi.testclient import TestClient
from loregarden.db.session import get_session
from loregarden.main import app
from loregarden.models.domain import BlockKind, Ticket, Workspace
from loregarden.models.domain.enums import utcnow
from loregarden.services.memory_store import AgentMemoryService
from loregarden.services.workspace_activity import workspace_activity
from loregarden.testing.prod_shape import CALIBRATION, build_prod_shape
from loregarden.testing.prod_shape_activity import ACTIVITY_CALIBRATION
from loregarden.testing.prod_shape_activity import WINDOW as ACTIVITY_WINDOW
from loregarden.testing.prod_shape_tree import TREE_CALIBRATION
from sqlmodel import Session, select


@pytest.fixture(name="prod_shape")
def prod_shape_fixture(client, isolated_db):
    with Session(isolated_db) as session:
        summary = build_prod_shape(
            session, graph_path=AgentMemoryService.from_settings().graph_path
        )
    return summary


def test_counts_match_the_calibration(prod_shape):
    expected = sum(ws.tickets for ws in CALIBRATION.workspaces.values())
    assert abs(prod_shape.tickets - expected) <= len(CALIBRATION.types) * 4
    assert prod_shape.milestones == 75
    assert prod_shape.findings == CALIBRATION.findings_total
    assert prod_shape.memory_records == CALIBRATION.memory


def test_building_twice_changes_nothing(prod_shape, isolated_db):
    with Session(isolated_db) as session:
        again = build_prod_shape(session, graph_path=AgentMemoryService.from_settings().graph_path)
    assert again == prod_shape


def test_the_monitor_sees_mostly_finished_tickets(prod_shape, client):
    findings = client.get("/api/monitor/findings").json()
    on_tickets = [f for f in findings if f["ticket_id"]]
    finished = [f for f in on_tickets if f["ticket_state"] in ("done", "wont_do")]

    assert len(on_tickets) == 94
    assert len(finished) == 85
    assert all(f["ticket_title"] for f in on_tickets)


def test_memory_has_no_recorded_links_but_real_groups(prod_shape, client):
    graph = client.get("/api/memory/graph", params={"workspace_slug": "loregarden"}).json()

    assert len(graph["nodes"]) == 34
    assert graph["counts"]["links"] == 0
    kinds = {group["kind"] for group in graph["inferred"]}
    assert {"same_ticket", "same_milestone", "shared_tag"} <= kinds


def test_memory_proposals_match_production(prod_shape, client):
    """Live, 2026-09-28: 25 learnings titled by their ticket, no near-duplicates.
    Templated bodies once made the scenario claim 16 duplicates that production
    does not have — and the Health page was judged against that fiction."""
    proposals = client.get("/api/memory/proposals", params={"workspace_slug": "loregarden"}).json()
    kinds = [p["kind"] for p in proposals]

    assert kinds.count("near_duplicate") == 0
    assert 20 <= kinds.count("generic_title") <= 30


def test_one_initiative_claims_a_cross_workspace_theme(prod_shape, client):
    [initiative] = client.get("/api/initiatives").json()
    assert prod_shape.initiative_milestones == 10
    assert initiative["external_id"].startswith("init-")
    # The ten themed milestones, plus one open feature it tracks by membership.
    assert initiative["progress"] == {"resolved": 1, "total": 11}
    [member] = [m for m in initiative["milestones"] if m["member"]]
    assert member["work_item_type"] == "feature" and member["home_milestone"]
    assert initiative["workspaces"] == ["blobert", "loregarden", "loremaker"]
    # The children are mixed, so the rollup — not the factory default — set this.
    assert initiative["state"] == "in_progress"
    # Most milestones stay unclaimed: that is still the shape that dominates.
    assert len(client.get("/api/initiatives/attachable-milestones").json()) >= 65


def test_activity_digest_matches_the_live_two_weeks(isolated_db):
    """Live, 2026-10-17: 405 loregarden tickets had something happen in 14 days.

    Over the scenario alone: the `client` fixture's bootstrap seed writes
    events of its own, which are not production's shape.
    """
    with Session(isolated_db) as session:
        build_prod_shape(session)
        for slug, shape in ACTIVITY_CALIBRATION.items():
            workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).one()
            digest = workspace_activity(session, workspace, utcnow() - ACTIVITY_WINDOW)
            assert digest.totals == shape.kinds
            assert len(digest.tickets) + digest.more_tickets == shape.tickets


def test_the_tree_nests_parks_and_holds_like_production(isolated_db):
    """Live, 2026-10-19: four levels below the roots, 15 stages awaiting a person,
    and blocked work waiting on people, agents and other tickets.

    Over the scenario alone, as the recorded tree is: the seed adds tickets of its own.
    """
    with Session(isolated_db) as session:
        summary = build_prod_shape(session)
        stored = len(session.exec(select(Ticket)).all())
    # Member links are copies of a ticket listed under the initiative tracking it.
    nodes = [(n, d) for n, d in _walk(_tree(isolated_db), depth=1) if not n["member_link"]]
    blocked_on = Counter(node["blocked_on"] for node, _depth in nodes)
    awaiting = sum(node["workflow_stage_status"] == "awaiting" for node, _depth in nodes)

    assert len(nodes) == stored
    assert max(depth for _node, depth in nodes) >= 4
    scale = summary.tickets / TREE_CALIBRATION.tickets  # as build_prod_shape scales
    assert awaiting == summary.tree.awaiting == round(TREE_CALIBRATION.awaiting * scale)
    assert {"person", "agent", "tickets"} <= set(blocked_on)
    share = TREE_CALIBRATION.prerequisite_held / TREE_CALIBRATION.backlog
    held = blocked_on["tickets"] - summary.tree.blocked_kinds[BlockKind.TICKETS]
    assert held == summary.tree.prerequisite_held
    backlog = sum(node["state"] == "backlog" for node, _depth in nodes)
    assert abs(held / backlog - share) < 0.05


def _walk(nodes, depth):
    for node in nodes:
        yield node, depth
        yield from _walk(node["children"], depth + 1)


def _tree(isolated_db):
    def session_override():
        with Session(isolated_db) as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    try:
        response = TestClient(app).get("/api/tickets/tree")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200, response.text
    return response.json()
