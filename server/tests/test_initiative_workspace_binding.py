"""Nullable workspace_id invariant for initiatives (lg-initiatives-cross-732).

Maps 1:1 to AC1–AC8 / R1–R8. Implementation lands later — this is the red suite.
"""

from __future__ import annotations

import re
import tempfile
from unittest import mock

import pytest
from loregarden.db import migrations as M
from loregarden.db import migrations_ticket_workspace
from loregarden.db.migration_ids import SHIPPED_MIGRATION_IDS
from loregarden.db.migrations import apply_migrations
from loregarden.db.migrations_ticket_workspace import m_tickets_workspace_binding
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import Ticket, WorkflowInstance, WorkItemType, Workspace
from loregarden.models.domain.schemas import UpdateTicketRequest
from loregarden.services.hierarchy_service import build_tree, reparent_ticket
from loregarden.services.ticket_service import TicketService
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select
from tests.repo_paths import REPO_ROOT
from tests.ticket_row_helpers import raw_ticket_insert

PREV_TIP = "0130_retire_unmerged_branch_ledger_ids"

# --- helpers -----------------------------------------------------------------


def _initiative() -> WorkItemType:
    try:
        return WorkItemType.INITIATIVE
    except AttributeError as exc:
        raise AssertionError("WorkItemType.INITIATIVE missing — merge 731 first") from exc


def _validate_workspace_binding(work_item_type: WorkItemType, workspace_id: str | None) -> None:
    """AC1 seam — fail clearly until ticket_workspace_binding lands."""
    try:
        from loregarden.services.ticket_workspace_binding import validate_workspace_binding
    except ImportError as exc:
        raise AssertionError(
            "ticket_workspace_binding.validate_workspace_binding is missing — R1 / AC1–AC3"
        ) from exc
    validate_workspace_binding(work_item_type, workspace_id)


def _migration_id() -> str:
    """AC6 — next free id after live tip; module may expose it explicitly."""
    try:
        from loregarden.db import migrations_ticket_workspace as mod
    except ImportError as exc:
        raise AssertionError(
            f"db.migrations_ticket_workspace missing — R3 / AC6 (expect 0131_* after {PREV_TIP})"
        ) from exc
    mid = getattr(mod, "MIGRATION_ID", None)
    if not mid:
        raise AssertionError("migrations_ticket_workspace.MIGRATION_ID missing — R3 / AC6")
    return mid


def _count_tickets(session: Session) -> int:
    return len(session.exec(select(Ticket)).all())


def _create_milestone(session: Session, title: str = "Bound milestone") -> Ticket:
    return TicketService(session).create_ticket(
        workspace_slug="loregarden",
        title=title,
        work_item_type=WorkItemType.MILESTONE,
    )


def _create_initiative(
    session: Session,
    *,
    title: str = "Null-workspace initiative",
    workspace_slug: str | None = None,
    external_id: str = "",
) -> Ticket:
    return TicketService(session).create_ticket(
        workspace_slug=workspace_slug,
        title=title,
        work_item_type=_initiative(),
        external_id=external_id,
    )


def _fresh_engine():
    tmp = tempfile.mkdtemp()
    return create_engine(f"sqlite:///{tmp}/t.db")


def _tickets_sql(engine) -> str:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'")
        ).fetchone()
    assert row is not None, "tickets table missing"
    return row[0]


def _workspace_id_nullable(engine) -> bool:
    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(tickets)")).fetchall()
    for row in rows:
        # cid, name, type, notnull, dflt_value, pk
        if row[1] == "workspace_id":
            return int(row[3]) == 0
    raise AssertionError("tickets.workspace_id column missing")


# --- R1 / unit seam ----------------------------------------------------------


class TestValidateWorkspaceBinding:
    """R1 — pure pairs; no Session I/O."""

    def test_initiative_none_ok(self):
        _validate_workspace_binding(_initiative(), None)

    @pytest.mark.parametrize(
        "wit",
        [
            WorkItemType.MILESTONE,
            WorkItemType.FEATURE,
            WorkItemType.CAPABILITY,
            WorkItemType.TASK,
            WorkItemType.BUG,
        ],
    )
    def test_bound_types_with_id_ok(self, wit: WorkItemType):
        _validate_workspace_binding(wit, "ws-id")

    def test_initiative_with_id_rejected(self):
        with pytest.raises(ValueError, match="(?i)initiative"):
            _validate_workspace_binding(_initiative(), "ws-id")

    def test_task_without_workspace_rejected(self):
        with pytest.raises(ValueError, match="(?i)workspace"):
            _validate_workspace_binding(WorkItemType.TASK, None)


