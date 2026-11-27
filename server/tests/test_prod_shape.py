"""The production-shaped scenario has the proportions the UI must survive."""

from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path

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
from loregarden.testing.prod_shape_findings import INVARIANT_FINDINGS
from loregarden.testing.prod_shape_initiatives import INITIATIVE_CALIBRATION
from loregarden.testing.prod_shape_tree import TREE_CALIBRATION
from sqlmodel import Session, select
from tests.prod_shape_templates import (
    ProdShapeTemplate,
    build_prod_shape_template,
    restore_prod_shape,
)


@pytest.fixture(name="seeded_prod_shape", scope="module")
def seeded_prod_shape_fixture(tmp_path_factory, seeded_template) -> ProdShapeTemplate:
    """The scenario over the seeded database `client` starts from, built once per module."""
    root = tmp_path_factory.mktemp("seeded-prod-shape")
    return build_prod_shape_template(root, seeded_template.database)


@pytest.fixture(name="bare_prod_shape", scope="module")
def bare_prod_shape_fixture(tmp_path_factory, schema_template) -> ProdShapeTemplate:
    """The scenario alone, over the bare schema, built once per module."""
    return build_prod_shape_template(tmp_path_factory.mktemp("bare-prod-shape"), schema_template)


@pytest.fixture(name="prod_shape")
def prod_shape_fixture(client, isolated_db, seeded_prod_shape):
    """The scenario in this test's database and memory store, behind a running app.

    Copied after `client` starts, as it was built after it, then the seeded
    workspace pointed back at the repo `client` gave this test.
    """
    with Session(isolated_db) as session:
        repo_path = _seeded_workspace(session).repo_path
    restore_prod_shape(seeded_prod_shape, isolated_db, graphs=True)
    with Session(isolated_db) as session:
        workspace = _seeded_workspace(session)
        workspace.repo_path = repo_path
        session.add(workspace)
        session.commit()
    return seeded_prod_shape.summary


def _seeded_workspace(session: Session) -> Workspace:
    return session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()


def test_counts_match_the_calibration(prod_shape):
    expected = sum(ws.tickets for ws in CALIBRATION.workspaces.values())
    assert abs(prod_shape.tickets - expected) <= len(CALIBRATION.types) * 4
    assert prod_shape.milestones == 75
    invariants = sum(shape.live + shape.finished for shape in INVARIANT_FINDINGS)
    assert prod_shape.findings == CALIBRATION.findings_total + invariants
    assert prod_shape.memory_records == CALIBRATION.memory


def test_building_twice_changes_nothing(prod_shape, isolated_db):
    with Session(isolated_db) as session:
        again = build_prod_shape(session, graph_path=AgentMemoryService.from_settings().graph_path)
    assert again == prod_shape


def _row_counts(database: Path) -> dict[str, int]:
    conn = sqlite3.connect(database)
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    finally:
        conn.close()


def test_the_session_template_is_what_a_fresh_build_writes(seeded_prod_shape, prod_shape_template):
    """`prod_shape_session` copies a scenario built once per process; it must hold
    what this module's own build over the same seeded database wrote. That build
    is already paid for by this module's tests, so the check costs no third one."""
    fresh = _row_counts(seeded_prod_shape.database)

    assert fresh["tickets"] > 100, fresh
    assert _row_counts(prod_shape_template.database) == fresh
    assert prod_shape_template.summary == seeded_prod_shape.summary


def test_the_monitor_sees_mostly_finished_tickets(prod_shape, client):
    findings = client.get("/api/monitor/findings").json()
    on_tickets = [f for f in findings if f["ticket_id"]]
    finished = [f for f in on_tickets if f["ticket_state"] in ("done", "wont_do")]

    assert len(on_tickets) == 488
    assert len(finished) == 441
    assert all(f["ticket_title"] for f in on_tickets)
    oversized = [
        f for f in on_tickets if f["stage_key"] == "tickets_are_created_under_the_size_caps"
    ]
    assert len({f["ticket_id"] for f in oversized}) == 94
    assert len({f["summary"] for f in oversized}) == 1
    assert len({f["evidence"]["detail"] for f in oversized}) == 94


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


def test_initiatives_own_and_track_like_production(prod_shape, client):
    """Live, 2026-11-26: six initiatives, 28 milestones owned and 132 tickets
    tracked by membership, one initiative owning nothing. The scenario once
    carried a lone initiative, so the page that stacked six cards into five
    screens rendered fine over it."""
    initiatives = client.get("/api/initiatives").json()
    shapes = INITIATIVE_CALIBRATION.initiatives
    unclaimed = len(client.get("/api/initiatives/attachable-milestones").json())

    assert len(initiatives) == len(shapes) == 6
    assert all(i["external_id"].startswith("init-") for i in initiatives)
    rows = [m for i in initiatives for m in i["milestones"]]
    members = [m for m in rows if m["member"]]
    owned = [m for m in rows if not m["member"]]
    # Every member live counts, each a feature or bug that keeps its own milestone.
    assert len(members) == prod_shape.initiatives.members == 132
    assert all(m["work_item_type"] in ("feature", "bug") and m["home_milestone"] for m in members)
    assert len({m["id"] for m in members}) == len(members)
    # Owned milestones as far as the scenario's themes supply them (live: 28).
    assert len(owned) == prod_shape.initiatives.owned
    assert 20 <= len(owned) <= sum(shape.owned for shape in shapes)
    # One initiative owns nothing and tracks a long list, a quarter of it done.
    [tracker] = [
        i for i in initiatives if i["milestones"] and all(m["member"] for m in i["milestones"])
    ]
    assert tracker["progress"] == {"resolved": 23, "total": 97}
    # The rollup, not the factory default, set the states, and they differ.
    assert {"blocked", "in_progress"} <= {i["state"] for i in initiatives}
    # Most milestones stay unclaimed (live: 80 of 108).
    assert unclaimed / (unclaimed + len(owned)) > 0.65


def test_activity_digest_matches_the_live_two_weeks(isolated_db, bare_prod_shape):
    """Live, 2026-10-17: 405 loregarden tickets had something happen in 14 days.

    Over the scenario alone: the `client` fixture's bootstrap seed writes
    events of its own, which are not production's shape.
    """
    restore_prod_shape(bare_prod_shape, isolated_db, graphs=False)
    with Session(isolated_db) as session:
        for slug, shape in ACTIVITY_CALIBRATION.items():
            workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).one()
            digest = workspace_activity(session, workspace, utcnow() - ACTIVITY_WINDOW)
            assert digest.totals == shape.kinds
            assert len(digest.tickets) + digest.more_tickets == shape.tickets


def test_the_tree_nests_parks_and_holds_like_production(isolated_db, bare_prod_shape):
    """Live, 2026-10-19: four levels below the roots, 12 stages awaiting a person,
    and blocked work waiting on people, agents and other tickets.

    Over the scenario alone, as the recorded tree is: the seed adds tickets of its own.
    """
    restore_prod_shape(bare_prod_shape, isolated_db, graphs=False)
    summary = bare_prod_shape.summary
    with Session(isolated_db) as session:
        stored = len(session.exec(select(Ticket)).all())
    # Member links are copies of a ticket listed under the initiative tracking it.
    nodes = [(n, d) for n, d in _walk(_tree(isolated_db), depth=1) if not n["member_link"]]
    # An initiative is blocked through its rollup, not held by a prerequisite.
    blocked_on = Counter(
        node["blocked_on"] for node, _depth in nodes if node["work_item_type"] != "initiative"
    )
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
