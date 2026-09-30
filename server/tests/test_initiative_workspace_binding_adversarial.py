"""Adversarial / mutation coverage for nullable initiative workspace_id (732).

Extends test_initiative_workspace_binding.py into gaps the AC suite leaves open:
combinatorial seam matrix, whitespace slug mutations, move-order AST, service↔CHECK
agreement, UPDATE-path CHECK, migration abort symmetry, false-green AC2 pinning,
and assign_external_id / counter skip mutations.

Expected red until implement lands ticket_workspace_binding + nullable model +
CHECK migration + create/move/parent writers. Do not attribute those reds to
implement regressions.
"""

from __future__ import annotations

import ast
import inspect
import re
import tempfile
from itertools import product
from unittest.mock import patch

import pytest
from loregarden.db import migrations as M
from loregarden.db.migrations import apply_migrations
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import Ticket, WorkItemType, Workspace
from loregarden.services.hierarchy_service import build_tree, reparent_ticket
from loregarden.services.ticket_service import TicketService
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select
from tests.ticket_row_helpers import raw_ticket_insert

# --- helpers -----------------------------------------------------------------


def _initiative() -> WorkItemType:
    try:
        return WorkItemType.INITIATIVE
    except AttributeError as exc:
        raise AssertionError("WorkItemType.INITIATIVE missing — merge 731 first") from exc


def _validate_workspace_binding(work_item_type: WorkItemType, workspace_id: str | None) -> None:
    try:
        from loregarden.services.ticket_workspace_binding import validate_workspace_binding
    except ImportError as exc:
        raise AssertionError(
            "ticket_workspace_binding.validate_workspace_binding missing — R1"
        ) from exc
    validate_workspace_binding(work_item_type, workspace_id)


def _migration_id() -> str:
    try:
        from loregarden.db import migrations_ticket_workspace as mod
    except ImportError as exc:
        raise AssertionError("db.migrations_ticket_workspace missing — R3 / AC6") from exc
    mid = getattr(mod, "MIGRATION_ID", None)
    if not mid:
        raise AssertionError("migrations_ticket_workspace.MIGRATION_ID missing")
    return mid


def _create_initiative(session: Session, **kwargs) -> Ticket:
    kwargs.setdefault("title", "Adv initiative")
    kwargs.setdefault("workspace_slug", None)
    return TicketService(session).create_ticket(
        work_item_type=_initiative(),
        **kwargs,
    )


def _all_types() -> list[WorkItemType]:
    return list(WorkItemType)


def _binding_legal(wit: WorkItemType, workspace_id: str | None) -> bool:
    """Ground truth — workspace_id is None iff type is INITIATIVE."""
    return (workspace_id is None) == (wit == _initiative())


def _fresh_engine():
    tmp = tempfile.mkdtemp()
    return create_engine(f"sqlite:///{tmp}/t.db")


def _migrated_engine():
    engine = _fresh_engine()
    SQLModel.metadata.create_all(engine)
    apply_migrations(engine)
    mid = _migration_id()
    with engine.connect() as conn:
        recorded = {row[0] for row in conn.execute(text("SELECT id FROM schema_migrations"))}
    assert mid in recorded, f"{mid} not applied"
    return engine


def _count_tickets(session: Session) -> int:
    return len(session.exec(select(Ticket)).all())


# --- combinatorial seam ------------------------------------------------------


class TestWorkspaceBindingCombinatorial:
    """Exhaustive type × workspace_id matrix — catches a seam that only covers happy paths."""

    def test_every_type_id_pair_matches_ground_truth(self):
        ids: list[str | None] = [None, "", "ws-id"]
        for wit, wid in product(_all_types(), ids):
            should_pass = _binding_legal(wit, wid)
            if should_pass:
                _validate_workspace_binding(wit, wid)
            else:
                with pytest.raises(ValueError):
                    _validate_workspace_binding(wit, wid)

    def test_empty_string_is_not_none_for_initiative(self):
        """Mutation: `if not workspace_id` would accept '' for initiative — pin is None."""
        with pytest.raises(ValueError):
            _validate_workspace_binding(_initiative(), "")

    def test_empty_string_passes_non_initiative_under_exact_none_rule(self):
        """Spec uses `is None`, not truthiness — '' is not None, so non-init + '' is legal
        at the pure seam (FK may still reject later). Pins implementer against
        `if not workspace_id` which would reject this pair incorrectly."""
        _validate_workspace_binding(WorkItemType.TASK, "")