# --- AC1 / AC2 / AC3 TicketService.create ------------------------------------


class TestTicketServiceWorkspaceBinding:
    """AC1–AC3 (+ R4.4 regression)."""

    def test_ac1_rejects_initiative_with_workspace_slug(self, db_session: Session):
        """AC1 — initiative + non-empty slug → ValueError; no row."""
        before = _count_tickets(db_session)
        with pytest.raises(ValueError) as excinfo:
            _create_initiative(db_session, workspace_slug="loregarden")
        assert (
            "initiative" in str(excinfo.value).lower() or "workspace" in str(excinfo.value).lower()
        )
        assert _count_tickets(db_session) == before

    def test_ac2_rejects_milestone_without_workspace(self, db_session: Session):
        """AC2 — non-initiative + missing/empty slug → ValueError; no row."""
        before = _count_tickets(db_session)
        with pytest.raises(ValueError, match="(?i)workspace"):
            TicketService(db_session).create_ticket(
                workspace_slug=None,
                title="Orphan milestone",
                work_item_type=WorkItemType.MILESTONE,
            )
        with pytest.raises(ValueError, match="(?i)workspace"):
            TicketService(db_session).create_ticket(
                workspace_slug="",
                title="Empty-slug milestone",
                work_item_type=WorkItemType.MILESTONE,
            )
        assert _count_tickets(db_session) == before

    def test_ac3_creates_initiative_with_null_workspace(self, db_session: Session):
        """AC3 — null workspace_id; no WorkflowInstance; system-spelled init-* id."""
        initiative = _create_initiative(db_session, title="AC3 initiative")
        assert initiative.workspace_id is None
        assert initiative.work_item_type == _initiative()
        assert re.match(r"^init-[a-z0-9-]+-\d+$", initiative.external_id), initiative.external_id
        assert initiative.ticket_number == int(initiative.external_id.rsplit("-", 1)[-1])
        instances = db_session.exec(
            select(WorkflowInstance).where(WorkflowInstance.ticket_id == initiative.id)
        ).all()
        assert instances == []

    def test_ac3_caller_supplied_external_id_becomes_legacy(self, db_session: Session):
        """733 contract — supplied id is legacy; external_id is always init-*."""
        initiative = _create_initiative(
            db_session,
            title="AC3 supplied id",
            external_id="supplied-init-id",
        )
        assert initiative.workspace_id is None
        assert initiative.legacy_external_id == "supplied-init-id"
        assert re.match(r"^init-[a-z0-9-]+-\d+$", initiative.external_id), initiative.external_id
        assert initiative.external_id != "supplied-init-id"

    def test_r4_4_non_initiative_with_slug_still_binds(self, db_session: Session):
        milestone = _create_milestone(db_session, "Still bound")
        ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
        assert ws is not None
        assert milestone.workspace_id == ws.id


# --- AC4 SQLite CHECK --------------------------------------------------------


class TestTicketsWorkspaceCheckConstraint:
    """AC4 — IntegrityError for illegal pairs; legal pairs succeed.

    create_all does not install the CHECK — assert against a migrated engine.
    """

    def _migrated(self):
        engine = _fresh_engine()
        SQLModel.metadata.create_all(engine)
        apply_migrations(engine)
        mid = _migration_id()
        with engine.connect() as conn:
            recorded = {row[0] for row in conn.execute(text("SELECT id FROM schema_migrations"))}
        assert mid in recorded, f"{mid} not applied"
        return engine

    def _seed_workspace(self, engine) -> str:
        ws_id = "ws-check-1"
        with Session(engine) as session:
            session.add(
                Workspace(id=ws_id, slug="check-ws", name="Check", repo_path="/tmp/check-ws")
            )
            session.commit()
        return ws_id

    def test_illegal_initiative_with_workspace_id(self):
        engine = self._migrated()
        ws_id = self._seed_workspace(engine)
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                raw_ticket_insert(
                    conn,
                    ticket_id="t-bad-init",
                    external_id="",
                    workspace_id=ws_id,
                    title="Bad init",
                    work_item_type="initiative",
                )

    def test_illegal_task_with_null_workspace(self):
        engine = self._migrated()
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                raw_ticket_insert(
                    conn,
                    ticket_id="t-bad-task",
                    external_id="x",
                    workspace_id=None,
                    title="Bad task",
                    work_item_type="task",
                )

    def test_legal_pairs_succeed(self):
        engine = self._migrated()
        ws_id = self._seed_workspace(engine)
        with engine.begin() as conn:
            raw_ticket_insert(
                conn,
                ticket_id="t-ok-init",
                external_id="",
                workspace_id=None,
                title="Ok init",
                work_item_type="initiative",
            )
            raw_ticket_insert(
                conn,
                ticket_id="t-ok-ms",
                external_id="ms-1",
                workspace_id=ws_id,
                title="Ok ms",
                work_item_type="milestone",
            )


