"""Listing tickets must not re-resolve one template per row, and can be paged.

`_ticket_summary` resolves the ticket's workflow stages, and
`resolve_ticket_stages` parses the template's `stages_json`, validates every
stage, and reads the workspace override **off disk**. All three are identical
for every ticket sharing a template, so `GET /api/tickets` paid them per row:
836 tickets in the live database meant 836 reads of the same YAML file.

Like `test_ticket_list_commits`, these count work rather than asserting on a
duration — a file-read count is deterministic and a duration is a property of
the machine.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest
from loregarden.models.domain import Ticket
from sqlmodel import Session, select


@pytest.fixture(name="seeded")
def seeded_fixture(isolated_db):
    """The seed tickets, which share one workspace and one template."""
    with Session(isolated_db) as session:
        tickets = list(session.exec(select(Ticket)).all())
    assert len(tickets) >= 3, "seed data no longer has enough tickets to measure"
    return tickets


def test_listing_reads_the_workspace_override_once(client, seeded):
    """The disk read is per template, not per ticket.

    Measured on this suite's seed data: before the memo, one read per row.
    """
    import loregarden.services.workflow_service as workflow_service

    real = workflow_service.load_workspace_override
    with mock.patch.object(workflow_service, "load_workspace_override", side_effect=real) as reads:
        response = client.get("/api/tickets?workspace=loregarden")

    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) >= 3

    # Not "fewer than rows" — the seed shares a single template, so one read
    # answers every ticket in the page.
    assert reads.call_count == 1, f"{reads.call_count} override reads for {len(rows)} tickets"


def test_paged_listing_matches_the_unpaged_order(client, seeded):
    """`limit`/`offset` slice the same ordered set the unpaged call returns."""
    everything = client.get("/api/tickets?workspace=loregarden")
    assert everything.status_code == 200, everything.text
    ids = [row["id"] for row in everything.json()]
    assert len(ids) >= 3

    first = client.get("/api/tickets?workspace=loregarden&limit=2")
    assert first.status_code == 200, first.text
    assert [row["id"] for row in first.json()] == ids[:2]

    second = client.get("/api/tickets?workspace=loregarden&limit=2&offset=2")
    assert second.status_code == 200, second.text
    assert [row["id"] for row in second.json()] == ids[2:4]


def test_listing_is_unpaged_by_default(client, seeded):
    """No default page size: callers here count what they get back.

    A default would silently answer a different question — "the first N
    children" instead of "this parent's children".
    """
    response = client.get("/api/tickets?workspace=loregarden")
    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) > 2, "need more rows than the page size below to prove anything"

    paged = client.get("/api/tickets?workspace=loregarden&limit=2")
    assert len(paged.json()) == 2
    assert len(rows) == len(client.get("/api/tickets?workspace=loregarden").json())


def test_offset_without_limit_is_rejected(client):
    """An offset into an unbounded set is a caller error, not a silent full read."""
    response = client.get("/api/tickets?workspace=loregarden&offset=5")
    assert response.status_code == 400, response.text
    assert "limit" in response.json()["detail"]


def test_memo_does_not_survive_a_template_edit(client, isolated_db):
    """The memo is keyed by `template.version`, which a publish bumps.

    This is the risk the memo introduces: two resolutions in one session, with
    the template changed in between, must not return the first answer twice.
    """
    from loregarden.models.domain import WorkflowTemplate
    from loregarden.services.workflow_service import resolve_ticket_stages

    with Session(isolated_db) as session:
        ticket = session.exec(select(Ticket)).first()
        _, before = resolve_ticket_stages(session, ticket)
        assert before, "seed ticket has no stages to edit"

        template = session.exec(
            select(WorkflowTemplate).where(WorkflowTemplate.id == _template_id(session, ticket))
        ).one()
        trimmed = json.loads(template.stages_json)[:-1]
        template.stages_json = json.dumps(trimmed)
        template.version += 1
        session.add(template)
        session.flush()

        _, after = resolve_ticket_stages(session, ticket)

    assert len(after) == len(before) - 1, (
        f"memo served {len(after)} stages after the template dropped one (was {len(before)})"
    )


def _template_id(session, ticket) -> str:
    """The template `ticket` actually resolves through."""
    from loregarden.services.workflow_service import resolve_ticket_stages

    template, _ = resolve_ticket_stages(session, ticket)
    return template.id
