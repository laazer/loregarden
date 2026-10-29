"""Choosing the workspaces a review reads for and the initiatives it feeds: when a
reference is added, when one is reviewed now, and on the source afterwards."""

from __future__ import annotations

from unittest import mock

import pytest
from fastapi.testclient import TestClient
from loregarden.models.domain import Approval, ApprovalStatus, Ticket, WorkItemType
from loregarden.models.domain.survey_enums import (
    BulkAddOutcome,
    SurveyPassMode,
    SurveySourceKind,
    SurveySourceStatus,
)
from loregarden.models.domain.survey_tables import SurveyPass, SurveySource
from loregarden.services.reference_survey import feeder
from loregarden.services.reference_survey.bulk import enqueue_many
from loregarden.services.reference_survey.errors import SurveyError, SurveyNotFound
from loregarden.services.reference_survey.initiatives import linked_initiatives
from loregarden.services.reference_survey.queue import enqueue_source, target_workspaces
from loregarden.services.reference_survey.report import pass_brief
from loregarden.services.reference_survey.schedule import ReviewNowIn, review_now
from loregarden.services.reference_survey.schemas import BulkSourceIn, SourceIn
from sqlmodel import Session, select
from tests.factories import make_ticket
from tests.survey_support import MIT_TEXT, build_world, running_pass, second_workspace, write_tree


@pytest.fixture(name="ctx")
def ctx_fixture(isolated_db, tmp_path):
    session = Session(isolated_db)
    world = build_world(session)
    other = second_workspace(session, world)
    initiative = make_ticket(
        session, workspace_id=None, external_id="init-survey-1", title="Make surveys pay off",
        work_item_type=WorkItemType.INITIATIVE,
    )  # fmt: skip
    tree = write_tree(tmp_path / "clone", {"LICENSE": MIT_TEXT, "src/a.py": "a"})
    yield session, world, other, initiative, tree
    session.close()


def _served(session, source_id):
    return [w.slug for w, _ in target_workspaces(session, source_id)]


def _surveyed(session, world, tree):
    source, first = running_pass(session, world, tree)
    first.status, source.status = first.status.FINISHED, SurveySourceStatus.DONE
    session.add_all([first, source])
    session.commit()
    return source


def test_a_bulk_add_serves_every_workspace_picked_and_links_every_initiative(ctx):
    session, world, other, initiative, _ = ctx
    body = BulkSourceIn(
        workspace_slugs=[world.workspace_slug, other.slug], urls=["https://github.com/acme/a", "https://github.com/acme/b"],
        question="q", queued_reason="r", initiative_ids=["init-survey-1"],
    )  # fmt: skip
    items = enqueue_many(session, body)
    assert [i.outcome for i in items] == [BulkAddOutcome.QUEUED] * 2
    for item in items:
        assert _served(session, item.source.id) == [world.workspace_slug, other.slug]
        assert item.initiatives_linked == ["init-survey-1"]
        assert [i.external_id for i in linked_initiatives(session, item.source.id)] == [
            "init-survey-1"
        ]


def test_an_unknown_initiative_refuses_a_bulk_add_before_any_url_is_added(ctx):
    session, world, _other, _initiative, _ = ctx
    body = BulkSourceIn(
        workspace_slugs=[world.workspace_slug], urls=["https://github.com/acme/a"], question="q",
        queued_reason="r", initiative_ids=["init-missing-9"],
    )  # fmt: skip
    with pytest.raises(SurveyNotFound, match="No initiative 'init-missing-9'"):
        enqueue_many(session, body)
    assert session.exec(select(SurveySource)).all() == []


def test_review_now_reads_for_the_workspaces_added_and_links_the_initiatives(ctx):
    session, world, other, _initiative, tree = ctx
    source = _surveyed(session, world, tree)
    with (
        mock.patch.object(feeder, "clone_or_refresh", return_value=(tree, "main", "c" * 40)),
        mock.patch.object(feeder, "QueueLaneService"),
        mock.patch.object(feeder, "least_busy_lane", return_value=1),
    ):
        result = review_now(
            session, source.id,
            ReviewNowIn(question="What changed?", workspace_slugs=[other.slug], initiative_ids=["init-survey-1"]),
        )  # fmt: skip
    assert result.started
    assert (result.workspaces_added, result.initiatives_linked) == ([other.slug], ["init-survey-1"])
    assert _served(session, source.id) == [world.workspace_slug, other.slug]
    # One pass for the review, not a second one for the workspace it gained.
    passes = session.exec(select(SurveyPass).where(SurveyPass.source_id == source.id, SurveyPass.ordinal > 1)).all()  # fmt: skip
    assert [(p.mode, p.question) for p in passes] == [(SurveyPassMode.SCOPED, "What changed?")]


