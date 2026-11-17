"""Near-twin tools and routes, unified on one operation each: where the two had
drifted, the stricter rule holds on every transport, and the rule that differs
by caller (admission, who linked) is decided by the caller's transport."""

from __future__ import annotations

import json

import pytest
from loregarden.mcp.tools import execute_tool
from loregarden.models.domain import WorkItemType
from loregarden.models.domain.survey_tables import SurveySource, SurveySourceInitiative
from loregarden.services.operations import (
    Caller,
    OperationId,
    OperationUnavailable,
    Transport,
    run_operation,
)
from sqlmodel import Session
from tests.factories import make_ticket
from tests.survey_support import build_world


@pytest.fixture(name="ctx")
def ctx_fixture(isolated_db):
    session = Session(isolated_db)
    world = build_world(session)
    initiative = make_ticket(
        session, workspace_id=None, external_id="init-twins-1", title="Follow references",
        work_item_type=WorkItemType.INITIATIVE,
    )  # fmt: skip
    yield session, world, initiative
    session.close()


def test_an_agents_enqueue_follows_its_initiatives_and_still_waits_for_admission(ctx):
    """The page's add followed the initiatives it named; the tool dropped them.
    Both follow them now, and the agent's proposal still waits for a person."""
    session, world, initiative = ctx
    reply = json.loads(
        execute_tool(
            session,
            "loregarden_survey_queue",
            {
                "action": "enqueue",
                "kind": "repo",
                "url": "https://github.com/acme/twin",
                "workspace_slugs": [world.workspace_slug],
                "initiative_ids": [initiative.external_id],
            },
        )
    )

    link = session.get(SurveySourceInitiative, (reply["source_id"], initiative.id))
    assert link is not None
    assert link.linked_by == "agent"
    assert reply["approval_id"] is not None
    assert session.get(SurveySource, reply["source_id"]).approval_id == reply["approval_id"]


def test_the_page_adds_a_source_admitted_and_links_as_the_operator(client, ctx):
    session, world, initiative = ctx
    response = client.post(
        "/api/reference-library/sources",
        json={
            "workspace_slugs": [world.workspace_slug],
            "kind": "repo",
            "url": "https://github.com/acme/page-twin",
            "initiative_ids": [initiative.external_id],
        },
    )
    assert response.status_code == 200, response.text
    added = response.json()

    assert added["source"]["approval_id"] is None
    assert added["initiatives_linked"] == [initiative.external_id]
    link = session.get(SurveySourceInitiative, (added["source"]["id"], initiative.id))
    assert link is not None
    assert link.linked_by == "operator"


@pytest.mark.parametrize(
    ("operation", "arguments"),
    [
        (OperationId.SURVEY_QUEUE, {"action": "list"}),
        (OperationId.SURVEY_QUEUE, {"action": "request_pass", "source_id": "s"}),
        (OperationId.RESEARCH_RECORD, {"action": "question", "thread_id": "t"}),
    ],
)
def test_the_page_is_refused_an_agents_own_actions(db_session, operation, arguments):
    with pytest.raises(OperationUnavailable, match="not available over rest"):
        run_operation(db_session, operation, arguments, Caller(transport=Transport.REST))
