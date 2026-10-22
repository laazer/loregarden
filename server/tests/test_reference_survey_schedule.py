"""What the survey reviews next, why it is held, and reviewing one now."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from loregarden.mcp.tools import execute_tool
from loregarden.models.domain import Approval, Ticket
from loregarden.models.domain.survey_enums import (
    SurveyPassMode,
    SurveyPassStatus,
    SurveySourceKind,
    SurveySourceStatus,
)
from loregarden.models.domain.survey_tables import SurveyPass, SurveySource
from loregarden.services.orchestration import ApprovalService
from loregarden.services.reference_survey import feeder
from loregarden.services.reference_survey.errors import SurveyError
from loregarden.services.reference_survey.inbox import file_card
from loregarden.services.reference_survey.queue import enqueue_source
from loregarden.services.reference_survey.schedule import (
    FIRST_IN_LINE,
    ReviewNowIn,
    queue_schedule,
    review_now,
)
from loregarden.services.reference_survey.schemas import SourceIn
from sqlmodel import Session, select
from tests.survey_support import MIT_TEXT, build_world, running_pass, serve, write_tree


@pytest.fixture(name="ctx")
def ctx_fixture(isolated_db, tmp_path):
    session = Session(isolated_db)
    world = build_world(session)
    tree = write_tree(tmp_path / "clone", {"LICENSE": MIT_TEXT, "src/a.py": "a", "docs/b.md": "b"})
    yield session, world, tree
    session.close()


def _source(session, world, name, *, priority=100, status=SurveySourceStatus.ACTIVE, queued=True):
    source = SurveySource(
        workspace_id=world.workspace_id, kind=SurveySourceKind.REPO, url=f"https://github.com/acme/{name}",
        name=name, question="How do they gate?", queued_reason="r", status=status, priority=priority,
    )  # fmt: skip
    session.add(source)
    session.commit()
    serve(session, source, world.workspace_id)
    if queued:
        session.add(
            SurveyPass(source_id=source.id, ordinal=1, mode=SurveyPassMode.INITIAL, question="q")
        )
        session.commit()
    session.refresh(source)
    return source


def _dispatching(tree):
    """Patch the parts of a dispatch that reach outside the database."""
    return (
        mock.patch.object(feeder, "clone_or_refresh", return_value=(tree, "main", "c" * 40)),
        mock.patch.object(feeder, "QueueLaneService"),
        mock.patch.object(feeder, "least_busy_lane", return_value=1),
    )


def _untag_carrier_parent(session, world):
    parent = session.get(Ticket, world.carrier_parent_id)
    parent.tags_json = "[]"
    session.add(parent)
    session.commit()


def test_the_schedule_names_the_head_and_every_place_in_line(ctx):
    session, world, _ = ctx
    later = _source(session, world, "later", priority=200)
    first = _source(session, world, "first", priority=10)
    _source(session, world, "candidate", status=SurveySourceStatus.PROPOSED)
    schedule = queue_schedule(session)
    assert schedule.next.name == "first"
    assert (schedule.next.waiting, schedule.next.can_start_now) == ("", True)
    assert schedule.positions == {first.id: 1, later.id: 2}
    assert schedule.running == []


def test_the_schedule_says_what_holds_the_head(ctx):
    session, world, tree = ctx
    _source(session, world, "waiting")
    _untag_carrier_parent(session, world)
    held = queue_schedule(session).next
    assert "tagged `reference-survey-carrier`" in held.waiting
    assert not held.can_start_now
    running_pass(session, world, tree)
    schedule = queue_schedule(session)
    assert "at the limit of 1" in schedule.next.waiting
    assert [(r.name, r.ordinal) for r in schedule.running] == [("widget", 1)]


def test_the_next_tick_follows_the_last_one_and_is_unknown_before_it(ctx):
    session, _world, _ = ctx
    with mock.patch.object(feeder, "_CLOCK", feeder._Clock()):
        assert queue_schedule(session).next_tick_at is None
        feeder._CLOCK.last_tick_at = datetime(2026, 10, 22, 12, 0)
        schedule = queue_schedule(session)
    assert schedule.next_tick_at == datetime(2026, 10, 22, 12, 0) + timedelta(
        seconds=schedule.interval_seconds
    )


def test_review_now_dispatches_a_source_out_of_turn_and_puts_it_first(ctx):
    session, world, tree = ctx
    _source(session, world, "head", priority=1)
    behind = _source(session, world, "behind", priority=500)
    clone, lanes, lane = _dispatching(tree)
    with clone, lanes, lane:
        result = review_now(session, behind.id, ReviewNowIn())
    assert (result.started, result.ordinal, result.waiting) == (True, 1, "")
    assert result.carrier is not None
    session.refresh(behind)
    assert behind.priority == FIRST_IN_LINE


def test_review_now_on_a_surveyed_source_queues_a_scoped_pass_on_the_question_given(ctx):
    session, world, tree = ctx
    source, first = running_pass(session, world, tree)
    first.status = SurveyPassStatus.FINISHED
    source.status = SurveySourceStatus.DONE
    session.add_all([first, source])
    session.commit()
    clone, lanes, lane = _dispatching(tree)
    with clone, lanes, lane:
        result = review_now(session, source.id, ReviewNowIn(question="What changed in the gate?"))
    assert (result.started, result.ordinal) == (True, 2)
    second = session.exec(select(SurveyPass).where(SurveyPass.source_id == source.id, SurveyPass.ordinal == 2)).one()  # fmt: skip
    assert (second.mode, second.question) == (SurveyPassMode.SCOPED, "What changed in the gate?")


def test_review_now_held_by_the_limit_says_so_and_leaves_the_pass_first_in_line(ctx):
    session, world, tree = ctx
    running_pass(session, world, tree)
    held = _source(session, world, "held", priority=300)
    with mock.patch.object(feeder, "clone_or_refresh") as clone:
        result = review_now(session, held.id, ReviewNowIn())
    clone.assert_not_called()
    assert not result.started
    assert "at the limit" in result.waiting
    assert queue_schedule(session).positions[held.id] == 1


def test_review_now_without_a_carrier_parent_files_nothing_and_says_why(ctx):
    session, world, _ = ctx
    source = _source(session, world, "orphan")
    _untag_carrier_parent(session, world)
    result = review_now(session, source.id, ReviewNowIn())
    assert not result.started
    assert "tagged `reference-survey-carrier`" in result.waiting
    assert not session.exec(select(Approval).where(Approval.title == "Reference survey has no carrier parent")).all()  # fmt: skip


@pytest.mark.parametrize(
    ("status", "approval", "message"),
    [
        (SurveySourceStatus.PROPOSED, False, "has not been proposed"),
        (SurveySourceStatus.PROPOSED, True, "approve its card in the inbox"),
        (SurveySourceStatus.REFUSED, False, "was refused"),
    ],
)
def test_review_now_refuses_a_source_not_admitted(ctx, status, approval, message):
    session, world, _ = ctx
    source = _source(session, world, "gated", status=status, queued=False)
    if approval:
        card = file_card(
            session, workspace_id=world.workspace_id, title="Admit", impact="i", level="medium"
        )
        source.approval_id = card.id
        session.add(source)
        session.commit()
    with pytest.raises(SurveyError, match=message):
        review_now(session, source.id, ReviewNowIn())


def test_review_now_refuses_a_source_being_read(ctx):
    session, world, tree = ctx
    source, _ = running_pass(session, world, tree)
    with pytest.raises(SurveyError, match="Pass 1 is reading widget now"):
        review_now(session, source.id, ReviewNowIn())


def test_dispatch_now_leaves_a_pass_the_loop_already_took(ctx):
    session, world, tree = ctx
    source, survey_pass = running_pass(session, world, tree)
    result = feeder.dispatch_now(session, survey_pass, source)
    assert result.dispatched is None
    assert result.waiting == "pass 1 is dispatched, not queued"


def test_the_api_adds_a_source_straight_into_the_queue(client: TestClient, isolated_db):
    with Session(isolated_db) as session:
        build_world(session)
    body = {
        "workspace_slugs": ["loregarden"], "kind": "repo", "url": "git@github.com:acme/newone.git",
        "question": "How do they retry?", "queued_reason": "It ships a retry ledger",
    }  # fmt: skip
    response = client.post("/api/reference-library/sources", json=body)
    assert response.status_code == 200, response.text
    added = response.json()
    assert (added["source"]["url"], added["source"]["state"]) == (
        "https://github.com/acme/newone",
        "queued",
    )
    assert added["source"]["approval_id"] is None
    assert added["attached"] == ["loregarden"]
    bad = client.post("/api/reference-library/sources", json=body | {"url": "not a url"})
    assert bad.status_code == 422
    listing = client.get("/api/reference-library", params={"workspace_slug": "loregarden"}).json()
    assert listing["schedule"]["next"]["name"] == "newone"


def test_the_api_review_now_answers_409_for_a_source_not_admitted(client: TestClient, isolated_db):
    with Session(isolated_db) as session:
        world = build_world(session)
        source_id = _source(
            session, world, "gated", status=SurveySourceStatus.PROPOSED, queued=False
        ).id
    response = client.post(f"/api/reference-library/{source_id}/review-now", json={})
    assert response.status_code == 409
    assert "has not been proposed" in response.json()["detail"]
    assert client.post("/api/reference-library/missing/review-now", json={}).status_code == 404


def test_an_agent_reviews_now_through_the_queue_tool(ctx):
    session, world, _ = ctx
    source = _source(session, world, "orphan")
    _untag_carrier_parent(session, world)
    raw = execute_tool(
        session,
        "loregarden_survey_queue",
        {"action": "review_now", "source_id": source.id},
        orchestrated=True,
        run_id="",
    )
    answer = json.loads(raw)
    assert (answer["started"], answer["ordinal"]) == (False, 1)
    assert "tagged `reference-survey-carrier`" in answer["waiting"]


def test_a_server_with_no_loop_says_so_and_review_now_still_can_start(ctx):
    session, world, _ = ctx
    _source(session, world, "waiting")
    with mock.patch.object(feeder.settings, "reference_survey_interval_seconds", 0):
        schedule = queue_schedule(session)
    assert (schedule.enabled, schedule.disabled_reason) == (
        False,
        "the survey loop is switched off on this server",
    )
    assert (schedule.next.waiting, schedule.next.can_start_now) == (schedule.disabled_reason, True)


def test_a_sandbox_runs_no_loop_and_nothing_starts_there(ctx):
    session, world, _ = ctx
    _source(session, world, "waiting")
    _untag_carrier_parent(session, world)
    with mock.patch.object(feeder.settings, "sandbox", True):
        schedule = queue_schedule(session)
        reviewed = review_now(session, schedule.next.source_id, ReviewNowIn())
    # The bar and the button name the same first reason, though both apply.
    assert schedule.next.waiting == reviewed.waiting == feeder.SANDBOX_WAITING
    assert not schedule.enabled
    assert "sandbox" in schedule.disabled_reason
    assert not schedule.next.can_start_now


def test_an_approved_card_reads_as_queued_without_waiting_for_a_tick(
    client: TestClient, isolated_db
):
    with Session(isolated_db) as session:
        world = build_world(session)
        # An agent's proposal: it waits for its card.
        added = enqueue_source(
            session,
            SourceIn(workspace_slugs=[world.workspace_slug], kind=SurveySourceKind.REPO, url="https://github.com/acme/approved"),
        ).source  # fmt: skip
        ApprovalService(session).resolve(added.approval_id, approved=True)
        added = {"id": added.id}
    detail = client.get(f"/api/reference-library/{added['id']}").json()
    assert detail["source"]["state"] == "queued"
    listing = client.get("/api/reference-library", params={"workspace_slug": "loregarden"}).json()
    assert listing["schedule"]["positions"] == {added["id"]: 1}


def test_a_refused_source_is_reviewed_again_only_with_a_reason_and_keeps_both_on_record(ctx):
    session, world, tree = ctx
    source = _source(session, world, "gpl", status=SurveySourceStatus.REFUSED, queued=False)
    source.refused_reason = "Licence gate: GPL-3.0 excludes it."
    session.add(source)
    session.commit()
    with pytest.raises(SurveyError, match="Say why it should be read after all"):
        review_now(session, source.id, ReviewNowIn())
    clone, lanes, lane = _dispatching(tree)
    with clone, lanes, lane:
        result = review_now(session, source.id, ReviewNowIn(reason="They relicensed to MIT in v2."))
    session.refresh(source)
    assert result.ordinal == 1
    assert result.started  # its licence file now reads MIT, so the gate lets it through
    assert "It had been refused: Licence gate: GPL-3.0 excludes it." in source.notes
    assert "Reason to read it now: They relicensed to MIT in v2." in source.notes