def test_review_now_on_a_source_it_cannot_run_writes_nothing(ctx):
    session, world, other, _initiative, tree = ctx
    source, _reading = running_pass(session, world, tree)
    with pytest.raises(SurveyError, match="is reading"):
        review_now(
            session, source.id, ReviewNowIn(workspace_slugs=[other.slug], initiative_ids=["init-survey-1"])
        )  # fmt: skip
    assert _served(session, source.id) == [world.workspace_slug]
    assert linked_initiatives(session, source.id) == []


def test_review_now_refuses_an_unknown_workspace_before_writing(ctx):
    session, world, _other, _initiative, tree = ctx
    source = _surveyed(session, world, tree)
    with pytest.raises(SurveyNotFound, match="No workspace 'nowhere'"):
        review_now(session, source.id, ReviewNowIn(workspace_slugs=["nowhere"]))
    assert (
        session.exec(select(SurveyPass).where(SurveyPass.source_id == source.id)).all()[-1].ordinal
        == 1
    )


def test_the_api_adds_one_reference_for_two_workspaces_and_an_initiative(
    client: TestClient, isolated_db
):
    with Session(isolated_db) as session:
        world = build_world(session)
        other = second_workspace(session, world).slug
        make_ticket(
            session, workspace_id=None, external_id="init-survey-1", title="i",
            work_item_type=WorkItemType.INITIATIVE,
        )  # fmt: skip
    body = {"workspace_slugs": ["loregarden", other], "kind": "repo", "url": "https://github.com/acme/x",
            "question": "q", "queued_reason": "r", "initiative_ids": ["init-survey-1"]}  # fmt: skip
    added = client.post("/api/reference-library/sources", json=body).json()
    assert added["initiatives_linked"] == ["init-survey-1"]
    assert sorted(added["source"]["workspaces"]) == sorted(["loregarden", other])
    bad = client.post(
        "/api/reference-library/sources",
        json=body | {"url": "https://github.com/acme/y", "initiative_ids": ["init-x"]},
    )
    assert bad.status_code == 404
    assert (
        client.get("/api/reference-library", params={"workspace_slug": "loregarden"}).json()[
            "counts"
        ]["queued"]
        == 1
    )