# --- AC5 move refuse ---------------------------------------------------------


class TestMoveRefusesInitiative:
    """AC5 — clear ValueError before assign; workspace_id stays NULL."""

    def test_move_initiative_raises_and_preserves_null(self, db_session: Session):
        existing = db_session.exec(select(Workspace).where(Workspace.slug == "elsewhere")).first()
        if existing is None:
            existing = Workspace(slug="elsewhere", name="Elsewhere", repo_path="/tmp/elsewhere")
            db_session.add(existing)
            db_session.commit()

        initiative = _create_initiative(db_session, title="Immovable initiative")
        assert initiative.workspace_id is None

        with pytest.raises(ValueError, match="(?i)initiative"):
            execute_tool(
                db_session,
                "loregarden_move_ticket_workspace",
                normalize_tool_arguments(
                    "loregarden_move_ticket_workspace",
                    {
                        "ticket_id": initiative.id,
                        "workspace_slug": "elsewhere",
                        "detach_parent": True,
                    },
                ),
            )

        db_session.refresh(initiative)
        assert initiative.workspace_id is None


# --- AC6 migration -----------------------------------------------------------


class TestWorkspaceBindingMigration:
    """AC6 — append-only, guarded, listed, no UPDATE of workspace_id, abort on illegal."""

    def test_registered_after_live_tip(self):
        mid = _migration_id()
        assert mid in SHIPPED_MIGRATION_IDS
        assert SHIPPED_MIGRATION_IDS.index(mid) > SHIPPED_MIGRATION_IDS.index(PREV_TIP)
        ids = [i for i, _ in M.MIGRATIONS]
        assert mid in ids
        assert ids.index(mid) > ids.index(PREV_TIP)

    @pytest.fixture()
    def old_engine(self):
        """`tickets` as it was before the migration: the column nullable or not, and
        no CHECK. `create_all` emits today's table, on which the migration returns
        at once."""

        def build(*, not_null: bool, rows: list[tuple[str, str, str | None]]):
            engine = _fresh_engine()
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "CREATE TABLE tickets (id VARCHAR PRIMARY KEY, "
                        "work_item_type VARCHAR NOT NULL, "
                        f"workspace_id VARCHAR{' NOT NULL' if not_null else ''})"
                    )
                )
                for ticket_id, kind, workspace_id in rows:
                    conn.execute(
                        text("INSERT INTO tickets VALUES (:id, :kind, :ws)"),
                        {"id": ticket_id, "kind": kind, "ws": workspace_id},
                    )
            return engine

        return build

    @pytest.mark.parametrize(
        ("not_null", "rows"),
        [
            (True, [("ms", "milestone", "ws-keep"), ("t", "task", "ws-keep")]),
            (False, [("ms", "milestone", "ws-keep"), ("init", "initiative", None)]),
        ],
        ids=["not-null", "nullable-without-check"],
    )
    def test_an_old_table_becomes_nullable_and_checked_and_keeps_its_rows(
        self, old_engine, not_null, rows
    ):
        engine = old_engine(not_null=not_null, rows=rows)
        with engine.begin() as conn:
            m_tickets_workspace_binding(conn)

        assert _workspace_id_nullable(engine)
        with engine.connect() as conn:
            kept = conn.execute(text("SELECT id, work_item_type, workspace_id FROM tickets"))
            assert sorted(tuple(row) for row in kept) == sorted(rows)
        for kind, workspace_id in (("initiative", "ws-keep"), ("task", None)):
            with pytest.raises(IntegrityError), engine.begin() as conn:
                conn.execute(
                    text("INSERT INTO tickets VALUES ('bad', :kind, :ws)"),
                    {"kind": kind, "ws": workspace_id},
                )
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO tickets VALUES ('new', 'initiative', NULL)"))

    @pytest.mark.parametrize(("kind", "workspace_id"), [("initiative", "ws-bad"), ("task", None)])
    def test_a_row_that_breaks_the_rule_stops_the_migration_and_is_not_rewritten(
        self, old_engine, kind, workspace_id
    ):
        engine = old_engine(not_null=False, rows=[("bad", kind, workspace_id)])
        before = _tickets_sql(engine)

        with pytest.raises(ValueError, match=f"1 ticket row.*bad .*{kind}"):
            with engine.begin() as conn:
                m_tickets_workspace_binding(conn)

        assert _tickets_sql(engine) == before
        with engine.connect() as conn:
            assert conn.execute(text("SELECT workspace_id FROM tickets")).scalar() == workspace_id

    def test_a_migrated_table_is_left_as_it_is(self, old_engine):
        engine = old_engine(not_null=True, rows=[])
        with engine.begin() as conn:
            m_tickets_workspace_binding(conn)
        with (
            mock.patch.object(
                migrations_ticket_workspace, "rebuild_drop_not_null_and_add_check"
            ) as rebuild,
            engine.begin() as conn,
        ):
            m_tickets_workspace_binding(conn)
        rebuild.assert_not_called()


