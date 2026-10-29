"""Survey exception cards close when what they reported stops being true.

A card that outlives its cause asks a person to fix something already fixed:
"Survey clone failed: aqueli" stayed pending for four days after the source's
next pass cloned and dispatched without trouble.
"""

from __future__ import annotations

import json
from datetime import timedelta
from unittest import mock

import pytest
from loregarden.models.domain import Approval, ApprovalStatus, HumanActionTier, Ticket
from loregarden.models.domain.enums import utcnow
from loregarden.models.domain.survey_enums import (
    SurveyCardKind,
    SurveyPassMode,
    SurveyPassStatus,
    SurveySourceKind,
    SurveySourceStatus,
)
from loregarden.models.domain.survey_tables import SurveyPass, SurveySource
from loregarden.services.approval_views import approval_to_view
from loregarden.services.invariant_registry import SURVEY_CARDS_CLOSE
from loregarden.services.invariants import run_invariant
from loregarden.services.prepared_action import PreparedAction
from loregarden.services.reference_repo_service import ReferenceRepoError
from loregarden.services.reference_survey import feeder
from loregarden.services.reference_survey.inbox import (
    SURVEY_CARD_TOOL,
    SurveyCardKey,
    card_key,
    cleared_cards,
    file_card,
    report_exception,
)
from sqlmodel import Session, select
from tests.survey_support import MIT_TEXT, build_world, serve, write_tree

TREE = {"README.md": "r", "src/a.py": "a", "src/b.py": "b"}


@pytest.fixture(name="ctx")
def ctx_fixture(isolated_db, tmp_path):
    session = Session(isolated_db)
    world = build_world(session)
    tree = write_tree(tmp_path / "clone", TREE)
    (tree / "LICENSE").write_text(MIT_TEXT)
    yield session, world, tree
    session.close()


def _source(session: Session, world, name: str = "widget", owner: str = "acme") -> SurveySource:
    source = SurveySource(
        workspace_id=world.workspace_id, kind=SurveySourceKind.REPO, url=f"https://github.com/{owner}/{name}",
        name=name, question="How do they gate?", queued_reason="r", status=SurveySourceStatus.ACTIVE,
    )  # fmt: skip
    session.add(source)
    session.commit()
    serve(session, source, world.workspace_id)
    return source


def _queue_pass(session: Session, source: SurveySource, ordinal: int) -> SurveyPass:
    survey_pass = SurveyPass(
        source_id=source.id, ordinal=ordinal, mode=SurveyPassMode.INITIAL, question=source.question
    )
    session.add(survey_pass)
    session.commit()
    return survey_pass


def _card(session: Session, world, key: SurveyCardKey, title: str) -> Approval:
    return report_exception(
        session, workspace_id=world.workspace_id, key=key, title=title, detail="d"
    )


def _feed(session: Session, clone):
    with (
        mock.patch.object(feeder, "clone_or_refresh", **clone),
        mock.patch.object(feeder, "QueueLaneService"),
        mock.patch.object(feeder, "least_busy_lane", return_value=1),
    ):
        return feeder.feed_once(session)


def _status(session: Session, card: Approval) -> ApprovalStatus:
    session.expire_all()
    return session.get(Approval, card.id).status


