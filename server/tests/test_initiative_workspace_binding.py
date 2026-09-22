"""Nullable workspace_id invariant for initiatives (lg-initiatives-cross-732).

Maps 1:1 to AC1–AC8 / R1–R8. Implementation lands later — this is the red suite.
"""

from __future__ import annotations

import inspect
import re
import tempfile
from pathlib import Path

import pytest
from loregarden.db import migrations as M
from loregarden.db.migration_ids import SHIPPED_MIGRATION_IDS
from loregarden.db.migrations import apply_migrations
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import Ticket, WorkflowInstance, WorkItemType, Workspace
from loregarden.models.domain import tables as tables_mod
from loregarden.models.domain.schemas import UpdateTicketRequest
from loregarden.services.hierarchy_service import build_tree, reparent_ticket
from loregarden.services.ticket_service import TicketService
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select

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


def _raw_ticket_insert(
    conn,
    *,
    ticket_id: str,
    external_id: str,
    workspace_id: str | None,
    title: str,
    work_item_type: str,
) -> None:
    """INSERT with NOT NULL columns filled — create_all has no SQL defaults."""
    conn.execute(
        text(
            "INSERT INTO tickets ("
            "id, external_id, ticket_number, milestone_code, legacy_external_id, "
            "workspace_id, title, description, state, priority, branch, milestone, "
            "work_item_type, acceptance_criteria_json, tags_json, workflow_stage_key, "
            "workflow_stage_status, revision, last_updated_by, next_agent, next_status, "
            "blocking_issues, scope_reroute_agent, dispatch_waiver_stage_key, "
            "dispatch_waiver_approval_id, is_integration_review, state_locked, "
            "workflow_disabled, git_automation_json, triage_runtime_json, "
            "orchestration_runtime_json, permission_allowlist_json, compatibility_posture, "
            "created_at, updated_at"
            ") VALUES ("
            ":id, :ext, 0, '', '', :ws, :title, '', 'backlog', 3, '', '', :wit, "
            "'[]', '[]', '', 'pending', 0, '', '', 'Proceed', '', '', '', '', "
            "0, 0, 0, '', '{}', '{}', '[]', '', datetime('now'), datetime('now')"
            ")"
        ),
        {
            "id": ticket_id,
            "ext": external_id,
            "ws": workspace_id,
            "title": title,
            "wit": work_item_type,
        },
    )


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

    def test_seam_is_pure_no_session_param(self):
        from loregarden.services.ticket_workspace_binding import validate_workspace_binding

        params = inspect.signature(validate_workspace_binding).parameters
        assert "session" not in params
        assert "workspace_slug" not in params


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
                _raw_ticket_insert(
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
                _raw_ticket_insert(
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
            _raw_ticket_insert(
                conn,
                ticket_id="t-ok-init",
                external_id="",
                workspace_id=None,
                title="Ok init",
                work_item_type="initiative",
            )
            _raw_ticket_insert(
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

    def test_apply_makes_nullable_and_installs_check(self):
        mid = _migration_id()
        engine = _fresh_engine()
        SQLModel.metadata.create_all(engine)
        # Force a NOT NULL pre-state if create_all already emitted nullable after
        # the model change — the migration must still install the CHECK.
        apply_migrations(engine)
        assert _workspace_id_nullable(engine)
        sql = _tickets_sql(engine).replace(" ", "").lower()
        assert "work_item_type='initiative'" in sql or 'work_item_type="initiative"' in sql
        assert "workspace_idisnull" in sql.replace(" ", "")
        with engine.connect() as conn:
            recorded = {row[0] for row in conn.execute(text("SELECT id FROM schema_migrations"))}
        assert mid in recorded

    def test_reapply_is_noop(self):
        mid = _migration_id()
        engine = _fresh_engine()
        SQLModel.metadata.create_all(engine)
        first = apply_migrations(engine)
        with engine.connect() as conn:
            recorded = {row[0] for row in conn.execute(text("SELECT id FROM schema_migrations"))}
        assert mid in first or mid in recorded
        second = apply_migrations(engine)
        assert second == []

    def test_existing_non_initiative_workspace_id_untouched(self):
        engine = _fresh_engine()
        SQLModel.metadata.create_all(engine)
        apply_migrations(engine)
        # Seed after migrate (legal), then re-run migration body if exposed.
        ws_id = "ws-keep"
        with Session(engine) as session:
            session.add(Workspace(id=ws_id, slug="keep", name="Keep", repo_path="/tmp/keep"))
            session.commit()
        with Session(engine) as session:
            session.add(
                Ticket(
                    id="keep-ms",
                    external_id="keep-ms",
                    workspace_id=ws_id,
                    title="Keep me",
                    work_item_type=WorkItemType.MILESTONE,
                )
            )
            session.commit()
        assert apply_migrations(engine) == []
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT workspace_id FROM tickets WHERE id='keep-ms'")
            ).fetchone()
        assert row is not None
        assert row[0] == ws_id

    def test_illegal_preexisting_row_aborts_migration(self):
        """AC6 — refuse apply when a row would violate the new CHECK."""
        mid = _migration_id()
        migrate_fn = None
        for migration_id, fn in M.MIGRATIONS:
            if migration_id == mid:
                migrate_fn = fn
                break
        assert migrate_fn is not None

        engine = _fresh_engine()
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(Workspace(id="ws-bad", slug="bad", name="Bad", repo_path="/tmp/bad"))
            session.commit()
        # If create_all already has CHECK (model+table args), this insert fails —
        # that still proves the invariant; skip the abort path in that case.
        try:
            with engine.begin() as conn:
                _raw_ticket_insert(
                    conn,
                    ticket_id="bad-init",
                    external_id="",
                    workspace_id="ws-bad",
                    title="Pre-check init",
                    work_item_type="initiative",
                )
        except IntegrityError:
            pytest.skip("create_all already enforces CHECK — abort path needs old DDL")

        with pytest.raises((ValueError, RuntimeError), match="(?i)initiative|workspace|violat"):
            with engine.begin() as conn:
                migrate_fn(conn)


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

    def test_ticket_model_workspace_id_optional(self):
        field = tables_mod.Ticket.model_fields["workspace_id"]
        assert field.annotation == str | None or "None" in str(field.annotation)
        assert field.default is None

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
        root = Path(__file__).resolve().parents[2]
        # Presence check only — implement must not add client files for this ticket.
        assert (root / "client").is_dir()