# --- whitespace / empty slug mutations on create -----------------------------


class TestCreateSlugMutations:
    """AC1/AC2 edges the design suite only covers with None / '' / 'loregarden'."""

    @pytest.mark.parametrize("slug", ["   ", "\t", "\n", "  \t  "])
    def test_whitespace_only_slug_rejected_for_non_initiative(self, db_session: Session, slug: str):
        before = _count_tickets(db_session)
        with pytest.raises(ValueError, match="(?i)workspace"):
            TicketService(db_session).create_ticket(
                workspace_slug=slug,
                title="Whitespace slug milestone",
                work_item_type=WorkItemType.MILESTONE,
            )
        assert _count_tickets(db_session) == before

    def test_empty_slug_creates_null_initiative(self, db_session: Session):
        """Explicit empty slug is not a supplied workspace — AC3 path."""
        initiative = _create_initiative(db_session, title="empty-slug-init", workspace_slug="")
        assert initiative.workspace_id is None

    @pytest.mark.parametrize("slug", ["   ", "\t"])
    def test_whitespace_slug_does_not_bind_initiative(self, db_session: Session, slug: str):
        """Whitespace must not resolve to a workspace row. Strip→empty succeeds with
        null workspace_id; unstripped miss still leaves no bound initiative row.
        # CHECKPOINT: treat whitespace like title.strip() — empty after strip ⇒ AC3.
        """
        before = _count_tickets(db_session)
        try:
            initiative = _create_initiative(
                db_session, title=f"ws-slug-{slug!r}", workspace_slug=slug
            )
        except ValueError:
            assert _count_tickets(db_session) == before
            return
        assert initiative.workspace_id is None

    def test_ac2_error_is_not_workspace_not_found_false_green(self, db_session: Session):
        """Baseline trap: today's create raises `Workspace not found: None` which
        already matches `(?i)workspace` — that is not validate_workspace_binding.
        Pin a binding-origin message so a lookup-order bug cannot keep AC2 green."""
        with pytest.raises(ValueError) as excinfo:
            TicketService(db_session).create_ticket(
                workspace_slug=None,
                title="Orphan feature",
                work_item_type=WorkItemType.FEATURE,
            )
        msg = str(excinfo.value).lower()
        assert "workspace not found" not in msg
        assert "workspace" in msg or "initiative" in msg or "binding" in msg

    @pytest.mark.parametrize(
        "wit",
        [
            WorkItemType.FEATURE,
            WorkItemType.CAPABILITY,
            WorkItemType.TASK,
            WorkItemType.BUG,
        ],
    )
    def test_ac2_all_non_initiative_types_need_workspace(
        self, db_session: Session, wit: WorkItemType
    ):
        """Design suite only asserts MILESTONE — a type-switch mutation can slip."""
        before = _count_tickets(db_session)
        with pytest.raises(ValueError, match="(?i)workspace"):
            TicketService(db_session).create_ticket(
                workspace_slug=None,
                title=f"Orphan {wit.value}",
                work_item_type=wit,
            )
        assert _count_tickets(db_session) == before


# --- AC3 skip mutations ------------------------------------------------------


