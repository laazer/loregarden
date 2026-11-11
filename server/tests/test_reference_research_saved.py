"""Saved research questions: one turn is one run, what a run changed, saving, re-running, the schedule."""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from loregarden.models.domain import BaxterChatMessage, BaxterChatSession, Workspace
from loregarden.models.domain.chat_enums import ChatThreadMode
from loregarden.models.domain.enums import utcnow
from loregarden.models.domain.survey_area_tables import SurveyArea
from loregarden.models.domain.survey_enums import ClaimStatus
from loregarden.models.domain.survey_research_tables import SurveyResearchQuestion
from loregarden.services.baxter_chat_service import BaxterChatConflictError
from loregarden.services.reference_survey import research, research_saved, research_scheduler
from loregarden.services.reference_survey.errors import SurveyError, SurveyNotFound
from loregarden.services.reference_survey.research import (
    CandidateIn,
    ClaimIn,
    QuestionIn,
    open_question,
    record_candidates,
)
from loregarden.services.reference_survey.research_context import workspace_questions
from loregarden.services.reference_survey.research_diff import run_diff
from loregarden.services.reference_survey.research_saved import (
    SaveQuestionIn,
    rerun_question,
    save_question,
)
from sqlmodel import Session, select
from tests.survey_support import build_world

RIGGER = "https://github.com/acme/rigger"
OTHER = "https://github.com/acme/other"
NEWCOMER = "https://github.com/acme/newcomer"
SKELETON = "generates a skeleton for an unrigged mesh"
SKINNING = "computes skin weights"


def _record(session: Session, question_id: str, candidates: list[CandidateIn]) -> int:
    with mock.patch.object(
        research, "gh_api_json", side_effect=research.UpstreamUnavailable("offline")
    ):
        return record_candidates(session, question_id, candidates).run


def _turn(session: Session, thread_id: str) -> BaxterChatMessage:
    turn = BaxterChatMessage(session_id=thread_id, role="assistant", content="", status="pending")
    session.add(turn)
    session.commit()
    return turn


def _finish(session: Session, turn: BaxterChatMessage) -> None:
    turn.status = "complete"
    session.add(turn)
    session.commit()


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
    yield session, world, thread, question
    session.close()


def _first_run(session: Session, question_id: str) -> None:
    _record(session, question_id, [
        CandidateIn(url=RIGGER, name="Rigger", claims=[
            ClaimIn(capability=SKELETON, status=ClaimStatus.DOCS_ONLY, note="README says so"),
        ]),
        CandidateIn(url=OTHER, name="Other"),
    ])  # fmt: skip


def test_one_turn_is_one_run_however_many_record_calls_it_makes(ctx):
    session, _, thread, question = ctx
    turn = _turn(session, thread.id)
    assert _record(session, question.id, [CandidateIn(url=RIGGER, name="Rigger")]) == 1
    assert _record(session, question.id, [CandidateIn(url=OTHER, name="Other")]) == 1
    _finish(session, turn)
    second = _turn(session, thread.id)
    assert _record(session, question.id, [CandidateIn(url=RIGGER, name="Rigger")]) == 2
    # Outside a turn (the CLI, a test), every call is a run of its own.
    _finish(session, second)
    assert _record(session, question.id, [CandidateIn(url=RIGGER, name="Rigger")]) == 3
    assert _record(session, question.id, [CandidateIn(url=RIGGER, name="Rigger")]) == 4


def test_a_run_reports_what_it_added_dropped_and_changed(ctx):
    session, _, _, question = ctx
    _first_run(session, question.id)
    with pytest.raises(SurveyError, match="Run 3 does not exist"):
        run_diff(session, question.id, 3)
    _record(session, question.id, [
        CandidateIn(url=RIGGER, name="Rigger", claims=[
            ClaimIn(capability=SKELETON, status=ClaimStatus.NOT_FOUND, note="no such code"),
            ClaimIn(capability=SKINNING, status=ClaimStatus.DOCS_ONLY),
        ]),
        CandidateIn(url=NEWCOMER, name="Newcomer"),
    ])  # fmt: skip

    diff = run_diff(session, question.id)

    assert (diff.run, diff.previous_run, diff.dropped_known) == (2, 1, True)
    assert [c.url for c in diff.added] == [NEWCOMER]
    assert [c.url for c in diff.dropped] == [OTHER]
    changes = {(c.capability, c.before.status if c.before else None, c.after.status)
               for c in diff.changed}  # fmt: skip
    assert changes == {
        (SKELETON, ClaimStatus.DOCS_ONLY, ClaimStatus.NOT_FOUND),
        (SKINNING, None, ClaimStatus.DOCS_ONLY),
    }
    assert diff.unchanged == 0
    first = run_diff(session, question.id, 1)
    assert ({c.url for c in first.added}, first.dropped_known) == ({RIGGER, OTHER}, False)
    # Dropped means gone since the run before, not gone at some point.
    _record(session, question.id, [CandidateIn(url=RIGGER, name="Rigger")])
    assert [c.url for c in run_diff(session, question.id).dropped] == [NEWCOMER]


def test_a_claim_rechecked_the_same_counts_as_unchanged(ctx):
    session, _, _, question = ctx
    _first_run(session, question.id)
    _record(session, question.id, [
        CandidateIn(url=RIGGER, name="Rigger", claims=[
            ClaimIn(capability=SKELETON, status=ClaimStatus.DOCS_ONLY, note="still README"),
        ]),
        CandidateIn(url=OTHER, name="Other"),
    ])  # fmt: skip
    diff = run_diff(session, question.id)
    assert (diff.added, diff.dropped, diff.changed, diff.unchanged) == ([], [], [], 1)


