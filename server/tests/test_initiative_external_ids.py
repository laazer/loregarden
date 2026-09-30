"""Initiative external_id scheme and cross-workspace resolve (lg-initiatives-cross-733).

Maps to AC2–AC6 / R1–R6. Red until implement merges 732 (R0) and lands the global
counter, assign path, and resolve INCLUDE.

False-green guards (from the spec):
- Pin resolve(session, id, workspace_id=ws.id) directly — never get_ticket alone.
- list_tickets_mcp(parent_external_id=init-…) has no unscoped fallback.
- Board/list EXCLUDE must be asserted so a broken list filter cannot hide orphans.

When merging 732: update test_initiative_workspace_binding.py
test_ac3_creates_initiative_with_null_workspace and
test_ac3_caller_supplied_external_id_kept — those asserted external_id=='' /
supplied-as-canonical; this suite is the new contract (init-* + legacy).
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from loregarden.api import tickets as tickets_api
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import Ticket, WorkItemType, Workspace
from loregarden.services.hierarchy_service import build_tree, collect_ticket_scope_ids
from loregarden.services.ticket_discovery import (
    compact_ticket_row,
    list_tickets_mcp,
    ticket_neighbors_mcp,
)
from loregarden.services.ticket_ids import resolve
from loregarden.services.ticket_service import TicketService
from sqlalchemy import text
from sqlmodel import Session, select

_INIT_ID_RE = re.compile(r"^init-[a-z0-9-]+-\d+$")


# --- helpers -----------------------------------------------------------------


def _initiative() -> WorkItemType:
    try:
        return WorkItemType.INITIATIVE
    except AttributeError as exc:
        raise AssertionError(
            "WorkItemType.INITIATIVE missing — merge loregarden/lg-initiatives-cross-732 first (R0/AC1)"
        ) from exc


def _call(session: Session, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return json.loads(execute_tool(session, name, normalize_tool_arguments(name, args)))


def _create_initiative(
    session: Session,
    *,
    title: str = "Cross-workspace initiative",
    external_id: str = "",
) -> Ticket:
    return TicketService(session).create_ticket(
        workspace_slug=None,
        title=title,
        work_item_type=_initiative(),
        external_id=external_id,
    )


def _workspace(session: Session, slug: str = "loregarden") -> Workspace:
    ws = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    assert ws is not None, f"workspace {slug!r} missing from test DB"
    return ws


def _trailing_n(external_id: str) -> int:
    match = _INIT_ID_RE.match(external_id)
    assert match, f"expected init-<slug>-<n>, got {external_id!r}"
    return int(external_id.rsplit("-", 1)[-1])


def _count_tickets(session: Session) -> int:
    return len(session.exec(select(Ticket)).all())


def _pool_last(session: Session) -> int:
    last = session.execute(
        text("SELECT last_initiative_number FROM initiative_number_pool WHERE id='global'")
    ).scalar()
    assert last is not None, "initiative_number_pool global row missing — implement R1"
    return int(last)


# --- AC2 / R1+R3 create spelling + pool --------------------------------------


class TestInitiativeCreateSpelling:
    """AC2 / R3.AC1 / R3.AC4 — create yields init-*; null workspace; no workflow assign."""

    def test_create_yields_init_slug_number_matching_ticket_number(self, db_session: Session):
        initiative = _create_initiative(db_session, title="Initiative External Ids")
        assert initiative.workspace_id is None
        assert _INIT_ID_RE.match(initiative.external_id), initiative.external_id
        assert initiative.ticket_number == _trailing_n(initiative.external_id)
        assert initiative.ticket_number >= 1

    def test_pool_advances_and_survives_delete(self, db_session: Session):
        first = _create_initiative(db_session, title="Pool Advance Alpha")
        n = first.ticket_number
        assert _pool_last(db_session) >= n

        # create_ticket publishes domain_events that FK the ticket.
        db_session.execute(
            text("DELETE FROM domain_events WHERE ticket_id = :id"), {"id": first.id}
        )
        db_session.delete(first)
        db_session.commit()

        second = _create_initiative(db_session, title="Pool Advance Beta")
        assert second.ticket_number > n
        assert _pool_last(db_session) >= second.ticket_number

    def test_create_does_not_bump_workspace_last_ticket_number(self, db_session: Session):
        """R1 constraint — never workspaces.last_ticket_number."""
        ws = _workspace(db_session)
        before = ws.last_ticket_number
        _create_initiative(db_session, title="No Workspace Counter")
        db_session.refresh(ws)
        assert ws.last_ticket_number == before


# --- AC3 / R3 supplied → legacy + global uniqueness --------------------------


class TestInitiativeAssignAndUniqueness:
    """AC3 / R3.AC2–AC3 — supplied becomes legacy; spelling unique globally."""

    def test_supplied_id_becomes_legacy_external_id(self, db_session: Session):
        initiative = _create_initiative(
            db_session,
            title="Supplied Legacy Key",
            external_id="my-old-key",
        )
        assert initiative.legacy_external_id == "my-old-key"
        assert _INIT_ID_RE.match(initiative.external_id), initiative.external_id
        assert initiative.external_id != "my-old-key"

    def test_duplicate_external_id_spelling_rejected_globally(self, db_session: Session):
        first = _create_initiative(db_session, title="Unique Spelling One")
        before = _count_tickets(db_session)
        with pytest.raises(ValueError):
            _create_initiative(
                db_session,
                title="Unique Spelling Collision",
                external_id=first.external_id,
            )
        assert _count_tickets(db_session) == before

    def test_duplicate_legacy_spelling_rejected_globally(self, db_session: Session):
        _create_initiative(
            db_session,
            title="Legacy Unique Alpha",
            external_id="shared-legacy-key",
        )
        before = _count_tickets(db_session)
        with pytest.raises(ValueError):
            _create_initiative(
                db_session,
                title="Legacy Unique Beta",
                external_id="shared-legacy-key",
            )
        assert _count_tickets(db_session) == before


# --- AC4 / R4 direct resolve (also covered in test_ticket_ids; create path) ---


class TestInitiativeResolveViaCreate:
    """AC4 — resolve INCLUDE after a real TicketService create."""

    def test_direct_scoped_resolve_finds_created_initiative(self, db_session: Session):
        ws = _workspace(db_session)
        initiative = _create_initiative(db_session, title="Resolve After Create")
        found = resolve(db_session, initiative.external_id, workspace_id=ws.id)
        assert found is not None
        assert found.id == initiative.id

    def test_bare_and_trailing_numbers_do_not_resolve_to_initiative(self, db_session: Session):
        ws = _workspace(db_session)
        initiative = _create_initiative(db_session, title="Numbers Stay Workspace")
        bare = resolve(db_session, str(initiative.ticket_number), workspace_id=ws.id)
        assert bare is None or bare.id != initiative.id
        trailing = resolve(
            db_session,
            f"{ws.ticket_prefix}-noise-{initiative.ticket_number}",
            workspace_id=ws.id,
        )
        assert trailing is None or trailing.id != initiative.id


# --- AC5 / R5 MCP get / create parent= / list parent_external_id -------------


class TestInitiativeMcpSurfaces:
    """AC5 — get_ticket ± slug; create parent=; list parent_external_id false-green guard."""

    def test_get_ticket_without_workspace_slug(self, db_session: Session):
        initiative = _create_initiative(db_session, title="Get Ticket Unscoped")
        payload = _call(
            db_session,
            "loregarden_get_ticket",
            {"ticket_id": initiative.external_id},
        )
        assert payload["ticket_id"] == initiative.id
        assert payload["external_id"] == initiative.external_id

    def test_get_ticket_with_workspace_slug(self, db_session: Session):
        """Secondary coverage — not a substitute for direct resolve INCLUDE."""
        initiative = _create_initiative(db_session, title="Get Ticket Scoped")
        payload = _call(
            db_session,
            "loregarden_get_ticket",
            {
                "ticket_id": initiative.external_id,
                "workspace_slug": "loregarden",
            },
        )
        assert payload["ticket_id"] == initiative.id
        assert payload["external_id"] == initiative.external_id

    def test_create_ticket_parent_attaches_milestone(self, db_session: Session):
        initiative = _create_initiative(db_session, title="Parent For Milestone")
        created = _call(
            db_session,
            "loregarden_create_ticket",
            {
                "workspace_slug": "loregarden",
                "title": "Child Milestone Under Initiative",
                "work_item_type": "milestone",
                "parent": initiative.external_id,
            },
        )
        child = db_session.get(Ticket, created["id"])
        assert child is not None
        assert child.work_item_type == WorkItemType.MILESTONE
        assert child.parent_ticket_id == initiative.id
        assert child.workspace_id == _workspace(db_session).id

    def test_list_tickets_mcp_parent_external_id_finds_children(self, db_session: Session):
        """R5.AC4 / false-green guard — no unscoped fallback on this path."""
        initiative = _create_initiative(db_session, title="List Parent External")
        milestone = TicketService(db_session).create_ticket(
            workspace_slug="loregarden",
            title="Listed Child Milestone",
            work_item_type=WorkItemType.MILESTONE,
            parent_ticket_id=initiative.id,
        )
        payload = list_tickets_mcp(
            db_session,
            workspace_slug="loregarden",
            parent_external_id=initiative.external_id,
        )
        ids = {row["id"] for row in payload["tickets"]}
        assert milestone.id in ids
        assert initiative.id not in ids


# --- AC6 / R6 EXCLUDE list/board; INCLUDE neighbors/ancestors; null-safe -----


class TestInitiativeJoinSites:
    """AC6 — list EXCLUDE; neighbors/ancestors/subtree INCLUDE; compact null-safe."""

    def test_list_tickets_mcp_excludes_orphan_initiative(self, db_session: Session):
        initiative = _create_initiative(db_session, title="Orphan List Exclude")
        payload = list_tickets_mcp(db_session, workspace_slug="loregarden", limit=100)
        ids = {row["id"] for row in payload["tickets"]}
        assert initiative.id not in ids

    def test_workspace_api_list_excludes_orphan_initiative(self, db_session: Session, client):
        initiative = _create_initiative(db_session, title="Orphan API List Exclude")
        res = client.get("/api/tickets", params={"workspace": "loregarden"})
        assert res.status_code == 200
        ids = {row["id"] for row in res.json()}
        assert initiative.id not in ids

    def test_neighbors_include_null_workspace_initiative_parent(self, db_session: Session):
        initiative = _create_initiative(db_session, title="Neighbors Parent")
        milestone = TicketService(db_session).create_ticket(
            workspace_slug="loregarden",
            title="Neighbors Child Milestone",
            work_item_type=WorkItemType.MILESTONE,
            parent_ticket_id=initiative.id,
        )
        neighbors = ticket_neighbors_mcp(db_session, milestone)
        assert neighbors["parent"] is not None
        assert neighbors["parent"]["id"] == initiative.id
        assert neighbors["parent"]["workspace_slug"] == ""

    def test_collect_ancestors_includes_null_workspace_initiative(self, db_session: Session):
        initiative = _create_initiative(db_session, title="Ancestor Walk")
        milestone = TicketService(db_session).create_ticket(
            workspace_slug="loregarden",
            title="Ancestor Child Milestone",
            work_item_type=WorkItemType.MILESTONE,
            parent_ticket_id=initiative.id,
        )
        ancestors = tickets_api._collect_ancestors(db_session, [milestone])
        assert any(a.id == initiative.id for a in ancestors)

    def test_subtree_scope_from_initiative_includes_workspace_children(self, db_session: Session):
        initiative = _create_initiative(db_session, title="Subtree Scope Root")
        milestone = TicketService(db_session).create_ticket(
            workspace_slug="loregarden",
            title="Subtree Scope Milestone",
            work_item_type=WorkItemType.MILESTONE,
            parent_ticket_id=initiative.id,
        )
        scope = collect_ticket_scope_ids(db_session, initiative.id)
        assert initiative.id in scope
        assert milestone.id in scope

    def test_compact_and_tree_null_safe_workspace_slug(self, db_session: Session):
        initiative = _create_initiative(db_session, title="Compact Null Safe")
        row = compact_ticket_row(db_session, initiative)
        assert row["workspace_slug"] == ""

        milestone = TicketService(db_session).create_ticket(
            workspace_slug="loregarden",
            title="Tree Null Safe Milestone",
            work_item_type=WorkItemType.MILESTONE,
            parent_ticket_id=initiative.id,
        )
        # Tree must include the null-workspace parent or the child would be re-rooted.
        forest = build_tree(db_session, [initiative, milestone])
        assert len(forest) == 1
        assert forest[0].id == initiative.id
        assert forest[0].workspace_slug == ""
        assert forest[0].children[0].id == milestone.id
