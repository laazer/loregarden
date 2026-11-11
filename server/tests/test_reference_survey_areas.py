"""Project areas: labelling references with parts of a product, mapping pasted
links onto them, reading for them, and filing what they find under their home."""

from __future__ import annotations

import json
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from loregarden.db.versions.reference_areas import _seed
from loregarden.models.domain import Ticket, WorkItemType, Workspace
from loregarden.models.domain.survey_area_tables import SurveyArea, SurveySourceArea
from loregarden.models.domain.survey_enums import (
    BulkAddOutcome,
    SurveyEpistemic,
    SurveyFindingClaim,
)
from loregarden.models.domain.survey_tables import SurveySource
from loregarden.services.reference_survey.area_suggest import (
    AREA_MAPPER_PROFILE,
    AreaMapperReplyError,
    SuggestIn,
    SuggestMode,
    suggest_areas,
)
from loregarden.services.reference_survey.areas import (
    AreaIn,
    AreaUpdate,
    create_area,
    list_areas,
    source_area_context,
    update_area,
)
from loregarden.services.reference_survey.bulk import enqueue_many
from loregarden.services.reference_survey.errors import SurveyError, SurveyNotFound
from loregarden.services.reference_survey.feeder import _brief
from loregarden.services.reference_survey.library import library_row
from loregarden.services.reference_survey.record import record_evidence, record_finding
from loregarden.services.reference_survey.report import pass_brief
from loregarden.services.reference_survey.schemas import (
    BulkSourceIn,
    EvidenceIn,
    FindingIn,
    TicketWriteIn,
)
from loregarden.services.reference_survey.ticket_writer import write_findings_to_ticket
from loregarden.services.ticket_tags import load_tags
from sqlmodel import Session, select
from tests.factories import make_ticket
from tests.survey_support import build_world, running_pass, second_workspace, write_tree


@pytest.fixture(name="ctx")
def ctx_fixture(isolated_db):
    session = Session(isolated_db)
    world = build_world(session)
    blobert = second_workspace(session, world, "blobert")
    ops = create_area(
        session,
        AreaIn(workspace_slug=world.workspace_slug, slug="capacity-ops", name="Capacity and ops",
               description="Capacity ledger and monitoring.", keywords=["observability", "monitoring"]),
    )  # fmt: skip
    gen = create_area(
        session,
        AreaIn(workspace_slug="blobert", slug="enemy-generator", name="Enemy generator",
               description="Procedural Blender pipeline.", keywords=["blender", "procedural"]),
    )  # fmt: skip
    yield session, world, blobert, ops, gen
    session.close()


# --- the catalog --------------------------------------------------------------


def test_areas_are_listed_by_workspace_and_named_by_label(ctx):
    session, *_ = ctx
    assert [(a.label, a.keywords) for a in list_areas(session)] == [
        ("blobert/enemy-generator", ["blender", "procedural"]),
        ("loregarden/capacity-ops", ["observability", "monitoring"]),
    ]


def test_a_duplicate_slug_and_a_bad_home_are_refused(ctx):
    session, world, *_ = ctx
    with pytest.raises(SurveyError, match="already exists"):
        create_area(
            session, AreaIn(workspace_slug=world.workspace_slug, slug="capacity-ops", name="x")
        )
    with pytest.raises(SurveyNotFound, match="No ticket 'nope-1'"):
        create_area(
            session,
            AreaIn(
                workspace_slug=world.workspace_slug,
                slug="memory",
                name="Memory",
                home_ticket="nope-1",
            ),
        )
    with pytest.raises(ValueError, match="lowercase"):
        AreaIn(workspace_slug=world.workspace_slug, slug="Has Spaces", name="x")