def test_every_success_path_closes_the_cards_it_clears(ctx):
    session, world, tree = ctx
    source = _source(session, world)
    other = _source(session, world, "gadget")
    clone = _card(
        session, world, SurveyCardKey(kind=SurveyCardKind.CLONE_FAILED, source_id=source.id), "c"
    )
    licence = _card(
        session, world, SurveyCardKey(kind=SurveyCardKind.LICENCE_REFUSED, source_id=source.id), "l"
    )
    abandoned = _card(
        session,
        world,
        SurveyCardKey(kind=SurveyCardKind.PASS_ABANDONED, source_id=source.id, occurrence_id="p0"),
        "a",
    )
    no_parent = _card(session, world, SurveyCardKey(kind=SurveyCardKind.NO_CARRIER_PARENT), "n")
    # Not cleared by a dispatch: a person reads these, or they belong elsewhere.
    disagreement = _card(
        session,
        world,
        SurveyCardKey(kind=SurveyCardKind.LICENCE_DISAGREEMENT, source_id=source.id),
        "d",
    )
    other_clone = _card(
        session, world, SurveyCardKey(kind=SurveyCardKind.CLONE_FAILED, source_id=other.id), "o"
    )
    survey_pass = _queue_pass(session, source, 1)

    result = _feed(session, {"return_value": (tree, "main", "c" * 40)})

    assert result.dispatched == survey_pass.id
    for closed in (clone, licence, abandoned, no_parent):
        session.expire_all()
        row = session.get(Approval, closed.id)
        assert row.status is ApprovalStatus.IGNORED, row.title
        assert row.resolved_by == SURVEY_CARD_TOOL
        assert row.resolved_at is not None
        assert json.loads(row.response_json)["ignored_reason"]
    assert _status(session, disagreement) is ApprovalStatus.PENDING
    assert _status(session, other_clone) is ApprovalStatus.PENDING
    assert cleared_cards(session) == []


def test_a_clone_that_succeeds_with_nothing_unread_still_closes_the_clone_card(ctx):
    session, world, tree = ctx
    source = _source(session, world)
    clone = _card(
        session, world, SurveyCardKey(kind=SurveyCardKind.CLONE_FAILED, source_id=source.id), "c"
    )
    survey_pass = _queue_pass(session, source, 1)
    with mock.patch.object(feeder, "assign_slice", return_value=[]):
        result = _feed(session, {"return_value": (tree, "main", "c" * 40)})
    assert result.dispatched is None
    assert session.get(SurveyPass, survey_pass.id).status is SurveyPassStatus.REFUSED
    assert _status(session, clone) is ApprovalStatus.IGNORED


def test_a_failed_clone_files_one_keyed_card_saying_what_happens_next(ctx):
    session, world, _ = ctx
    source = _source(session, world)
    _queue_pass(session, source, 1)
    _feed(session, {"side_effect": ReferenceRepoError("fatal: could not read Username")})
    _queue_pass(session, source, 2)
    _feed(session, {"side_effect": ReferenceRepoError("fatal: again")})

    cards = session.exec(select(Approval).where(Approval.tool_name == SURVEY_CARD_TOOL)).all()
    assert len(cards) == 1
    assert card_key(session, cards[0]) == SurveyCardKey(
        kind=SurveyCardKind.CLONE_FAILED, source_id=source.id
    )
    detail = cards[0].impact
    assert "fatal: could not read Username" in detail
    assert "**What loregarden did:**" in detail
    assert "No other pass is queued" in detail
    assert "**What you can do:**" in detail


def _legacy_card(session: Session, world, title: str, created_at) -> Approval:
    """A card filed before keys: the title is all it carries."""
    card = Approval(
        workspace_id=world.workspace_id, kind="human_action", title=title, impact="d",
        tool_name=SURVEY_CARD_TOOL, status=ApprovalStatus.PENDING, created_at=created_at,
    )  # fmt: skip
    session.add(card)
    session.commit()
    return card


def test_the_invariant_closes_a_legacy_card_whose_source_dispatched_since(ctx):
    session, world, _ = ctx
    source = _source(session, world, "aqueli")
    filed = utcnow() - timedelta(days=4)
    card = _legacy_card(session, world, "Survey clone failed: aqueli", filed)
    survey_pass = _queue_pass(session, source, 2)
    survey_pass.status = SurveyPassStatus.DISPATCHED
    survey_pass.dispatched_at = filed + timedelta(days=1)
    session.add(survey_pass)
    session.commit()

    assert [v.subject_id for v in SURVEY_CARDS_CLOSE.detect(session)] == [card.id]
    run_invariant(session, SURVEY_CARDS_CLOSE)

    session.expire_all()
    row = session.get(Approval, card.id)
    assert row.status is ApprovalStatus.IGNORED
    assert row.resolved_by == SURVEY_CARD_TOOL
    assert "pass 2 on aqueli" in json.loads(row.response_json)["ignored_reason"]
    assert list(SURVEY_CARDS_CLOSE.detect(session)) == []


