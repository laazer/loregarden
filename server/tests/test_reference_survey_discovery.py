"""Quick-add topics, discovery runs that find references, and keeping or dropping
what they find."""

from __future__ import annotations

import json
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from loregarden.agents.registry import AGENTS
from loregarden.config import settings
from loregarden.db.migration_utils import table_columns, table_exists
from loregarden.db.versions.reference_survey_record import m_reference_survey_record
from loregarden.db.versions.survey_discoveries import m_survey_discoveries
from loregarden.db.versions.survey_source_description import m_survey_source_description
from loregarden.db.versions.surveyor_discovery_runs import (
    MARKER,
    ROLE_FILE,
    SLUG,
    m_surveyor_discovery_runs,
)
from loregarden.mcp.tools import execute_tool
from loregarden.models.domain import Approval, ApprovalStatus, StudioAgent, Ticket, WorkItemType
from loregarden.models.domain.survey_enums import (
    SurveyPassMode,
    SurveySourceKind,
    SurveySourceStatus,
    SurveyTopic,
)
from loregarden.models.domain.survey_tables import SurveyDiscovery, SurveyPass, SurveySource
from loregarden.services.reference_survey import discovery as discovery_module
from loregarden.services.reference_survey.discovery import (
    DiscoveryIn,
    enqueue_found,
    start_discovery,
)
from loregarden.services.reference_survey.errors import SurveyError, SurveyNotFound
from loregarden.services.reference_survey.initiatives import linked_initiatives
from loregarden.services.reference_survey.queue import drop_source, keep_source, target_workspaces
from loregarden.services.reference_survey.schemas import SourceIn
from loregarden.services.reference_survey.topics import TOPICS
from sqlalchemy import create_engine
from sqlmodel import Session, select
from tests.factories import make_ticket
from tests.role_refresh_helpers import strip_role_marker
from tests.survey_support import build_world, second_workspace


@pytest.fixture(name="ctx")
def ctx_fixture(isolated_db):
    session = Session(isolated_db)
    world = build_world(session)
    other = second_workspace(session, world)
    make_ticket(
        session, workspace_id=None, external_id="init-mem-1", title="Better memory",
        work_item_type=WorkItemType.INITIATIVE,
    )  # fmt: skip
    yield session, world, other
    session.close()


def _start(session, world, **kw):
    body = DiscoveryIn(
        **({"topic": SurveyTopic.MEMORY_LAYER, "workspace_slugs": [world.workspace_slug]} | kw)
    )
    with (
        mock.patch.object(discovery_module, "QueueLaneService") as lanes,
        mock.patch.object(discovery_module, "least_busy_lane", return_value=2),
    ):
        view = start_discovery(session, body)
    return view, lanes


def test_every_topic_carries_a_question_a_reason_and_a_search():
    assert set(TOPICS) == set(SurveyTopic)
    for topic in TOPICS.values():
        assert topic.label and topic.question.endswith("?") and topic.why and topic.search


def test_a_discovery_run_is_a_carrier_ticket_queued_in_a_lane_with_its_brief(ctx):
    session, world, other = ctx
    view, lanes = _start(
        session, world, workspace_slugs=[world.workspace_slug, other.slug],
        initiative_ids=["init-mem-1"], focus="graph-backed", wanted=7,
    )  # fmt: skip
    carrier = session.exec(select(Ticket).where(Ticket.external_id == view.carrier)).one()
    assert carrier.title == "Discover references: Improved memory layer"
    assert carrier.parent_ticket_id == world.carrier_parent_id
    assert f"`{view.id}`" in carrier.description
    assert "up to 7 references" in carrier.description
    assert "Narrowed to: graph-backed." in carrier.description
    assert f"workspace_slugs=[{world.workspace_slug}, {other.slug}]" in carrier.description
    lanes.return_value.add_to_lane.assert_called_once_with(
        ticket_id=carrier.id, slot_number=2, auto_approve=True
    )
    assert (view.found, view.workspaces) == (0, [world.workspace_slug, other.slug])


def test_a_run_needs_a_carrier_parent_and_is_refused_in_a_sandbox(ctx):
    session, world, _ = ctx
    with mock.patch.object(discovery_module.settings, "sandbox", True):
        with pytest.raises(SurveyError, match="sandbox"):
            _start(session, world)
    parent = session.get(Ticket, world.carrier_parent_id)
    parent.tags_json = "[]"
    session.add(parent)
    session.commit()
    with pytest.raises(SurveyError, match="reference-survey-carrier"):
        _start(session, world)
    with pytest.raises(SurveyNotFound, match="No initiative"):
        _start(session, world, initiative_ids=["init-none-9"])
    assert session.exec(select(SurveyDiscovery)).all() == []


def test_a_find_serves_the_runs_workspaces_and_initiatives_and_waits_for_a_person(ctx):
    session, world, other = ctx
    view, _ = _start(
        session, world, workspace_slugs=[world.workspace_slug, other.slug], initiative_ids=["init-mem-1"]
    )  # fmt: skip
    found = enqueue_found(
        session,
        SourceIn(workspace_slugs=[world.workspace_slug], kind=SurveySourceKind.REPO, url="https://github.com/mem0ai/mem0"),
        view.id,
    ).source  # fmt: skip
    assert found.status is SurveySourceStatus.PROPOSED
    assert session.get(Approval, found.approval_id).status is ApprovalStatus.PENDING
    assert found.discovery_id == view.id
    assert found.question == TOPICS[SurveyTopic.MEMORY_LAYER].question
    assert [w.slug for w, _ in target_workspaces(session, found.id)] == [
        world.workspace_slug,
        other.slug,
    ]
    assert [i.external_id for i in linked_initiatives(session, found.id)] == ["init-mem-1"]