class TestInitiativeCreateSkips:
    """AC3 — workspace assign_external_id / next_ticket_number must not run for initiatives."""

    def test_assign_external_id_not_called(self, db_session: Session):
        with patch("loregarden.services.ticket_service.assign_external_id") as assign_mock:
            initiative = _create_initiative(db_session, title="No assign")
        assign_mock.assert_not_called()
        assert initiative.workspace_id is None
        assert re.match(r"^init-[a-z0-9-]+-\d+$", initiative.external_id), initiative.external_id

    def test_spells_init_prefixed_id_from_global_counter(self, db_session: Session):
        """733 contract — system spells init-*; ticket_number equals trailing n."""
        initiative = _create_initiative(db_session, title="Init Scheme")
        assert re.match(r"^init-[a-z0-9-]+-\d+$", initiative.external_id), initiative.external_id
        assert initiative.ticket_number == int(initiative.external_id.rsplit("-", 1)[-1])
        assert initiative.ticket_number >= 1

    def test_supplied_external_id_becomes_legacy_not_canonical(self, db_session: Session):
        initiative = _create_initiative(db_session, title="Supplied", external_id="Keep-Case-Id")
        assert initiative.legacy_external_id == "Keep-Case-Id"
        assert re.match(r"^init-[a-z0-9-]+-\d+$", initiative.external_id), initiative.external_id
        assert initiative.external_id != "Keep-Case-Id"
        assert initiative.workspace_id is None


# --- AC5 move order / mutation -----------------------------------------------


class TestMoveRefuseOrderAndMutation:
    """R6 — refuse INITIATIVE before destination-equality short-circuit and assign loop."""

    def test_source_refuses_initiative_before_destination_equality(self):
        """AST order: a mutation that only refuses after `== destination.id` lets a
        future non-null initiative short-circuit as 'already in workspace'."""
        from loregarden.mcp import ticket_ops_tools as mod

        source = inspect.getsource(mod._move_ticket_workspace)
        init_pos = source.lower().find("initiative")
        eq_pos = source.find("destination.id")
        assert init_pos != -1, "_move_ticket_workspace must name INITIATIVE / initiative"
        assert eq_pos != -1, "destination-equality short-circuit missing"
        assert init_pos < eq_pos, (
            "INITIATIVE refuse must appear before destination-equality short-circuit (R6)"
        )

    def test_move_valueerror_not_integrityerror(self, db_session: Session):
        """If refuse is missing, CHECK may raise IntegrityError on commit — wrong surface."""
        existing = db_session.exec(select(Workspace).where(Workspace.slug == "elsewhere")).first()
        if existing is None:
            existing = Workspace(slug="elsewhere", name="Elsewhere", repo_path="/tmp/elsewhere")
            db_session.add(existing)
            db_session.commit()

        initiative = _create_initiative(db_session, title="Move surface")
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

    def test_move_preserves_revision_and_null(self, db_session: Session):
        existing = db_session.exec(select(Workspace).where(Workspace.slug == "elsewhere")).first()
        if existing is None:
            existing = Workspace(slug="elsewhere", name="Elsewhere", repo_path="/tmp/elsewhere")
            db_session.add(existing)
            db_session.commit()

        initiative = _create_initiative(db_session, title="Immutable move")
        before_rev = initiative.revision
        before_parent = initiative.parent_ticket_id
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
        assert initiative.revision == before_rev
        assert initiative.parent_ticket_id == before_parent


# --- AC4 UPDATE-path CHECK + service↔CHECK agreement -------------------------