# --- AC7 parent workspace rule -----------------------------------------------


class TestNullInitiativeParentWorkspace:
    """AC7 — null-workspace INITIATIVE may parent bound MILESTONE; unequal non-null still rejected."""

    def test_create_milestone_under_null_initiative(self, db_session: Session):
        initiative = _create_initiative(db_session, title="Parent init")
        assert initiative.workspace_id is None
        milestone = TicketService(db_session).create_ticket(
            workspace_slug="loregarden",
            title="Child milestone",
            work_item_type=WorkItemType.MILESTONE,
            parent_ticket_id=initiative.id,
        )
        assert milestone.parent_ticket_id == initiative.id
        assert milestone.workspace_id is not None
        assert initiative.workspace_id is None
        assert milestone.workspace_id != initiative.workspace_id  # None vs str

    def test_reparent_milestone_under_null_initiative(self, db_session: Session):
        initiative = _create_initiative(db_session, title="Reparent init")
        milestone = _create_milestone(db_session, "Orphan ms")
        reparent_ticket(db_session, milestone, initiative.id)
        db_session.commit()
        assert milestone.parent_ticket_id == initiative.id
        assert milestone.workspace_id is not None
        assert initiative.workspace_id is None

    def test_import_milestone_under_null_initiative(self, client, db_session: Session):
        initiative = _create_initiative(db_session, title="Import parent init")
        res = client.post(
            "/api/tickets/import",
            json={
                "workspace_slug": "loregarden",
                "tickets": [
                    {
                        "title": "Imported under null init",
                        "work_item_type": "milestone",
                        "parent_ticket_id": initiative.id,
                    }
                ],
            },
        )
        assert res.status_code == 201, res.text
        child = db_session.get(Ticket, res.json()["ticket_ids"][0])
        assert child is not None
        assert child.parent_ticket_id == initiative.id
        assert child.workspace_id is not None
        assert initiative.workspace_id is None

    def test_unequal_non_null_parent_still_rejected(self, db_session: Session):
        other = Workspace(slug="other-ws", name="Other", repo_path="/tmp/other-ws")
        db_session.add(other)
        db_session.commit()
        db_session.refresh(other)

        # Bound milestone in other workspace, pretending to parent a loregarden child.
        foreign = Ticket(
            external_id="foreign-ms",
            workspace_id=other.id,
            title="Foreign milestone",
            work_item_type=WorkItemType.MILESTONE,
        )
        db_session.add(foreign)
        db_session.commit()
        db_session.refresh(foreign)

        with pytest.raises(ValueError, match="(?i)workspace|parent"):
            TicketService(db_session).create_ticket(
                workspace_slug="loregarden",
                title="Cross-ws child",
                work_item_type=WorkItemType.FEATURE,
                parent_ticket_id=foreign.id,
            )


# --- AC8 / R7 writers + surfaces ---------------------------------------------


class TestWritersAndSurfaces:
    """AC8 — UpdateTicketRequest cannot set type/workspace; build_tree null-safe."""

    def test_update_ticket_request_forbids_type_and_workspace(self):
        fields = set(UpdateTicketRequest.model_fields)
        assert "work_item_type" not in fields
        assert "workspace_id" not in fields

    def test_build_tree_tolerates_null_workspace_initiative(self, db_session: Session):
        initiative = _create_initiative(db_session, title="Tree init")
        milestone = TicketService(db_session).create_ticket(
            workspace_slug="loregarden",
            title="Tree ms",
            work_item_type=WorkItemType.MILESTONE,
            parent_ticket_id=initiative.id,
        )
        # Must not crash on None workspace_id when resolving slug.
        tree = build_tree(db_session, [initiative, milestone])
        assert tree is not None

    def test_client_tree_untouched_contract(self):
        """AC8 — no client/** changes required; pin that this ticket's suite is server-only."""
        root = REPO_ROOT
        # Presence check only — implement must not add client files for this ticket.
        assert (root / "client").is_dir()