def test_an_agent_enqueues_a_find_through_the_queue_tool(ctx):
    session, world, _ = ctx
    view, _ = _start(session, world)
    answer = json.loads(
        execute_tool(
            session, "loregarden_survey_queue",
            {"action": "enqueue", "discovery_id": view.id, "workspace_slugs": [world.workspace_slug],
             "kind": "repo", "url": "https://github.com/letta-ai/letta", "queued_reason": "Stateful agents"},
            orchestrated=True, run_id="",
        )
    )  # fmt: skip
    assert answer["status"] == "proposed"
    assert session.get(SurveySource, answer["source_id"]).discovery_id == view.id


def test_keep_queues_a_proposal_and_approves_its_card(ctx):
    session, world, _ = ctx
    view, _ = _start(session, world)
    found = enqueue_found(
        session, SourceIn(workspace_slugs=[world.workspace_slug], kind=SurveySourceKind.REPO, url="https://github.com/a/keep"), view.id
    ).source  # fmt: skip
    card_id = found.approval_id
    kept = keep_source(session, found.id)
    assert kept.status is SurveySourceStatus.ACTIVE
    assert session.get(Approval, card_id).status is ApprovalStatus.APPROVED
    passes = session.exec(select(SurveyPass).where(SurveyPass.source_id == found.id)).all()
    assert [(p.ordinal, p.mode) for p in passes] == [(1, SurveyPassMode.INITIAL)]
    with pytest.raises(SurveyError, match="not waiting to be kept"):
        keep_source(session, found.id)


def test_drop_refuses_a_proposal_with_the_reason_and_rejects_its_card(ctx):
    session, world, _ = ctx
    view, _ = _start(session, world)
    found = enqueue_found(
        session, SourceIn(workspace_slugs=[world.workspace_slug], kind=SurveySourceKind.REPO, url="https://github.com/a/drop"), view.id
    ).source  # fmt: skip
    card_id = found.approval_id
    dropped = drop_source(session, found.id, "abandoned since 2021")
    assert dropped.status is SurveySourceStatus.REFUSED
    assert dropped.refused_reason == "A person dropped it: abandoned since 2021."
    assert session.get(Approval, card_id).status is ApprovalStatus.REJECTED


def test_the_library_shows_runs_with_their_finds_and_names_the_topic_on_each(
    client: TestClient, isolated_db
):
    with Session(isolated_db) as session:
        world = build_world(session)
        view, _ = _start(session, world)
        enqueue_found(
            session, SourceIn(workspace_slugs=[world.workspace_slug], kind=SurveySourceKind.REPO, url="https://github.com/a/found"), view.id
        )  # fmt: skip
    listing = client.get(
        "/api/reference-library", params={"workspace_slug": world.workspace_slug}
    ).json()
    assert [(d["label"], d["found"]) for d in listing["discoveries"]] == [
        ("Improved memory layer", 1)
    ]
    row = next(r for r in listing["sources"] if r["name"] == "found")
    assert (row["state"], row["discovered_by"]) == ("awaiting_approval", "Improved memory layer")
    topics = client.get("/api/reference-library/topics").json()
    assert [t["id"] for t in topics] == [t.value for t in SurveyTopic]
    kept = client.post(f"/api/reference-library/{row['id']}/keep").json()
    assert kept["state"] == "queued"
    assert client.post(f"/api/reference-library/{row['id']}/drop", json={}).status_code == 409


def test_the_api_says_why_a_run_cannot_start(client: TestClient, isolated_db):
    with Session(isolated_db) as session:
        world = build_world(session)
        parent = session.get(Ticket, world.carrier_parent_id)
        parent.tags_json = "[]"
        session.add(parent)
        session.commit()
    response = client.post(
        "/api/reference-library/discover",
        json={"topic": "ui_ux", "workspace_slugs": [world.workspace_slug]},
    )
    assert response.status_code == 409
    assert "reference-survey-carrier" in response.json()["detail"]


def test_the_discovery_table_and_column_are_added_idempotently():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for table in ("workspaces", "approvals", "tickets"):
            conn.exec_driver_sql(f"CREATE TABLE {table} (id VARCHAR PRIMARY KEY)")
        m_reference_survey_record(conn)
        m_survey_source_description(conn)
        m_survey_discoveries(conn)
        m_survey_discoveries(conn)
        assert table_exists(conn, "survey_discoveries")
        assert "discovery_id" in table_columns(conn, "survey_sources")


def test_the_surveyor_role_carries_discovery_runs_once_migrated(isolated_db):
    assert AGENTS[SLUG]["role_file"] == ROLE_FILE
    assert MARKER in (settings.agent_context_dir / ROLE_FILE).read_text(encoding="utf-8")
    with Session(isolated_db) as session:
        before = strip_role_marker(session, SLUG, MARKER)
    with isolated_db.begin() as conn:
        m_surveyor_discovery_runs(conn)
        m_surveyor_discovery_runs(conn)
    with Session(isolated_db) as session:
        agent = session.exec(select(StudioAgent).where(StudioAgent.slug == SLUG)).one()
        assert MARKER in agent.role_body
        assert agent.version == before + 1