class TestCheckConstraintMutations:
    """CHECK must catch writers that bypass TicketService (raw UPDATE / INSERT)."""

    def test_update_flip_milestone_to_initiative_keeps_workspace_rejected(self):
        engine = _migrated_engine()
        ws_id = "ws-flip"
        with Session(engine) as session:
            session.add(Workspace(id=ws_id, slug="flip", name="Flip", repo_path="/tmp/flip"))
            session.commit()
        with engine.begin() as conn:
            raw_ticket_insert(
                conn,
                ticket_id="flip-ms",
                external_id="flip-ms",
                workspace_id=ws_id,
                title="Flip me",
                work_item_type="milestone",
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    text("UPDATE tickets SET work_item_type='initiative' WHERE id='flip-ms'")
                )

    def test_update_null_workspace_on_task_rejected(self):
        engine = _migrated_engine()
        ws_id = "ws-nullify"
        with Session(engine) as session:
            session.add(
                Workspace(id=ws_id, slug="nullify", name="Nullify", repo_path="/tmp/nullify")
            )
            session.commit()
        with engine.begin() as conn:
            raw_ticket_insert(
                conn,
                ticket_id="nullify-task",
                external_id="nt",
                workspace_id=ws_id,
                title="Nullify me",
                work_item_type="task",
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(text("UPDATE tickets SET workspace_id=NULL WHERE id='nullify-task'"))

    def test_service_reject_and_check_agree_on_illegal_initiative(self, db_session: Session):
        """Cross-path: service ValueError and raw INSERT IntegrityError for the same pair."""
        before = _count_tickets(db_session)
        with pytest.raises(ValueError):
            _create_initiative(db_session, workspace_slug="loregarden", title="Svc reject")
        assert _count_tickets(db_session) == before

        engine = _migrated_engine()
        ws_id = "ws-agree"
        with Session(engine) as session:
            session.add(Workspace(id=ws_id, slug="agree", name="Agree", repo_path="/tmp/agree"))
            session.commit()
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                raw_ticket_insert(
                    conn,
                    ticket_id="agree-bad",
                    external_id="",
                    workspace_id=ws_id,
                    title="Agree bad",
                    work_item_type="initiative",
                )


# --- AC6 migration abort symmetry --------------------------------------------


class TestMigrationAbortSymmetry:
    """AC6 design only aborts on initiative+workspace; task+NULL is the other illegal."""

    def test_illegal_preexisting_task_null_aborts_migration(self):
        mid = _migration_id()
        migrate_fn = next((fn for migration_id, fn in M.MIGRATIONS if migration_id == mid), None)
        assert migrate_fn is not None

        engine = _fresh_engine()
        SQLModel.metadata.create_all(engine)
        try:
            with engine.begin() as conn:
                raw_ticket_insert(
                    conn,
                    ticket_id="bad-task",
                    external_id="bt",
                    workspace_id=None,
                    title="Pre-check task",
                    work_item_type="task",
                )
        except IntegrityError:
            pytest.skip("create_all already enforces CHECK — abort path needs old DDL")

        with pytest.raises((ValueError, RuntimeError), match="(?i)workspace|violat|task"):
            with engine.begin() as conn:
                migrate_fn(conn)

    def test_migration_body_does_not_update_workspace_id(self):
        """Source mutation: a silent UPDATE of initiatives would hide 731-era data."""
        mid = _migration_id()
        migrate_fn = next((fn for migration_id, fn in M.MIGRATIONS if migration_id == mid), None)
        assert migrate_fn is not None
        source = inspect.getsource(migrate_fn).lower()
        # Allow SELECT ... workspace_id; forbid UPDATE tickets SET workspace_id
        assert "update tickets" not in source.replace("\n", " ")
        assert "set workspace_id" not in source.replace("\n", " ")


# --- AC7 parent rule edges ---------------------------------------------------


class TestParentWorkspaceRuleAdversarial:
    """Null-initiative parent allowed; unequal non-null still rejected — all three writers."""

    def test_parent_equality_sites_allow_null_parent_workspace(self):
        """AST: bare `parent.workspace_id != …` rejects null parents — pin the R5 guard."""
        from loregarden.services import hierarchy_service as hs
        from loregarden.services import ticket_import_service as tis
        from loregarden.services.ticket_service import TicketService

        sources = {
            "TicketService._validated_parent": inspect.getsource(TicketService._validated_parent),
            "reparent_ticket": inspect.getsource(hs.reparent_ticket),
            "_resolve_parent_id": inspect.getsource(tis.TicketImportService._resolve_parent_id),
        }
        for label, source in sources.items():
            # R5: if parent.workspace_id is not None, it must equal the child's;
            # a bare `!=` without a null guard rejects null-workspace INITIATIVE parents.
            has_null_guard = (
                "workspace_id is not None" in source or "workspace_id is None" in source
            )
            assert has_null_guard, (
                f"{label} must guard null-workspace parents before equality (AC7 / R5)"
            )

    def test_reparent_then_create_child_under_null_initiative(self, db_session: Session):
        initiative = _create_initiative(db_session, title="Combo parent")
        ms_a = TicketService(db_session).create_ticket(
            workspace_slug="loregarden",
            title="First ms",
            work_item_type=WorkItemType.MILESTONE,
        )
        reparent_ticket(db_session, ms_a, initiative.id)
        db_session.commit()
        ms_b = TicketService(db_session).create_ticket(
            workspace_slug="loregarden",
            title="Second ms",
            work_item_type=WorkItemType.MILESTONE,
            parent_ticket_id=initiative.id,
        )
        assert ms_a.parent_ticket_id == initiative.id
        assert ms_b.parent_ticket_id == initiative.id
        assert initiative.workspace_id is None
        assert ms_a.workspace_id == ms_b.workspace_id

    def test_unequal_non_null_reparent_still_rejected(self, db_session: Session):
        other = Workspace(slug="adv-other", name="Adv Other", repo_path="/tmp/adv-other")
        db_session.add(other)
        db_session.commit()
        db_session.refresh(other)

        foreign = Ticket(
            external_id="adv-foreign-ms",
            workspace_id=other.id,
            title="Foreign ms",
            work_item_type=WorkItemType.MILESTONE,
        )
        db_session.add(foreign)
        local = TicketService(db_session).create_ticket(
            workspace_slug="loregarden",
            title="Local ms",
            work_item_type=WorkItemType.MILESTONE,
        )
        db_session.commit()
        with pytest.raises(ValueError, match="(?i)workspace|parent"):
            reparent_ticket(db_session, local, foreign.id)


# --- build_tree / writers ----------------------------------------------------


class TestNullWorkspaceFallout:
    def test_build_tree_solo_initiative_no_children(self, db_session: Session):
        initiative = _create_initiative(db_session, title="Solo tree")
        tree = build_tree(db_session, [initiative])
        assert len(tree) == 1
        assert tree[0].id == initiative.id
        # Null workspace → empty slug, not a crash / KeyError on None.
        assert tree[0].workspace_slug == ""

    def test_binding_not_folded_into_validate_parent_assignment(self):
        """Spec leave_alone: workspace binding must not live inside parent-type seam."""
        from loregarden.services import hierarchy_service as hs

        source = inspect.getsource(hs.validate_parent_assignment)
        tree = ast.parse(source)
        # Docstrings may mention workspace; the body must not call the binding seam
        # or branch on workspace_id.
        body_nodes = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                body_nodes.extend(node.body)
        for node in body_nodes:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                continue  # docstring
            text = ast.unparse(node)
            assert "validate_workspace_binding" not in text
            assert "workspace_id" not in text

    def test_create_ticket_signature_accepts_optional_slug(self):
        params = inspect.signature(TicketService.create_ticket).parameters
        assert "workspace_slug" in params
        ann = params["workspace_slug"].annotation
        assert ann == str | None or "None" in str(ann)


# --- MCP surface (727 still requires slug — must reject initiative) ----------


class TestMcpInitiativeCreateReject:
    def test_mcp_create_initiative_with_required_slug_rejects(self, db_session: Session):
        """Until 727, MCP always sends workspace_slug — AC1 must reject."""
        before = _count_tickets(db_session)
        with pytest.raises(ValueError, match="(?i)initiative|workspace"):
            execute_tool(
                db_session,
                "loregarden_create_ticket",
                normalize_tool_arguments(
                    "loregarden_create_ticket",
                    {
                        "workspace_slug": "loregarden",
                        "title": "MCP initiative",
                        "work_item_type": "initiative",
                    },
                ),
            )
        assert _count_tickets(db_session) == before
