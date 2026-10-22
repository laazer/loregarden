"""The reference library's read model: states, workspace scope, detail, learnings."""

from __future__ import annotations

from unittest import mock

import pytest
from fastapi.testclient import TestClient
from loregarden.models.domain import Approval, ApprovalKind, ApprovalStatus
from loregarden.models.domain.survey_enums import (
    LearningsAvailability,
    LibraryState,
    SurveyPassMode,
    SurveyPassStatus,
    SurveySourceKind,
    SurveySourceStatus,
    SurveyTicketAction,
)
from loregarden.models.domain.survey_tables import SurveyPass, SurveyPassTicket, SurveySource
from loregarden.services.memory_store import AgentMemoryService, MemoryGraphStore
from loregarden.services.reference_survey import library
from loregarden.services.reference_survey.library import library_detail, library_list
from sqlmodel import Session
from tests.survey_support import build_world, running_pass, second_workspace, serve, write_tree


@pytest.fixture(name="ctx")
def ctx_fixture(isolated_db, tmp_path):
    session = Session(isolated_db)
    world = build_world(session)
    yield session, world, tmp_path
    session.close()


def _source(session, world, name, status, *, approval=None):
    source = SurveySource(
        workspace_id=world.workspace_id, kind=SurveySourceKind.REPO, url=f"https://github.com/acme/{name}",
        name=name, status=status, approval_id=approval,
    )  # fmt: skip
    session.add(source)
    session.commit()
    serve(session, source, world.workspace_id)
    return source


def test_each_source_is_shown_in_the_state_that_leads_to_its_next_action(ctx):
    session, world, tmp_path = ctx
    card = Approval(
        workspace_id=world.workspace_id,
        kind=ApprovalKind.HUMAN_ACTION,
        title="Admit",
        status=ApprovalStatus.PENDING,
    )
    session.add(card)
    session.commit()
    _source(session, world, "awaiting", SurveySourceStatus.PROPOSED, approval=card.id)
    _source(session, world, "candidate", SurveySourceStatus.PROPOSED)
    queued = _source(session, world, "queued", SurveySourceStatus.ACTIVE)
    session.add(SurveyPass(source_id=queued.id, ordinal=1, mode=SurveyPassMode.INITIAL))
    running_pass(
        session,
        world,
        write_tree(tmp_path / "t", {"a.py": "a"}),
        url="https://github.com/acme/reading",
    )
    _source(session, world, "surveyed", SurveySourceStatus.DONE)
    _source(session, world, "refused", SurveySourceStatus.REFUSED)
    session.commit()

    listing = library_list(session, "loregarden")
    states = {row.name: row.state for row in listing.sources}
    assert states == {
        "awaiting": LibraryState.AWAITING_APPROVAL,
        "candidate": LibraryState.CANDIDATE,
        "queued": LibraryState.QUEUED,
        "widget": LibraryState.READING,
        "surveyed": LibraryState.SURVEYED,
        "refused": LibraryState.REFUSED,
    }
    assert listing.counts == dict.fromkeys(LibraryState, 1)
    awaiting = next(row for row in listing.sources if row.name == "awaiting")
    assert awaiting.approval_id == card.id


def test_the_list_holds_only_sources_serving_the_workspace(ctx):
    session, world, _ = ctx
    blobert = second_workspace(session, world)
    both = _source(session, world, "both", SurveySourceStatus.DONE)
    serve(session, both, blobert.id)
    only_blobert = SurveySource(
        workspace_id=blobert.id, kind=SurveySourceKind.REPO, url="https://github.com/b/b", name="b"
    )
    session.add(only_blobert)
    session.commit()
    serve(session, only_blobert, blobert.id)
    assert [r.name for r in library_list(session, "loregarden").sources] == ["both"]
    assert {r.name for r in library_list(session, "blobert").sources} == {"both", "b"}
    assert library_list(session, "loregarden").sources[0].workspaces == ["loregarden", "blobert"]


