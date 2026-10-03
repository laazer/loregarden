"""Studio finalize issues an initiative the system's own ``init-*`` spelling.

Finalize is a create surface beside ``TicketService.create_ticket``. It used to
copy the proposal's ref onto both id columns for INITIATIVE rows, and check refs
against ``Ticket.workspace_id == workspace_id`` only — which never matches the
null-workspace rows initiatives live on. Between them a finalize could mint an
arbitrary global id, or shadow a live ``init-*`` one.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient
from loregarden.models.domain import Ticket, WorkItemType, Workspace
from loregarden.services.ticket_service import TicketService
from sqlmodel import Session, select

INITIATIVE_SPELLING = re.compile(r"^init-[a-z0-9-]+-\d+$")


def _finalize(
    client: TestClient,
    ref: str,
    *,
    title: str = "Cross Workspace Rollout",
    work_item_type: str = "initiative",
):
    return client.post(
        "/api/tickets/finalize-hierarchy",
        json={
            "workspace_slug": "loregarden",
            "hierarchy": [
                {
                    "external_id": ref,
                    "title": title,
                    "work_item_type": work_item_type,
                    "children": [],
                }
            ],
        },
    )


def _initiative_via_service(session: Session, title: str) -> Ticket:
    return TicketService(session).create_ticket(
        workspace_slug=None,
        title=title,
        work_item_type=WorkItemType.INITIATIVE,
    )


class TestFinalizeInitiativeSpelling:
    """AC1 — system-spelled ``external_id``, proposal ref on the legacy column."""

    def test_finalize_initiative_gets_system_spelling(
        self, client: TestClient, db_session: Session
    ):
        res = _finalize(client, "studio-proposal-init-01")
        assert res.status_code == 201, res.text

        ticket = db_session.exec(
            select(Ticket).where(Ticket.legacy_external_id == "studio-proposal-init-01")
        ).first()
        assert ticket is not None
        assert ticket.workspace_id is None
        assert ticket.legacy_external_id == "studio-proposal-init-01"
        assert INITIATIVE_SPELLING.match(ticket.external_id), ticket.external_id
        assert ticket.external_id != "studio-proposal-init-01"
        assert ticket.ticket_number > 0

    def test_finalize_cannot_mint_an_arbitrary_init_spelling(
        self, client: TestClient, db_session: Session
    ):
        """A ref already shaped like an initiative id still does not become one."""
        res = _finalize(client, "init-hand-written-9999")
        assert res.status_code == 201, res.text

        ticket = db_session.exec(
            select(Ticket).where(Ticket.legacy_external_id == "init-hand-written-9999")
        ).first()
        assert ticket is not None
        assert ticket.legacy_external_id == "init-hand-written-9999"
        assert ticket.external_id != "init-hand-written-9999"
        assert INITIATIVE_SPELLING.match(ticket.external_id), ticket.external_id


class TestFinalizeRefUniquenessIsGlobal:
    """AC2/AC3/AC4 — refs collide against any ticket, including null-workspace rows."""

    def test_finalize_rejects_ref_taken_by_a_service_created_initiative(
        self, client: TestClient, db_session: Session
    ):
        """AC3 — TicketService init-* spelling is not free for a studio finalize."""
        existing = _initiative_via_service(db_session, "Collide Me")
        db_session.commit()
        assert existing.workspace_id is None
        assert INITIATIVE_SPELLING.match(existing.external_id), existing.external_id

        res = _finalize(client, existing.external_id, title="Impostor")
        assert res.status_code == 400, res.text

        rows = db_session.exec(
            select(Ticket).where(Ticket.external_id == existing.external_id)
        ).all()
        assert len(rows) == 1
        assert rows[0].id == existing.id

    def test_finalize_rejects_ref_taken_by_another_workspaces_ticket(
        self, client: TestClient, db_session: Session
    ):
        """AC2 — taken set is global; OR workspace_id IS NULL alone is not enough."""
        seeded = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
        other_ws = Workspace(
            slug="otherspace",
            name="Other",
            ticket_prefix="oth",
            workflow_template_id=seeded.workflow_template_id,
        )
        db_session.add(other_ws)
        db_session.commit()
        TicketService(db_session).create_ticket(
            workspace_slug="otherspace",
            title="Foreign ticket",
            work_item_type=WorkItemType.MILESTONE,
            external_id="oth-none-1",
        )
        db_session.commit()

        res = _finalize(client, "oth-none-1", title="Reuse A Foreign Ref")
        assert res.status_code == 400, res.text

    def test_finalize_rejects_ref_taken_as_legacy_external_id(
        self, client: TestClient, db_session: Session
    ):
        """AC2 — a live legacy spelling is taken even when external_id differs."""
        first = _finalize(client, "studio-legacy-taken-01", title="Original")
        assert first.status_code == 201, first.text
        original = db_session.exec(
            select(Ticket).where(Ticket.legacy_external_id == "studio-legacy-taken-01")
        ).one()
        assert original.external_id != "studio-legacy-taken-01"

        res = _finalize(client, "studio-legacy-taken-01", title="Reuse Legacy")
        assert res.status_code == 400, res.text

        rows = db_session.exec(
            select(Ticket).where(Ticket.legacy_external_id == "studio-legacy-taken-01")
        ).all()
        assert len(rows) == 1
        assert rows[0].id == original.id

    def test_finalize_rejects_non_initiative_ref_equal_to_live_init_spelling(
        self, client: TestClient, db_session: Session
    ):
        """AC4 — global gate must reject before assign_external_id (no uniqueness there)."""
        existing = _initiative_via_service(db_session, "Live Init Spelling")
        db_session.commit()
        assert INITIATIVE_SPELLING.match(existing.external_id), existing.external_id

        res = _finalize(
            client,
            existing.external_id,
            title="Milestone Alias Onto Init",
            work_item_type="milestone",
        )
        assert res.status_code == 400, res.text

        rows = db_session.exec(
            select(Ticket).where(Ticket.external_id == existing.external_id)
        ).all()
        assert len(rows) == 1
        assert rows[0].id == existing.id
        assert rows[0].work_item_type == WorkItemType.INITIATIVE

    def test_two_finalized_initiatives_do_not_share_a_spelling(
        self, client: TestClient, db_session: Session
    ):
        assert _finalize(client, "dup-slug-a", title="Shared Title").status_code == 201
        assert _finalize(client, "dup-slug-b", title="Shared Title").status_code == 201

        first = db_session.exec(
            select(Ticket).where(Ticket.legacy_external_id == "dup-slug-a")
        ).first()
        second = db_session.exec(
            select(Ticket).where(Ticket.legacy_external_id == "dup-slug-b")
        ).first()
        assert first is not None and second is not None
        assert first.external_id != second.external_id
        assert INITIATIVE_SPELLING.match(second.external_id), second.external_id