def test_the_source_page_links_workspaces_and_initiatives_together(
    client: TestClient, isolated_db, tmp_path
):
    with Session(isolated_db) as session:
        world = build_world(session)
        other = second_workspace(session, world).slug
        third = second_workspace(session, world, "lore-eden").slug
        for ext in ("init-survey-1", "init-survey-2"):
            make_ticket(
                session, workspace_id=None, external_id=ext, title=ext,
                work_item_type=WorkItemType.INITIATIVE,
            )  # fmt: skip
        source_id = _surveyed(session, world, write_tree(tmp_path / "t", {"a.py": "a"})).id
    url = f"/api/reference-library/{source_id}/links"
    response = client.post(
        url,
        json={
            "workspace_slugs": [other, third],
            "initiative_ids": ["init-survey-1", "init-survey-2"],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["attached"], body["scoped_pass"]) == ([other, third], 2)
    assert [link["initiative"] for link in body["initiatives"]] == [
        "init-survey-1",
        "init-survey-2",
    ]
    # Initiatives alone are a link too; no pass is queued for them.
    with Session(isolated_db) as session:
        make_ticket(
            session, workspace_id=None, external_id="init-survey-3", title="i3",
            work_item_type=WorkItemType.INITIATIVE,
        )  # fmt: skip
    only = client.post(url, json={"initiative_ids": ["init-survey-3"]}).json()
    assert (only["attached"], only["scoped_pass"]) == ([], None)
    assert client.post(url, json={}).status_code == 422
    assert client.post(url, json={"workspace_slugs": [other]}).status_code == 422  # already serves
    assert (
        client.post(
            "/api/reference-library/missing/links", json={"initiative_ids": ["x"]}
        ).status_code
        == 404
    )


def test_an_unknown_initiative_links_nothing_on_the_source_page(
    client: TestClient, isolated_db, tmp_path
):
    with Session(isolated_db) as session:
        world = build_world(session)
        other = second_workspace(session, world).slug
        source_id = _surveyed(session, world, write_tree(tmp_path / "t", {"a.py": "a"})).id
    response = client.post(
        f"/api/reference-library/{source_id}/links",
        json={"workspace_slugs": [other], "initiative_ids": ["init-nope-9"]},
    )
    assert response.status_code == 404
    with Session(isolated_db) as session:
        assert _served(session, source_id) == [world.workspace_slug]


def test_no_question_or_reason_is_needed_and_an_agents_card_says_none_was_given(ctx):
    session, world, _other, _initiative, _ = ctx
    item = enqueue_many(
        session,
        BulkSourceIn(workspace_slugs=[world.workspace_slug], urls=["https://github.com/acme/q"]),
    )[0]
    assert item.outcome is BulkAddOutcome.QUEUED
    # An agent's enqueue (not admitted) still files a card, and it says what was left out.
    proposed = enqueue_source(
        session,
        SourceIn(workspace_slugs=[world.workspace_slug], kind=SurveySourceKind.REPO, url="https://github.com/acme/agent"),
    ).source  # fmt: skip
    card = session.get(Approval, proposed.approval_id)
    assert "Question: (none given; it reads for the workspaces it serves)" in card.impact


def test_a_review_with_no_question_reads_for_its_workspaces_and_initiatives(ctx):
    session, world, other, initiative, tree = ctx
    initiative.description = "Make every survey finding land on a ticket someone will act on."
    session.add(initiative)
    session.commit()
    source = _surveyed(session, world, tree)
    source.question = ""
    session.add(source)
    session.commit()
    lanes = mock.MagicMock()
    with (
        mock.patch.object(feeder, "clone_or_refresh", return_value=(tree, "main", "c" * 40)),
        mock.patch.object(feeder, "QueueLaneService", return_value=lanes),
        mock.patch.object(feeder, "least_busy_lane", return_value=1),
    ):
        result = review_now(
            session, source.id, ReviewNowIn(workspace_slugs=[other.slug], initiative_ids=["init-survey-1"])
        )  # fmt: skip
    assert result.started
    survey_pass = session.exec(select(SurveyPass).where(SurveyPass.source_id == source.id, SurveyPass.ordinal == 2)).one()  # fmt: skip
    assert survey_pass.question == ""
    expected = (
        f"No question was given: read for what would serve the backlogs of {world.workspace_slug}, "
        f"{other.slug} and the goals of init-survey-1 (Make surveys pay off)."
    )
    brief = pass_brief(session, survey_pass.id)
    assert brief["pass"]["question"] == expected
    assert brief["initiatives"] == [
        {"external_id": "init-survey-1", "title": "Make surveys pay off",
         "goal": "Make every survey finding land on a ticket someone will act on."}
    ]  # fmt: skip
    carrier = session.get(Ticket, survey_pass.carrier_ticket_id)
    assert f"**Question:** {expected}" in carrier.description
    assert "**For initiatives:** init-survey-1 (Make surveys pay off)" in carrier.description
    assert carrier.title.endswith("open read for what it serves")


def test_adding_a_source_still_waiting_on_its_card_queues_it_and_closes_the_card(ctx):
    session, world, _other, _initiative, _ = ctx
    body = SourceIn(
        workspace_slugs=[world.workspace_slug], kind=SurveySourceKind.REPO, url="https://github.com/acme/waiting"
    )  # fmt: skip
    waiting = enqueue_source(session, body).source  # an agent's proposal
    card_id = waiting.approval_id
    added = enqueue_source(session, body, admitted=True)
    assert added.admitted
    assert added.source.status is SurveySourceStatus.ACTIVE
    assert session.get(Approval, card_id).status is ApprovalStatus.APPROVED
    initial = session.exec(select(SurveyPass).where(SurveyPass.source_id == waiting.id)).all()
    assert [(p.ordinal, p.mode) for p in initial] == [(1, SurveyPassMode.INITIAL)]