def test_an_archived_area_leaves_the_list_and_cannot_be_picked(ctx):
    session, world, _, ops, _ = ctx
    update_area(session, ops.id, AreaUpdate(archived=True))
    assert [a.label for a in list_areas(session)] == ["blobert/enemy-generator"]
    assert [a.archived for a in list_areas(session, include_archived=True)] == [False, True]
    body = BulkSourceIn(
        workspace_slugs=[world.workspace_slug], urls=["https://github.com/a/b"],
        entries=[{"url": "https://github.com/a/b", "areas": [{"area": "loregarden/capacity-ops"}]}],
    )  # fmt: skip
    with pytest.raises(SurveyError, match="archived"):
        enqueue_many(session, body)


def test_the_migration_seed_fills_known_workspaces_once_and_keeps_edits(ctx, isolated_db):
    session, *_ = ctx
    session.add(Workspace(slug="loremaker", name="loremaker", repo_path="/nowhere"))
    session.commit()
    with isolated_db.begin() as conn:
        _seed(conn)
    labels = [a.label for a in list_areas(session)]
    assert {"loremaker/stratos", "loremaker/xeditor", "loremaker/dexter", "loremaker/ops"} <= set(
        labels
    )
    assert {"blobert/enemy-generator", "blobert/gameplay", "loregarden/orchestration"} <= set(
        labels
    )
    assert not any(label.startswith("lore-eden/") for label in labels), (
        "absent workspace is skipped"
    )
    gen = next(a for a in list_areas(session) if a.label == "blobert/enemy-generator")
    assert gen.description == "Procedural Blender pipeline.", (
        "an area already there is left as edited"
    )
    with isolated_db.begin() as conn:
        _seed(conn)
    assert len(list_areas(session)) == len(labels), "seeding twice adds nothing"


# --- adding references with areas ------------------------------------------------


def test_a_links_areas_are_linked_and_read_it_for_their_workspace(ctx):
    session, world, *_ = ctx
    signoz, kuma = "https://github.com/SigNoz/signoz", "https://github.com/louislam/uptime-kuma"
    body = BulkSourceIn(
        workspace_slugs=[world.workspace_slug], urls=[signoz, kuma],
        entries=[
            {"url": signoz, "areas": [{"area": "loregarden/capacity-ops", "reason": "tracing"},
                                      {"area": "blobert/enemy-generator", "reason": "export timing"}]},
        ],
    )  # fmt: skip
    items = enqueue_many(session, body)
    assert [i.outcome for i in items] == [BulkAddOutcome.QUEUED, BulkAddOutcome.QUEUED]
    assert items[0].source.areas == ["blobert/enemy-generator", "loregarden/capacity-ops"]
    assert items[0].source.workspaces == ["loregarden", "blobert"]
    assert (items[1].source.areas, items[1].source.workspaces) == ([], ["loregarden"])
    reasons = {link.reason for link in session.exec(select(SurveySourceArea)).all()}
    assert reasons == {"tracing", "export timing"}


def test_an_unknown_area_refuses_the_request_before_any_url_is_added(ctx):
    session, world, *_ = ctx
    body = BulkSourceIn(
        workspace_slugs=[world.workspace_slug], urls=["https://github.com/a/one", "https://github.com/a/two"],
        entries=[{"url": "https://github.com/a/two", "areas": [{"area": "loremaker/stratos"}]}],
    )  # fmt: skip
    with pytest.raises(SurveyNotFound, match="loremaker/stratos"):
        enqueue_many(session, body)
    assert session.exec(select(SurveySource)).all() == []


def test_a_known_source_gains_areas_without_a_second_survey(ctx):
    session, world, *_ = ctx
    url = "https://github.com/a/known"
    enqueue_many(session, BulkSourceIn(workspace_slugs=[world.workspace_slug], urls=[url]))
    again = enqueue_many(
        session,
        BulkSourceIn(workspace_slugs=[world.workspace_slug], urls=[url],
                     entries=[{"url": url, "areas": [{"area": "loregarden/capacity-ops"}]}]),
    )  # fmt: skip
    assert again[0].outcome is BulkAddOutcome.ALREADY
    source = session.exec(select(SurveySource)).one()
    assert library_row(session, source).areas == ["loregarden/capacity-ops"]


