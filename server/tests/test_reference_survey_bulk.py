"""Proposing several references at once: each on its own, each reported."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from loregarden.models.domain import Approval
from loregarden.models.domain.survey_enums import (
    BulkAddOutcome,
    SurveySourceKind,
    SurveySourceStatus,
)
from loregarden.models.domain.survey_tables import SurveySource
from loregarden.services.reference_survey.bulk import enqueue_many, infer_kind
from loregarden.services.reference_survey.schemas import MAX_BULK_URLS, BulkSourceIn
from pydantic import ValidationError
from sqlmodel import Session, select
from tests.survey_support import build_world, second_workspace


@pytest.fixture(name="ctx")
def ctx_fixture(isolated_db):
    session = Session(isolated_db)
    world = build_world(session)
    yield session, world
    session.close()


def _bulk(world, urls, **kw):
    return BulkSourceIn(
        **({"workspace_slugs": [world.workspace_slug], "urls": urls, "question": "How do they retry?",
            "queued_reason": "Retry ledgers"} | kw)
    )  # fmt: skip


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ("https://github.com/acme/widget", SurveySourceKind.REPO),
        ("git@gitlab.example.com:team/tool.git", SurveySourceKind.REPO),
        ("https://codeberg.org/a/b.git", SurveySourceKind.REPO),
        ("https://docs.example.dev/guide/retries", SurveySourceKind.SITE),
        ("https://example.dev", SurveySourceKind.SITE),
    ],
)
def test_a_urls_kind_is_told_from_its_host(url, kind):
    assert infer_kind(url) is kind


def test_each_url_joins_the_queue_on_its_own_and_is_reported(ctx):
    session, world = ctx
    urls = [
        "https://github.com/acme/one",
        "https://docs.example.dev/retries",
        "not a url",
        "https://github.com/acme/one",
    ]
    items = enqueue_many(session, _bulk(world, urls))
    assert [(i.url, i.outcome, i.kind) for i in items] == [
        ("https://github.com/acme/one", BulkAddOutcome.QUEUED, SurveySourceKind.REPO),
        ("https://docs.example.dev/retries", BulkAddOutcome.QUEUED, SurveySourceKind.SITE),
        ("not a url", BulkAddOutcome.FAILED, SurveySourceKind.SITE),
    ]
    assert "https URL" in items[2].detail
    assert items[2].source is None
    sources = session.exec(select(SurveySource)).all()
    assert {s.url for s in sources} == {
        "https://github.com/acme/one",
        "https://docs.example.dev/retries",
    }
    # A person added them: no admission card, and each has its first pass queued.
    cards = session.exec(
        select(Approval).where(Approval.title.startswith("Admit reference source"))
    ).all()  # type: ignore[union-attr]
    assert cards == []
    assert all(s.status is SurveySourceStatus.ACTIVE for s in sources)
    assert [i.source.state for i in items[:2]] == ["queued", "queued"]


def test_a_failure_in_the_middle_leaves_the_rest_added(ctx):
    session, world = ctx
    refused = SurveySource(
        workspace_id=world.workspace_id, kind=SurveySourceKind.REPO, url="https://github.com/acme/refused",
        name="refused", status=SurveySourceStatus.REFUSED, refused_reason="GPL",
    )  # fmt: skip
    session.add(refused)
    session.commit()
    urls = [
        "https://github.com/acme/a",
        "https://github.com/acme/refused",
        "https://github.com/acme/b",
    ]
    items = enqueue_many(session, _bulk(world, urls))
    assert [i.outcome for i in items] == [
        BulkAddOutcome.QUEUED,
        BulkAddOutcome.FAILED,
        BulkAddOutcome.QUEUED,
    ]
    assert "was refused (GPL)" in items[1].detail


def test_a_known_source_gains_the_workspace_instead(ctx):
    session, world = ctx
    enqueue_many(session, _bulk(world, ["https://github.com/acme/shared"]))
    other = second_workspace(session, world)
    items = enqueue_many(
        session, _bulk(world, ["https://github.com/acme/shared"], workspace_slugs=[other.slug])
    )
    assert items[0].outcome is BulkAddOutcome.ATTACHED
    assert set(items[0].source.workspaces) == {world.workspace_slug, other.slug}


def test_a_url_already_serving_the_workspace_is_reported_as_known_not_failed(ctx):
    session, world = ctx
    first = enqueue_many(session, _bulk(world, ["https://github.com/acme/known"]))[0]
    again = enqueue_many(session, _bulk(world, ["git@github.com:acme/known.git"]))[0]
    assert (again.outcome, again.detail) == (BulkAddOutcome.ALREADY, "")
    assert again.source.id == first.source.id


def test_one_chosen_kind_applies_to_every_url(ctx):
    session, world = ctx
    items = enqueue_many(
        session, _bulk(world, ["https://example.dev/app"], kind=SurveySourceKind.RELEASED_APP)
    )
    assert items[0].kind is SurveySourceKind.RELEASED_APP


def test_a_bulk_add_is_bounded():
    with pytest.raises(ValidationError):
        BulkSourceIn(
            workspace_slugs=["loregarden"], urls=[f"https://github.com/a/r{i}" for i in range(MAX_BULK_URLS + 1)],
            question="q", queued_reason="r",
        )  # fmt: skip


def test_the_api_answers_200_with_a_row_per_url(client: TestClient, isolated_db):
    with Session(isolated_db) as session:
        build_world(session)
    body = {"workspace_slugs": ["loregarden"], "urls": ["https://github.com/acme/x", "ftp://nope"],
            "question": "q", "queued_reason": "r"}  # fmt: skip
    response = client.post("/api/reference-library/sources/bulk", json=body)
    assert response.status_code == 200, response.text
    assert [row["outcome"] for row in response.json()] == ["queued", "failed"]
    assert response.json()[0]["source"]["state"] == "queued"