def test_a_card_with_no_later_success_is_left_for_a_person(ctx):
    session, world, _ = ctx
    source = _source(session, world)
    earlier = _queue_pass(session, source, 1)
    earlier.status = SurveyPassStatus.FINISHED
    earlier.dispatched_at = utcnow() - timedelta(days=1)
    session.add(earlier)
    session.commit()
    _card(session, world, SurveyCardKey(kind=SurveyCardKind.CLONE_FAILED, source_id=source.id), "c")
    assert list(SURVEY_CARDS_CLOSE.detect(session)) == []


def test_a_legacy_title_naming_two_sources_stays_unkeyed(ctx):
    session, world, _ = ctx
    _source(session, world, "twin")
    _source(session, world, "twin", owner="fork")
    card = _legacy_card(session, world, "Survey clone failed: twin", utcnow())
    assert card_key(session, card) is None


def test_a_card_from_another_producer_is_not_the_surveys(ctx):
    session, world, _ = ctx
    ticket = session.get(Ticket, world.feature_id)
    card = Approval(
        ticket_id=ticket.id, workspace_id=world.workspace_id, kind="human_action",
        title="Survey clone failed: widget", tool_name="", status=ApprovalStatus.PENDING,
    )  # fmt: skip
    session.add(card)
    session.commit()
    assert card_key(session, card) is None


def test_the_inbox_view_offers_what_each_card_can_actually_do(ctx):
    session, world, _ = ctx
    source = _source(session, world)
    _queue_pass(session, source, 1)
    clone = _card(
        session, world, SurveyCardKey(kind=SurveyCardKind.CLONE_FAILED, source_id=source.id), "c"
    )
    licence = _card(
        session, world, SurveyCardKey(kind=SurveyCardKind.LICENCE_REFUSED, source_id=source.id), "l"
    )
    admission = file_card(
        session, workspace_id=world.workspace_id, title="Admit", impact="i", level="medium",
        key=SurveyCardKey(kind=SurveyCardKind.ADMISSION, source_id=source.id),
    )  # fmt: skip
    ticket = session.get(Ticket, world.feature_id)
    monitor = Approval(
        ticket_id=ticket.id, workspace_id=world.workspace_id, kind="human_action",
        title="Workflow monitor could not repair", status=ApprovalStatus.PENDING,
    )  # fmt: skip
    prepared = Approval(
        ticket_id=ticket.id, workspace_id=world.workspace_id, kind="human_action", title="Run it",
        tool_input_json=PreparedAction(tier=HumanActionTier.MANUAL).model_dump_json(),
        status=ApprovalStatus.PENDING,
    )  # fmt: skip
    session.add_all([monitor, prepared])
    session.commit()

    views = {
        card.id: approval_to_view(session, card)
        for card in (clone, licence, admission, monitor, prepared)
    }

    assert views[clone.id]["close_only"] is True
    assert views[clone.id]["survey_card"] == {
        "kind": "clone_failed", "source_id": source.id, "source_name": "widget",
        "retryable": True, "retry_held": "",
    }  # fmt: skip
    assert views[licence.id]["close_only"] is True
    assert views[licence.id]["survey_card"]["retryable"] is False
    assert views[admission.id]["close_only"] is False
    assert views[monitor.id]["close_only"] is True
    assert views[monitor.id]["survey_card"] is None
    assert views[prepared.id]["close_only"] is False


def test_retry_is_held_while_a_pass_is_reading_the_source(ctx):
    session, world, _ = ctx
    source = _source(session, world)
    reading = _queue_pass(session, source, 1)
    reading.status = SurveyPassStatus.DISPATCHED
    session.add(reading)
    session.commit()
    card = _card(
        session,
        world,
        SurveyCardKey(kind=SurveyCardKind.PASS_ABANDONED, source_id=source.id, occurrence_id="p0"),
        "a",
    )
    view = approval_to_view(session, card)["survey_card"]
    assert view["retryable"] is True
    assert view["retry_held"] == "Pass 1 is reading widget now."