def test_the_single_add_api_links_areas(client: TestClient):
    # The seeded database was migrated with loregarden in it, so its areas are seeded.
    listed = [a["label"] for a in client.get("/api/reference-areas").json()]
    assert "loregarden/memory" in listed and "loregarden/capacity-ops" in listed
    response = client.post(
        "/api/reference-library/sources",
        json={"workspace_slugs": ["loregarden"], "kind": "repo", "url": "https://github.com/a/mem",
              "areas": [{"area": "loregarden/memory", "reason": "retrieval"}]},
    )  # fmt: skip
    assert response.status_code == 200, response.text
    assert response.json()["source"]["areas"] == ["loregarden/memory"]


# --- best-effort mapping --------------------------------------------------------

_POST = [
    {"url": "https://github.com/SigNoz/signoz", "name": "SigNoz",
     "why": "Open-source Datadog alternative: metrics, distributed tracing and logs."},
    {"url": "https://github.com/woodpecker-ci/woodpecker", "name": "Woodpecker CI",
     "why": "Runs every build step in isolated Docker containers."},
]  # fmt: skip


def test_keyword_mode_matches_whole_words_and_runs_no_agent(ctx):
    session, *_ = ctx
    with mock.patch("loregarden.services.reference_survey.area_suggest.run_cli_agent_turn") as run:
        result = suggest_areas(session, SuggestIn(entries=_POST, mode=SuggestMode.KEYWORDS))
    run.assert_not_called()
    assert result.mode is SuggestMode.KEYWORDS
    # Neither text says "observability" or "blender": keywords alone map nothing,
    # which is exactly the gap the agent mode is for.
    assert [s.areas for s in result.suggestions] == [[], []]
    tracing = SuggestIn(
        entries=[{"url": "https://x.dev", "why": "Self-hosted monitoring"}], mode="keywords"
    )
    (only,) = suggest_areas(session, tracing).suggestions
    assert [(p.area, p.reason) for p in only.areas] == [
        ("loregarden/capacity-ops", "mentions monitoring")
    ]


def _agent_reply(entries) -> str:
    return "```json\n" + json.dumps({"entries": entries}) + "\n```"


def test_the_agent_maps_links_the_text_never_names_and_invented_areas_are_reported(
    ctx, monkeypatch
):
    session, *_ = ctx
    reply = _agent_reply([
        {"url": "https://github.com/SigNoz/signoz",
         "areas": [{"area": "loregarden/capacity-ops", "reason": "traces for the ledger"},
                   {"area": "loremaker/ops", "reason": "made up"}]},
        {"url": "https://github.com/not/pasted", "areas": [{"area": "loregarden/capacity-ops"}]},
    ])  # fmt: skip
    monkeypatch.setenv(AREA_MAPPER_PROFILE.stub_env, reply)
    result = suggest_areas(session, SuggestIn(entries=_POST))
    assert result.mode is SuggestMode.AGENT
    assert [(s.url, [p.area for p in s.areas]) for s in result.suggestions] == [
        ("https://github.com/SigNoz/signoz", ["loregarden/capacity-ops"]),
        ("https://github.com/woodpecker-ci/woodpecker", []),
    ]
    assert result.suggestions[0].areas[0].reason == "traces for the ledger"
    joined = " ".join(result.warnings)
    assert (
        "loremaker/ops" in joined
        and "not/pasted" in joined
        and "said nothing about 1 link" in joined
    )


def test_a_reply_that_is_not_suggestions_is_an_error_not_an_empty_answer(ctx, monkeypatch):
    session, *_ = ctx
    monkeypatch.setenv(AREA_MAPPER_PROFILE.stub_env, "I could not decide.")
    with pytest.raises(AreaMapperReplyError):
        suggest_areas(session, SuggestIn(entries=_POST))