def _with_links(session, world, tmp_path):
    source, survey_pass = running_pass(session, world, write_tree(tmp_path / "c", {"a.py": "a"}))
    second = SurveyPass(
        source_id=source.id,
        ordinal=2,
        mode=SurveyPassMode.CONTINUATION,
        status=SurveyPassStatus.FINISHED,
    )
    session.add(second)
    session.commit()
    for p, action in (
        (survey_pass, SurveyTicketAction.APPENDED),
        (second, SurveyTicketAction.SPLIT),
    ):
        session.add(SurveyPassTicket(pass_id=p.id, ticket_id=world.feature_id, action=action))
    session.add(
        SurveyPassTicket(
            pass_id=second.id, ticket_id=None, action=SurveyTicketAction.MENTIONED, legacy_ref="267"
        )
    )
    session.commit()
    return source


def test_detail_folds_each_ticket_once_with_every_action_and_pass(ctx):
    session, world, tmp_path = ctx
    source = _with_links(session, world, tmp_path)
    with mock.patch.object(
        AgentMemoryService,
        "require_graph",
        side_effect=ValueError("Memory graph SQLite is not configured."),
    ):
        detail = library_detail(session, source.id)
    assert [(t.external_id, t.actions, t.passes) for t in detail.tickets] == [
        (world.feature_ext, ["appended", "split"], [1, 2])
    ]
    assert detail.unresolved_refs == ["267"]
    assert [p.ordinal for p in detail.passes] == [1, 2]
    assert detail.source.tickets == 1 and detail.source.unresolved_refs == 1


def test_learnings_unavailable_is_its_own_answer_not_an_empty_list(ctx):
    session, world, tmp_path = ctx
    source = _with_links(session, world, tmp_path)
    with mock.patch.object(
        AgentMemoryService,
        "require_graph",
        side_effect=ValueError("Memory graph SQLite is not configured."),
    ):
        learnings = library_detail(session, source.id).learnings
    assert learnings.availability is LearningsAvailability.UNAVAILABLE
    assert "not configured" in learnings.reason


def test_learnings_recorded_on_linked_tickets_are_found_by_id_or_external_id(ctx):
    session, world, tmp_path = ctx
    source = _with_links(session, world, tmp_path)
    graph = MemoryGraphStore(tmp_path / "graph.db")
    graph.upsert_node(node_id="l1", title="By external id", body="b", ticket_id=world.feature_ext,
                      workspace_slug="loregarden", node_type=library.MemoryNodeType.LEARNING)  # fmt: skip
    graph.upsert_node(node_id="l2", title="By uuid", body="b", ticket_id=world.feature_id,
                      workspace_slug="loregarden", node_type=library.MemoryNodeType.LEARNING)  # fmt: skip
    graph.upsert_node(node_id="l3", title="Elsewhere", body="b", ticket_id="lg-other-9",
                      workspace_slug="loregarden", node_type=library.MemoryNodeType.LEARNING)  # fmt: skip
    with mock.patch.object(AgentMemoryService, "require_graph", return_value=graph):
        learnings = library_detail(session, source.id).learnings
    assert learnings.availability is LearningsAvailability.FOUND
    assert sorted(item.title for item in learnings.items) == ["By external id", "By uuid"]
    assert {item.ticket for item in learnings.items} == {world.feature_ext}


def test_a_source_with_no_linked_tickets_has_empty_learnings(ctx):
    session, world, _ = ctx
    source = _source(session, world, "bare", SurveySourceStatus.DONE)
    learnings = library_detail(session, source.id).learnings
    assert (learnings.availability, learnings.items) == (LearningsAvailability.EMPTY, [])


def test_the_api_answers_404_for_what_does_not_exist(client: TestClient):
    assert (
        client.get("/api/reference-library", params={"workspace_slug": "nowhere"}).status_code
        == 404
    )
    assert client.get("/api/reference-library/missing").status_code == 404
    ok = client.get("/api/reference-library", params={"workspace_slug": "loregarden"})
    assert ok.status_code == 200 and ok.json()["sources"] == []
