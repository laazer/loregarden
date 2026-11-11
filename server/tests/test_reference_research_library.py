"""Research meets the library: queueing picks, the question on the brief, and what is known per project."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from loregarden.mcp.survey_tools import HANDLERS as SURVEY_HANDLERS
from loregarden.models.domain import BaxterChatSession
from loregarden.models.domain.chat_enums import ChatThreadMode
from loregarden.models.domain.survey_enums import BulkAddOutcome, ClaimStatus
from loregarden.models.domain.survey_research_tables import (
    SurveyResearchCandidate,
    SurveySourceResearch,
)
from loregarden.models.domain.survey_tables import SurveySource
from loregarden.services.reference_survey import research
from loregarden.services.reference_survey.errors import SurveyError, SurveyNotFound
from loregarden.services.reference_survey.feeder import _brief
from loregarden.services.reference_survey.report import pass_brief
from loregarden.services.reference_survey.research import (
    CandidateIn,
    ClaimIn,
    QuestionIn,
    RefuteIn,
    open_question,
    record_candidates,
    refute,
)
from loregarden.services.reference_survey.research_context import (
    brief_lines,
    project_view,
    source_research,
    workspace_questions,
)
from loregarden.services.reference_survey.research_library import QueuePicksIn, queue_picks
from sqlmodel import Session, select
from tests.survey_support import build_world, running_pass, write_tree

RIGGER = "https://github.com/acme/rigger"
OTHER = "https://github.com/acme/other"
SKELETON = "generates a skeleton for an unrigged mesh"
SKINNING = "computes skin weights"


@pytest.fixture(name="ctx")
def ctx_fixture(isolated_db):
    session = Session(isolated_db)
    world = build_world(session)
    thread = BaxterChatSession(
        workspace_id=world.workspace_id, title="Rigging", mode=ChatThreadMode.RESEARCH
    )
    session.add(thread)
    session.commit()
    question = open_question(
        session, QuestionIn(thread_id=thread.id, question="Which repos auto-rig a mesh?",
                            capabilities=[SKELETON, SKINNING]),
    )  # fmt: skip
    with mock.patch.object(
        research, "gh_api_json", side_effect=research.UpstreamUnavailable("offline")
    ):
        record_candidates(session, question.id, [
            CandidateIn(url=RIGGER, name="Rigger", claims=[
                ClaimIn(capability=SKELETON, status=ClaimStatus.DOCS_ONLY, note="README says so"),
            ]),
            CandidateIn(url=OTHER, name="Other"),
        ])  # fmt: skip
    yield session, world, thread, question
    session.close()


def test_queueing_picks_admits_them_into_the_questions_workspace_and_links_the_question(ctx):
    session, world, _, question = ctx
    items = queue_picks(
        session, QueuePicksIn(urls=[RIGGER + ".git", OTHER], question_id=question.id)
    )

    assert [(item.url, item.outcome) for item in items] == [
        (RIGGER, BulkAddOutcome.QUEUED), (OTHER, BulkAddOutcome.QUEUED),
    ]  # fmt: skip
    source = session.exec(select(SurveySource).where(SurveySource.url == RIGGER)).one()
    assert source.workspace_id == world.workspace_id
    assert source.queued_reason == f"Research: {question.question}"
    links = session.exec(select(SurveySourceResearch)).all()
    assert {(link.source_id, link.question_id) for link in links} == {
        (item.source.id, question.id) for item in items
    }
    candidate = session.exec(
        select(SurveyResearchCandidate).where(SurveyResearchCandidate.url == RIGGER)
    ).one()
    assert candidate.source_id == source.id


def test_queueing_twice_links_once_and_without_a_question_needs_a_workspace(ctx):
    session, world, _, question = ctx
    queue_picks(session, QueuePicksIn(urls=[RIGGER], question_id=question.id))
    again = queue_picks(session, QueuePicksIn(urls=[RIGGER], question_id=question.id))
    assert again[0].outcome is BulkAddOutcome.ALREADY
    assert len(session.exec(select(SurveySourceResearch)).all()) == 1
    with pytest.raises(SurveyError, match="needs a workspace"):
        queue_picks(session, QueuePicksIn(urls=[OTHER]))
    with pytest.raises(SurveyNotFound):
        queue_picks(session, QueuePicksIn(urls=[OTHER], question_id="nope"))
    loose = queue_picks(session, QueuePicksIn(urls=[OTHER], workspace_slug=world.workspace_slug))
    assert loose[0].outcome is BulkAddOutcome.QUEUED


def test_the_agents_enqueue_names_its_question_and_is_linked_the_same_way(ctx):
    session, world, _, question = ctx
    reply = SURVEY_HANDLERS["loregarden_survey_queue"](session, {
        "action": "enqueue", "kind": "repo", "url": RIGGER,
        "workspace_slugs": [world.workspace_slug], "research_question_id": question.id,
    })  # fmt: skip
    source_id = json.loads(reply)["source_id"] if isinstance(reply, str) else reply["source_id"]
    assert session.get(SurveySourceResearch, (source_id, question.id)) is not None
    with pytest.raises(SurveyNotFound):
        SURVEY_HANDLERS["loregarden_survey_queue"](session, {
            "action": "enqueue", "kind": "repo", "url": OTHER,
            "workspace_slugs": [world.workspace_slug], "research_question_id": "nope",
        })  # fmt: skip


def test_a_pass_on_a_researched_source_is_briefed_with_the_question_and_its_claims(
    ctx, tmp_path: Path
):
    session, world, _, question = ctx
    source, survey_pass = running_pass(session, world, write_tree(tmp_path / "t", {"a.py": "x\n"}),
                                       url=RIGGER)  # fmt: skip
    session.add(SurveySourceResearch(source_id=source.id, question_id=question.id))
    session.commit()

    contexts = source_research(session, source)
    lines = brief_lines(contexts)
    assert lines == [
        f"**Research question:** {question.question} — confirm or refute each claim with "
        f"`loregarden_research_record`: {SKELETON} (docs_only); {SKINNING} (not checked)"
    ]
    text = _brief(source, survey_pass, [], None, [world.workspace_slug], [], [], lines)
    assert lines[0] in text
    brief = pass_brief(session, survey_pass.id)
    assert brief["research"][0]["question"] == question.question
    assert [c["capability"] for c in brief["research"][0]["claims"]] == [SKELETON]
    assert any("loregarden_research_record" in rule for rule in brief["rules"])


def test_a_project_shows_every_question_and_the_claim_that_stands(ctx):
    session, _, thread, question = ctx
    second = open_question(session, QuestionIn(thread_id=thread.id, question="Which repos retarget?",
                                                capabilities=["retargets animation"]))  # fmt: skip
    with mock.patch.object(
        research, "gh_api_json", side_effect=research.UpstreamUnavailable("offline")
    ):
        record_candidates(session, second.id, [CandidateIn(url=RIGGER, name="Rigger", claims=[
            ClaimIn(capability="retargets animation", status=ClaimStatus.NOT_FOUND),
        ])])  # fmt: skip
    refute(session, RefuteIn(url=RIGGER, capability=SKELETON, note="it only retargets"))

    view = project_view(session, RIGGER + ".git")
    assert (view.url, view.name, view.source_id) == (RIGGER, "Rigger", None)
    assert {q.id for q in view.questions} == {question.id, second.id}
    by_cap = {claim.capability: claim.status for claim in view.claims}
    assert by_cap == {SKELETON: ClaimStatus.REFUTED, "retargets animation": ClaimStatus.NOT_FOUND}
    empty = project_view(session, "https://github.com/acme/never")
    assert (empty.claims, empty.questions, empty.name) == ([], [], "")


def test_the_workspace_list_counts_candidates_newest_run_first(ctx):
    session, world, thread, question = ctx
    open_question(session, QuestionIn(thread_id=thread.id, question="Not run yet at all",
                                      capabilities=["x"]))  # fmt: skip
    listed = workspace_questions(session, world.workspace_id)
    assert [(q.id, q.candidates, q.run_count) for q in listed][0] == (question.id, 2, 1)
    assert listed[1].candidates == 0


def test_the_api_lists_shows_and_queues(client: TestClient):
    workspace = client.get("/api/workspaces").json()[0]["slug"]
    assert (
        client.get("/api/reference-research/questions", params={"workspace": workspace}).json()
        == []
    )
    assert (
        client.get("/api/reference-research/questions", params={"workspace": "nope"}).status_code
        == 404
    )
    project = client.get("/api/reference-research/projects", params={"url": RIGGER})
    assert (project.status_code, project.json()["claims"]) == (200, [])
    assert (
        client.get("/api/reference-research/projects", params={"url": "ftp://x"}).status_code == 400
    )
    queued = client.post(
        "/api/reference-research/queue", json={"urls": [RIGGER], "workspace_slug": workspace}
    )
    assert queued.status_code == 200, queued.text
    assert queued.json()[0]["outcome"] == "queued"
    assert (
        client.post(
            "/api/reference-research/queue", json={"urls": [RIGGER], "question_id": "nope"}
        ).status_code
        == 404
    )
    assert client.post("/api/reference-research/queue", json={"urls": [RIGGER]}).status_code == 400