def test_the_suggest_api_answers_502_naming_what_broke(client: TestClient, monkeypatch):
    keywords = client.post(
        "/api/reference-areas/suggest", json={"entries": _POST, "mode": "keywords"}
    )
    assert keywords.status_code == 200, keywords.text
    assert keywords.json()["mode"] == "keywords"
    monkeypatch.setenv(AREA_MAPPER_PROFILE.stub_env, "{}")
    broken = client.post("/api/reference-areas/suggest", json={"entries": _POST})
    assert broken.status_code == 502
    assert "did not match the format" in broken.json()["detail"]


# --- reading for areas, and filing findings under them ---------------------------


@pytest.fixture(name="pass_ctx")
def pass_ctx_fixture(ctx, tmp_path):
    session, world, blobert, ops, gen = ctx
    source, survey_pass = running_pass(
        session, world, write_tree(tmp_path / "clone", {"a.py": "x"})
    )
    ev = record_evidence(
        session,
        EvidenceIn(source_id=source.id, pass_id=survey_pass.id, epistemic=SurveyEpistemic.OBSERVED,
                   operation="read a.py", corpus=["a.py"]),
    )  # fmt: skip
    finding = record_finding(
        session,
        FindingIn(source_id=source.id, pass_id=survey_pass.id, claim=SurveyFindingClaim.ASSERTION,
                  epistemic=SurveyEpistemic.OBSERVED, statement="They sample traces.", evidence_ids=[ev.id]),
    )  # fmt: skip
    yield session, world, source, survey_pass, finding, ops


def _link(session, source, label):
    area = next(a for a in session.exec(select(SurveyArea)).all() if label.endswith(a.slug))
    session.add(SurveySourceArea(source_id=source.id, area_id=area.id, reason="why"))
    session.commit()
    return area


def _create(session, survey_pass, finding, **spec):
    return write_findings_to_ticket(
        session,
        TicketWriteIn(pass_id=survey_pass.id, finding_ids=[finding.id], block="They sample traces.",
                      create={"title": "Trace sampling", "description": "Sample.", "work_item_type": "feature"} | spec),
    )  # fmt: skip


def test_the_brief_names_the_areas_a_pass_reads_for(pass_ctx):
    session, world, source, survey_pass, _, _ = pass_ctx
    _link(session, source, "loregarden/capacity-ops")
    brief = pass_brief(session, survey_pass.id)
    assert [a["label"] for a in brief["areas"]] == ["loregarden/capacity-ops"]
    text = _brief(source, survey_pass, [], None, [world.workspace_slug], [],
                  source_area_context(session, source.id))  # fmt: skip
    assert "**For project areas:** loregarden/capacity-ops (Capacity and ops)" in text


def test_a_parentless_finding_goes_under_its_areas_home_and_is_tagged(pass_ctx):
    session, world, source, survey_pass, finding, ops = pass_ctx
    home = make_ticket(session, workspace_id=world.workspace_id, external_id="lg-ops-m-9",
                       title="Ops", work_item_type=WorkItemType.MILESTONE)  # fmt: skip
    update_area(session, ops.id, AreaUpdate(home_ticket="lg-ops-m-9"))
    _link(session, source, "loregarden/capacity-ops")
    created = _create(session, survey_pass, finding)
    feature = session.get(Ticket, created.ticket.parent_ticket_id)
    assert (feature.title, feature.parent_ticket_id) == ("Adopt from widget", home.id)
    assert "area:loregarden/capacity-ops" in load_tags(created.ticket.tags_json)


def test_with_no_area_home_it_falls_back_to_the_findings_home(pass_ctx):
    session, world, source, survey_pass, finding, _ = pass_ctx
    created = _create(session, survey_pass, finding, area="capacity-ops")
    feature = session.get(Ticket, created.ticket.parent_ticket_id)
    assert session.get(Ticket, feature.parent_ticket_id).title == "Adopt from other projects"
    assert "area:loregarden/capacity-ops" in load_tags(created.ticket.tags_json)


def test_an_area_from_another_workspace_is_refused(pass_ctx):
    session, _, _, survey_pass, finding, _ = pass_ctx
    with pytest.raises(SurveyError, match="not in loregarden"):
        _create(session, survey_pass, finding, area="blobert/enemy-generator")
