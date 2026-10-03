"""The production-shaped scenario has the proportions the UI must survive."""

from __future__ import annotations

import pytest
from loregarden.services.memory_store import AgentMemoryService
from loregarden.testing.prod_shape import CALIBRATION, build_prod_shape
from sqlmodel import Session


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
    assert initiative["progress"] == {"resolved": 1, "total": 10}
    assert initiative["workspaces"] == ["blobert", "loregarden", "loremaker"]
    # The children are mixed, so the rollup — not the factory default — set this.
    assert initiative["state"] == "in_progress"
    # Most milestones stay unclaimed: that is still the shape that dominates.
    assert len(client.get("/api/initiatives/attachable-milestones").json()) >= 65