def test_an_unrun_question_has_no_diff(ctx):
    session, _, _, question = ctx
    with pytest.raises(SurveyError, match="not been run"):
        run_diff(session, question.id)
    with pytest.raises(SurveyNotFound):
        run_diff(session, "nope")


def test_saving_pins_an_area_and_lists_the_question_first(ctx):
    session, world, thread, question = ctx
    area = SurveyArea(workspace_id=world.workspace_id, slug="rigging", name="Rigging")
    other_ws = Workspace(slug="elsewhere", name="Elsewhere", repo_path="/tmp/elsewhere")
    session.add_all([area, other_ws])
    session.commit()
    foreign = SurveyArea(workspace_id=other_ws.id, slug="rigging", name="Rigging")
    session.add(foreign)
    session.commit()
    newer = open_question(session, QuestionIn(thread_id=thread.id, question="Which repos retarget?",
                                              capabilities=[SKINNING]))  # fmt: skip
    _record(session, newer.id, [CandidateIn(url=OTHER, name="Other")])

    view = save_question(session, question.id, SaveQuestionIn(area_id=area.id, interval_days=7))

    assert (view.saved, view.area_id, view.interval_days) == (True, area.id, 7)
    listed = workspace_questions(session, world.workspace_id)
    assert [(q.id, q.saved, q.area_name, q.interval_days) for q in listed] == [
        (question.id, True, "Rigging", 7), (newer.id, False, "", None),
    ]  # fmt: skip
    with pytest.raises(SurveyError, match="project areas"):
        save_question(session, question.id, SaveQuestionIn(area_id=foreign.id))
    unsaved = save_question(session, question.id, SaveQuestionIn(saved=False, area_id=area.id,
                                                                 interval_days=7))  # fmt: skip
    assert (unsaved.saved, unsaved.area_id, unsaved.interval_days) == (False, None, None)


def test_a_rerun_posts_the_previous_run_to_the_thread_and_refuses_while_it_is_busy(ctx):
    session, _, thread, question = ctx
    _first_run(session, question.id)
    with mock.patch.object(research_saved, "schedule_baxter_chat_turn") as schedule:
        started = rerun_question(session, question.id)
        schedule.assert_called_once_with(started.turn_id)
        with pytest.raises(BaxterChatConflictError):
            rerun_question(session, question.id)
    prompt = session.exec(
        select(BaxterChatMessage).where(
            BaxterChatMessage.session_id == thread.id, BaxterChatMessage.role == "user"
        )
    ).one()
    assert "Re-run of research question" in prompt.content
    assert f"Rigger {RIGGER} — {SKELETON}: docs_only; {SKINNING}: not checked" in prompt.content
    assert f"research_diff card for question_id={question.id}" in prompt.content
    # No fence: it would render as a card inside the operator-visible message.
    assert "```" not in prompt.content
    refreshed = session.get(SurveyResearchQuestion, question.id)
    assert refreshed.last_scheduled_at is not None


def test_a_rerun_needs_its_research_thread(ctx):
    session, _, thread, question = ctx
    thread.mode = ChatThreadMode.GENERAL
    session.add(thread)
    session.commit()
    with pytest.raises(SurveyError, match="no longer a research thread"):
        rerun_question(session, question.id)


def test_the_schedule_reruns_due_questions_skips_busy_threads_and_records_failures(ctx):
    session, _, thread, question = ctx
    now = utcnow()
    question.saved = True
    question.interval_days = 7
    question.last_run_at = now - timedelta(days=8)
    session.add(question)
    session.commit()
    assert research_scheduler.is_due(question, now)
    assert not research_scheduler.is_due(question, now - timedelta(days=2))
    ad_hoc = open_question(session, QuestionIn(thread_id=thread.id, question="Which repos retarget?",
                                               capabilities=[SKINNING]))  # fmt: skip
    assert not research_scheduler.is_due(ad_hoc, now)

    with mock.patch.object(research_saved, "schedule_baxter_chat_turn"):
        assert research_scheduler.run_due_questions(session, now) == {question.id: ""}
        # Asked just now: not due again until the interval passes, though nothing was recorded.
        assert research_scheduler.run_due_questions(session, now + timedelta(days=1)) == {}
        later = now + timedelta(days=8)
        # The first re-run's turn is still pending: busy, so skipped, not failed.
        outcome = research_scheduler.run_due_questions(session, later)
    assert outcome == {question.id: research_scheduler.IN_FLIGHT}
    assert session.get(SurveyResearchQuestion, question.id).schedule_error == ""

    for message in session.exec(select(BaxterChatMessage)).all():
        message.status = "complete"
    session.commit()
    with mock.patch.object(
        research_scheduler, "rerun_question", side_effect=RuntimeError("adapter gone")
    ):
        failed = research_scheduler.run_due_questions(session, later)
        assert failed == {question.id: "adapter gone"}
        assert research_scheduler.run_due_questions(session, later + timedelta(hours=1)) == {}
    assert session.get(SurveyResearchQuestion, question.id).schedule_error == "adapter gone"


def test_the_api_saves_reruns_and_diffs(client: TestClient):
    missing = "/api/reference-research/questions/nope"
    assert client.post(f"{missing}/save", json={"interval_days": 7}).status_code == 404
    assert client.post(f"{missing}/rerun").status_code == 404
    assert client.get(f"{missing}/diff").status_code == 404
    assert client.post(f"{missing}/save", json={"interval_days": 0}).status_code == 422
