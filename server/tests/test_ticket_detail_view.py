"""The ticket page's context sections, whatever shape a context artifact was written in."""

from __future__ import annotations

import json

import pytest
from loregarden.models.domain import Artifact
from loregarden.services.ticket_detail_view import artifacts_grouped
from tests.factories import make_workspace_ticket

STAGE_REPORT = {"title": "plan", "body": "the plan"}

CASES = {
    "a stage report is one flat section": (STAGE_REPORT, [STAGE_REPORT]),
    "a run context nests its sections": (
        {"sections": [{"title": "a"}, {"title": "b"}]},
        [{"title": "a"}, {"title": "b"}],
    ),
    "a nested entry that is not an object is skipped": (
        {"sections": [{"title": "a"}, "stray", 3]},
        [{"title": "a"}],
    ),
    "sections that are not a list leave the payload as the section": (
        {"sections": "not a list", "title": "odd"},
        [{"sections": "not a list", "title": "odd"}],
    ),
}


@pytest.mark.parametrize(("content", "expected"), CASES.values(), ids=CASES.keys())
def test_context_artifacts_become_the_page_sections(db_session, content, expected):
    ticket = make_workspace_ticket(db_session, "lg-detail-context")
    db_session.add(
        Artifact(ticket_id=ticket.id, kind="context", title="ctx", content_json=json.dumps(content))
    )
    db_session.commit()

    assert artifacts_grouped(db_session, ticket)["context"] == expected
