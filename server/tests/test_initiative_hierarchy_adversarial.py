"""Adversarial / mutation coverage for INITIATIVE parent rules (lg-initiatives-cross-731).

Extends test_initiative_hierarchy.py into gaps the design suite leaves open:
combinatorial matrices, import short-circuit mutations, cross-path agreement,
sort-order twin-map drift, REST surfaces, and finalize nesting.

Expected red until implement lands WorkItemType.INITIATIVE +
validate_parent_assignment and deletes the unconditional milestone-parent
short-circuits. Do not attribute those reds to implement regressions.
"""

from __future__ import annotations

import ast
import inspect
from itertools import product
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from loregarden.models.domain import (
    VALID_HIERARCHY,
    WORKFLOW_WORK_ITEM_TYPES,
    Ticket,
    WorkItemType,
)
from loregarden.models.domain import enums as enums_mod
from loregarden.services.hierarchy_service import build_tree, reparent_ticket
from loregarden.services.subtree_auto_run import child_sort_key
from loregarden.services.ticket_service import TicketService
from sqlmodel import Session, select

# --- helpers -----------------------------------------------------------------


def _initiative() -> WorkItemType:
    try:
        return WorkItemType.INITIATIVE
    except AttributeError as exc:
        raise AssertionError("WorkItemType.INITIATIVE missing — R1 / AC1") from exc


def _validate_parent_assignment(child_type: WorkItemType, parent_type: WorkItemType | None) -> None:
    try:
        from loregarden.services.hierarchy_service import validate_parent_assignment
    except ImportError as exc:
        raise AssertionError("hierarchy_service.validate_parent_assignment missing — R2") from exc
    validate_parent_assignment(child_type, parent_type)


def _create(
    session: Session,
    *,
    title: str,
    work_item_type: WorkItemType,
    parent_ticket_id: str | None = None,
    workspace_slug: str = "loregarden",
) -> Ticket:
    return TicketService(session).create_ticket(
        workspace_slug=workspace_slug,
        title=title,
        work_item_type=work_item_type,
        parent_ticket_id=parent_ticket_id,
    )


def _all_types() -> list[WorkItemType]:
    """Every WorkItemType member, including INITIATIVE once it exists."""
    members = list(WorkItemType)
    initiative = getattr(WorkItemType, "INITIATIVE", None)
    if initiative is not None and initiative not in members:
        members.append(initiative)
    return members


def _ticket_stub(*, work_item_type: WorkItemType, external_id: str, priority: int = 3) -> Ticket:
    return Ticket(
        external_id=external_id,
        workspace_id="ws",
        title=external_id,
        work_item_type=work_item_type,
        priority=priority,
    )


# Allowed (child, parent|None) pairs after 731 — the ground-truth matrix.
def _allowed_assignment(child: WorkItemType, parent: WorkItemType | None) -> bool:
    initiative = _initiative()
    if parent is None:
        return child in {initiative, WorkItemType.MILESTONE}
    if child == WorkItemType.MILESTONE and parent == initiative:
        return True
    # Existing VALID_HIERARCHY pairs still hold for non-initiative parents.
    return child in VALID_HIERARCHY.get(parent, [])


# --- combinatorial seam matrix ----------------------------------------------


class TestParentAssignmentCombinatorial:
    """Exhaustive child×parent matrix — catches a seam that only covers happy paths."""

    def test_every_type_pair_matches_ground_truth(self):
        types = _all_types()
        for child, parent in product(types, [None, *types]):
            should_pass = _allowed_assignment(child, parent)
            if should_pass:
                _validate_parent_assignment(child, parent)
            else:
                with pytest.raises(ValueError):
                    _validate_parent_assignment(child, parent)

    def test_valid_hierarchy_initiative_has_no_extra_children(self):
        """Mutation: VALID_HIERARCHY[INITIATIVE] = [MILESTONE, FEATURE] would pass
        AC1's `== [MILESTONE]` only if someone weakens the assertion — pin length
        and membership separately so a list-extend mutation still fails."""
        children = VALID_HIERARCHY[_initiative()]
        assert len(children) == 1
        assert children[0] is WorkItemType.MILESTONE
        assert WorkItemType.FEATURE not in children
        assert _initiative() not in children


# --- import short-circuit mutations -----------------------------------------


class TestImportShortCircuitMutations:
    """Today's import path logs 'milestones cannot have a parent' and still creates
    a parentless milestone (HTTP 201). After the seam, that must become a hard
    reject with created_count==0 — otherwise AC9 agreement is a lie."""

    def test_import_milestone_under_feature_created_count_zero(
        self, client: TestClient, db_session: Session
    ):
        root = _create(db_session, title="Adv import root", work_item_type=WorkItemType.MILESTONE)
        feature = _create(
            db_session,
            title="Adv import feature",
            work_item_type=WorkItemType.FEATURE,
            parent_ticket_id=root.id,
        )
        before = len(db_session.exec(select(Ticket)).all())
        res = client.post(
            "/api/tickets/import",
            json={
                "workspace_slug": "loregarden",
                "tickets": [
                    {
                        "title": "Orphan-if-short-circuit",
                        "work_item_type": "milestone",
                        "parent_ticket_id": feature.id,
                    }
                ],
            },
        )
        assert res.status_code == 400, res.text
        body = res.json()
        assert body.get("created_count", -1) == 0 or "created_count" not in body
        after = len(db_session.exec(select(Ticket)).all())
        assert after == before, "reject path must not create a parentless milestone"

    def test_import_batch_initiative_then_milestone_via_parent_external_id(
        self, client: TestClient, db_session: Session
    ):
        """Batch import wires parent by external_id in the same payload — a path
        that bypasses parent_ticket_id and can skip TicketService pair checks if
        _resolve_parent_id still short-circuits milestones to None."""
        res = client.post(
            "/api/tickets/import",
            json={
                "workspace_slug": "loregarden",
                "tickets": [
                    {
                        "title": "Batch Initiative",
                        "work_item_type": "initiative",
                        "external_id": "adv-batch-init-01",
                    },
                    {
                        "title": "Batch Milestone Under Init",
                        "work_item_type": "milestone",
                        "external_id": "adv-batch-ms-01",
                        "parent_external_id": "adv-batch-init-01",
                    },
                ],
            },
        )
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["created_count"] == 2
        initiative = db_session.exec(
            select(Ticket).where(Ticket.external_id == "adv-batch-init-01")
        ).first()
        # Prefer external_id; fall back to legacy if assign_external_id remaps.
        if initiative is None:
            initiative = db_session.exec(
                select(Ticket).where(Ticket.legacy_external_id == "adv-batch-init-01")
            ).first()
        milestone = db_session.exec(
            select(Ticket).where(Ticket.external_id == "adv-batch-ms-01")
        ).first()
        if milestone is None:
            milestone = db_session.exec(
                select(Ticket).where(Ticket.legacy_external_id == "adv-batch-ms-01")
            ).first()
        assert initiative is not None and milestone is not None
        assert milestone.parent_ticket_id == initiative.id
        assert milestone.workspace_id == initiative.workspace_id

    def test_import_parentless_initiative(self, client: TestClient, db_session: Session):
        res = client.post(
            "/api/tickets/import",
            json={
                "workspace_slug": "loregarden",
                "tickets": [
                    {
                        "title": "Imported root initiative",
                        "work_item_type": "initiative",
                        "external_id": "adv-import-init-root",
                    }
                ],
            },
        )
        assert res.status_code == 201, res.text
        assert res.json()["created_count"] == 1

    def test_import_initiative_with_parent_rejected(self, client: TestClient, db_session: Session):
        milestone = _create(
            db_session, title="Adv import ms parent", work_item_type=WorkItemType.MILESTONE
        )
        before = len(db_session.exec(select(Ticket)).all())
        res = client.post(
            "/api/tickets/import",
            json={
                "workspace_slug": "loregarden",
                "tickets": [
                    {
                        "title": "Initiative under milestone",
                        "work_item_type": "initiative",
                        "parent_ticket_id": milestone.id,
                    }
                ],
            },
        )
        assert res.status_code == 400, res.text
        assert len(db_session.exec(select(Ticket)).all()) == before


# --- reparent gaps ----------------------------------------------------------


class TestReparentNonMilestoneUnderInitiative:
    """Design suite covers create-path AC3; reparent can still forget the seam."""

    @pytest.mark.parametrize(
        "child_type",
        [
            WorkItemType.FEATURE,
            WorkItemType.CAPABILITY,
            WorkItemType.TASK,
            WorkItemType.BUG,
        ],
    )
    def test_reparent_non_milestone_under_initiative_rejected(
        self, db_session: Session, child_type: WorkItemType
    ):
        initiative = _create(
            db_session,
            title=f"Adv reparent init {child_type.value}",
            work_item_type=_initiative(),
        )
        # Legal scaffolding so the child exists before the illegal move.
        root = _create(
            db_session,
            title=f"Adv scaffold root {child_type.value}",
            work_item_type=WorkItemType.MILESTONE,
        )
        if child_type == WorkItemType.FEATURE:
            child = _create(
                db_session,
                title="Adv feature",
                work_item_type=WorkItemType.FEATURE,
                parent_ticket_id=root.id,
            )
        elif child_type == WorkItemType.BUG:
            child = _create(
                db_session,
                title="Adv bug",
                work_item_type=WorkItemType.BUG,
                parent_ticket_id=root.id,
            )
        elif child_type == WorkItemType.CAPABILITY:
            feature = _create(
                db_session,
                title="Adv cap parent feature",
                work_item_type=WorkItemType.FEATURE,
                parent_ticket_id=root.id,
            )
            child = _create(
                db_session,
                title="Adv capability",
                work_item_type=WorkItemType.CAPABILITY,
                parent_ticket_id=feature.id,
            )
        else:
            feature = _create(
                db_session,
                title="Adv task feature",
                work_item_type=WorkItemType.FEATURE,
                parent_ticket_id=root.id,
            )
            capability = _create(
                db_session,
                title="Adv task capability",
                work_item_type=WorkItemType.CAPABILITY,
                parent_ticket_id=feature.id,
            )
            child = _create(
                db_session,
                title="Adv task",
                work_item_type=WorkItemType.TASK,
                parent_ticket_id=capability.id,
            )

        with pytest.raises(ValueError):
            reparent_ticket(db_session, child, initiative.id)

    def test_reparent_milestone_off_initiative_to_none(self, db_session: Session):
        initiative = _create(db_session, title="Adv detach init", work_item_type=_initiative())
        milestone = _create(
            db_session,
            title="Adv detach ms",
            work_item_type=WorkItemType.MILESTONE,
            parent_ticket_id=initiative.id,
        )
        reparent_ticket(db_session, milestone, None)
        db_session.commit()
        assert milestone.parent_ticket_id is None

    @pytest.mark.parametrize(
        "bad_parent_type",
        [
            WorkItemType.CAPABILITY,
            WorkItemType.TASK,
            WorkItemType.BUG,
        ],
    )
    def test_create_milestone_under_capability_task_bug_rejected(
        self, db_session: Session, bad_parent_type: WorkItemType
    ):
        """AC4 unit seam covers these parents; TicketService create path must too."""
        root = _create(db_session, title="Adv ac4 root", work_item_type=WorkItemType.MILESTONE)
        feature = _create(
            db_session,
            title="Adv ac4 feature",
            work_item_type=WorkItemType.FEATURE,
            parent_ticket_id=root.id,
        )
        if bad_parent_type == WorkItemType.CAPABILITY:
            parent = _create(
                db_session,
                title="Adv ac4 cap",
                work_item_type=WorkItemType.CAPABILITY,
                parent_ticket_id=feature.id,
            )
        elif bad_parent_type == WorkItemType.TASK:
            cap = _create(
                db_session,
                title="Adv ac4 task cap",
                work_item_type=WorkItemType.CAPABILITY,
                parent_ticket_id=feature.id,
            )
            parent = _create(
                db_session,
                title="Adv ac4 task",
                work_item_type=WorkItemType.TASK,
                parent_ticket_id=cap.id,
            )
        else:
            parent = _create(
                db_session,
                title="Adv ac4 bug",
                work_item_type=WorkItemType.BUG,
                parent_ticket_id=feature.id,
            )
        with pytest.raises(ValueError):
            _create(
                db_session,
                title="Adv ms under bad parent",
                work_item_type=WorkItemType.MILESTONE,
                parent_ticket_id=parent.id,
            )


# --- cross-path agreement ---------------------------------------------------


class TestCrossPathAgreement:
    """AC9 — create / reparent / import / finalize must share outcomes."""

    def test_milestone_under_initiative_agrees_across_surfaces(
        self, client: TestClient, db_session: Session
    ):
        initiative = _create(db_session, title="Agree init", work_item_type=_initiative())

        # create
        created = _create(
            db_session,
            title="Agree create ms",
            work_item_type=WorkItemType.MILESTONE,
            parent_ticket_id=initiative.id,
        )
        assert created.parent_ticket_id == initiative.id

        # reparent
        orphan = _create(db_session, title="Agree orphan ms", work_item_type=WorkItemType.MILESTONE)
        reparent_ticket(db_session, orphan, initiative.id)
        db_session.commit()
        assert orphan.parent_ticket_id == initiative.id

        # REST reparent
        rest_orphan = _create(
            db_session, title="Agree REST orphan", work_item_type=WorkItemType.MILESTONE
        )
        res = client.patch(
            f"/api/tickets/{rest_orphan.id}",
            json={"parent_ticket_id": initiative.id},
        )
        assert res.status_code == 200, res.text
        assert res.json()["parent_ticket_id"] == initiative.id

        # REST create
        res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Agree REST create ms",
                "work_item_type": "milestone",
                "parent_ticket_id": initiative.id,
            },
        )
        assert res.status_code == 201, res.text
        assert res.json()["parent_ticket_id"] == initiative.id

    def test_feature_under_initiative_rejected_across_surfaces(
        self, client: TestClient, db_session: Session
    ):
        initiative = _create(db_session, title="Agree reject init", work_item_type=_initiative())
        root = _create(db_session, title="Agree reject root", work_item_type=WorkItemType.MILESTONE)
        feature = _create(
            db_session,
            title="Agree reject feature",
            work_item_type=WorkItemType.FEATURE,
            parent_ticket_id=root.id,
        )

        with pytest.raises(ValueError):
            _create(
                db_session,
                title="Agree create feat under init",
                work_item_type=WorkItemType.FEATURE,
                parent_ticket_id=initiative.id,
            )
        with pytest.raises(ValueError):
            reparent_ticket(db_session, feature, initiative.id)

        res = client.patch(
            f"/api/tickets/{feature.id}",
            json={"parent_ticket_id": initiative.id},
        )
        assert res.status_code == 400, res.text

        res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Agree REST feat under init",
                "work_item_type": "feature",
                "parent_ticket_id": initiative.id,
            },
        )
        assert res.status_code == 400, res.text


# --- finalize nesting mutations ---------------------------------------------


class TestFinalizeNestingMutations:
    def test_finalize_initiative_nested_under_milestone_rejected(
        self, client: TestClient, db_session: Session
    ):
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "adv-fin-ms-nest",
                        "title": "Milestone nesting init",
                        "work_item_type": "milestone",
                        "children": [
                            {
                                "external_id": "adv-fin-init-nested",
                                "title": "Illegal nested initiative",
                                "work_item_type": "initiative",
                                "children": [],
                            }
                        ],
                    }
                ],
            },
        )
        assert res.status_code == 400
        assert (
            db_session.exec(
                select(Ticket).where(Ticket.legacy_external_id == "adv-fin-ms-nest")
            ).first()
            is None
        )

    def test_finalize_deep_initiative_milestone_feature(
        self, client: TestClient, db_session: Session
    ):
        """Legal deep tree — finalize root allow-list must include INITIATIVE and
        must not force milestone-to-None when the parent is an initiative."""
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "adv-fin-deep-init",
                        "title": "Deep Initiative",
                        "work_item_type": "initiative",
                        "children": [
                            {
                                "external_id": "adv-fin-deep-ms",
                                "title": "Deep Milestone",
                                "work_item_type": "milestone",
                                "children": [
                                    {
                                        "external_id": "adv-fin-deep-feat",
                                        "title": "Deep Feature",
                                        "work_item_type": "feature",
                                        "children": [],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
        )
        assert res.status_code == 201, res.text
        assert res.json()["total_created"] == 3
        initiative = db_session.exec(
            select(Ticket).where(Ticket.legacy_external_id == "adv-fin-deep-init")
        ).first()
        milestone = db_session.exec(
            select(Ticket).where(Ticket.legacy_external_id == "adv-fin-deep-ms")
        ).first()
        feature = db_session.exec(
            select(Ticket).where(Ticket.legacy_external_id == "adv-fin-deep-feat")
        ).first()
        assert initiative is not None and milestone is not None and feature is not None
        assert milestone.parent_ticket_id == initiative.id
        assert feature.parent_ticket_id == milestone.id


# --- sort-order twin-map drift ----------------------------------------------


class TestSortOrderTwinMap:
    """build_tree and child_sort_key keep separate type_order dicts — adding
    INITIATIVE to one and forgetting the other sinks initiatives to default 9."""

    def test_initiative_rank_immediately_before_milestone(self):
        initiative = _ticket_stub(work_item_type=_initiative(), external_id="z-init")
        milestone = _ticket_stub(work_item_type=WorkItemType.MILESTONE, external_id="a-ms")
        feature = _ticket_stub(work_item_type=WorkItemType.FEATURE, external_id="a-feat")
        bug = _ticket_stub(work_item_type=WorkItemType.BUG, external_id="a-bug")

        assert child_sort_key(initiative) < child_sort_key(milestone)
        assert child_sort_key(milestone) < child_sort_key(feature)
        # Mutation: INITIATIVE missing → default 9, sorts after BUG.
        assert child_sort_key(initiative) < child_sort_key(bug)

    def test_build_tree_and_child_sort_key_agree_on_relative_order(self, db_session: Session):
        initiative = _create(db_session, title="Twin init", work_item_type=_initiative())
        milestone = _create(db_session, title="Twin ms", work_item_type=WorkItemType.MILESTONE)
        initiative.priority = 3
        milestone.priority = 3
        db_session.add_all([initiative, milestone])
        db_session.commit()

        tree = build_tree(db_session, [initiative, milestone])
        tree_order = [n.work_item_type for n in tree]
        key_order = sorted([initiative, milestone], key=child_sort_key)
        assert tree_order == [t.work_item_type for t in key_order]
        assert tree_order[0] == _initiative()


# --- WORKFLOW frozenset construction mutations ------------------------------


class TestWorkflowFrozensetConstruction:
    def test_assignment_is_not_any_enum_iteration(self):
        """AC5 pins `frozenset(WorkItemType)`; also catch list/comprehension forms
        that would auto-include INITIATIVE the same way."""
        source = Path(enums_mod.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "WORKFLOW_WORK_ITEM_TYPES":
                    text = ast.unparse(node.value).replace(" ", "")
                    forbidden = (
                        "frozenset(WorkItemType)",
                        "frozenset(list(WorkItemType))",
                        "frozenset({*WorkItemType})",
                        "frozenset([tfor tin WorkItemType])",
                        "frozenset({tfor tin WorkItemType})",
                    )
                    for needle in forbidden:
                        assert needle not in text, (
                            f"WORKFLOW_WORK_ITEM_TYPES must be an explicit five-type "
                            f"frozenset; construction {text!r} auto-includes new enum members"
                        )
                    assert _initiative() not in WORKFLOW_WORK_ITEM_TYPES
                    assert len(WORKFLOW_WORK_ITEM_TYPES) == 5
                    return
        pytest.fail("WORKFLOW_WORK_ITEM_TYPES assignment not found")


# --- call-path message scan (broader) ---------------------------------------


class TestNoMilestoneParentShortCircuitAnywhereInServer:
    """AC8 names four call paths; a leftover raise in another server module still
    breaks agreement. Scan every .py under loregarden/ for the unconditional
    message strings."""

    def test_server_package_has_no_unconditional_milestone_parent_message(self):
        needles = (
            "Milestones cannot have a parent",
            "milestones cannot have a parent",
        )
        package_root = Path(inspect.getfile(TicketService)).resolve().parents[1]
        offenders: list[str] = []
        for path in package_root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for needle in needles:
                if needle in text:
                    offenders.append(f"{path.relative_to(package_root)}: {needle!r}")
        assert not offenders, (
            "Unconditional milestone-parent short-circuit still present:\n" + "\n".join(offenders)
        )


# --- order dependency / forward parent refs ---------------------------------


class TestImportOrderDependency:
    """_import_sort_key currently ranks MILESTONE before unknown types (default 99).
    INITIATIVE will land in that default bucket until the map is updated, so a
    batch that lists the milestone *before* the initiative exercises the
    multi-pass deferral path — a single-pass + short-circuit combo silently
    orphans the milestone today."""

    def test_import_milestone_before_initiative_in_payload(
        self, client: TestClient, db_session: Session
    ):
        res = client.post(
            "/api/tickets/import",
            json={
                "workspace_slug": "loregarden",
                "tickets": [
                    {
                        "title": "Child first (order trap)",
                        "work_item_type": "milestone",
                        "external_id": "adv-order-ms-first",
                        "parent_external_id": "adv-order-init-second",
                    },
                    {
                        "title": "Parent second (order trap)",
                        "work_item_type": "initiative",
                        "external_id": "adv-order-init-second",
                    },
                ],
            },
        )
        assert res.status_code == 201, res.text
        assert res.json()["created_count"] == 2
        initiative = (
            db_session.exec(
                select(Ticket).where(Ticket.external_id == "adv-order-init-second")
            ).first()
            or db_session.exec(
                select(Ticket).where(Ticket.legacy_external_id == "adv-order-init-second")
            ).first()
        )
        milestone = (
            db_session.exec(
                select(Ticket).where(Ticket.external_id == "adv-order-ms-first")
            ).first()
            or db_session.exec(
                select(Ticket).where(Ticket.legacy_external_id == "adv-order-ms-first")
            ).first()
        )
        assert initiative is not None and milestone is not None
        assert milestone.parent_ticket_id == initiative.id


# --- finalize root allow-list -----------------------------------------------


class TestFinalizeInitiativeRootAllowList:
    """finalize create_item_recursive currently allow-lists only
    MILESTONE/FEATURE/CAPABILITY as roots. INITIATIVE alone must be legal."""

    def test_finalize_sole_initiative_root(self, client: TestClient, db_session: Session):
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "adv-fin-sole-init",
                        "title": "Sole Initiative Root",
                        "work_item_type": "initiative",
                        "children": [],
                    }
                ],
            },
        )
        assert res.status_code == 201, res.text
        assert res.json()["total_created"] == 1
        initiative = db_session.exec(
            select(Ticket).where(Ticket.legacy_external_id == "adv-fin-sole-init")
        ).first()
        assert initiative is not None
        assert initiative.parent_ticket_id is None
        assert initiative.work_item_type == _initiative()


# --- caller seam wiring (mutation: helper exists but unused) ----------------


class TestCallerSeamWiring:
    """A helper that nothing calls satisfies ImportError checks while leaving
    the short-circuits in place. Pin that every create/reparent/import/finalize
    call path names validate_parent_assignment."""

    _MODULES = (
        "loregarden.services.hierarchy_service",
        "loregarden.services.ticket_service",
        "loregarden.services.ticket_import_service",
        "loregarden.api.tickets",
    )

    def test_call_paths_reference_validate_parent_assignment(self):
        missing: list[str] = []
        for mod_name in self._MODULES:
            mod = __import__(mod_name, fromlist=["*"])
            source = inspect.getsource(mod)
            if "validate_parent_assignment" not in source:
                missing.append(mod_name)
        assert not missing, (
            "Call paths must invoke validate_parent_assignment (not only "
            "validate_parent_child / local short-circuits):\n" + "\n".join(missing)
        )


# --- MCP create surface -----------------------------------------------------


class TestMcpInitiativeSurfaces:
    """MCP create_ticket must accept initiative and enforce the same pairs —
    schema enum rejection of 'initiative' is an AC1/AC6 miss on this surface."""

    def test_mcp_create_parentless_initiative(self, db_session: Session):
        from loregarden.mcp.tools import execute_tool, normalize_tool_arguments

        args = normalize_tool_arguments(
            "loregarden_create_ticket",
            {
                "workspace_slug": "loregarden",
                "title": "MCP parentless initiative",
                "work_item_type": "initiative",
            },
        )
        import json

        result = json.loads(execute_tool(db_session, "loregarden_create_ticket", args))
        stored = db_session.get(Ticket, result["id"])
        assert stored is not None
        assert stored.parent_ticket_id is None
        assert stored.work_item_type == _initiative()

    def test_mcp_create_initiative_with_parent_rejected(self, db_session: Session):
        from loregarden.mcp.tools import execute_tool, normalize_tool_arguments

        milestone = _create(
            db_session, title="MCP init reject parent", work_item_type=WorkItemType.MILESTONE
        )
        args = normalize_tool_arguments(
            "loregarden_create_ticket",
            {
                "workspace_slug": "loregarden",
                "title": "MCP initiative with parent",
                "work_item_type": "initiative",
                "parent": milestone.id,
            },
        )
        with pytest.raises(ValueError):
            execute_tool(db_session, "loregarden_create_ticket", args)


# --- invalid / corrupt type strings -----------------------------------------


class TestCorruptWorkItemTypeInputs:
    def test_rest_create_rejects_wrong_case_initiative(
        self, client: TestClient, db_session: Session
    ):
        """Enum is lowercase 'initiative'; casing mutations must 422, not coerce."""
        before = len(db_session.exec(select(Ticket)).all())
        res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Wrong case Initiative",
                "work_item_type": "Initiative",
            },
        )
        assert res.status_code == 422, res.text
        assert len(db_session.exec(select(Ticket)).all()) == before

    def test_rest_create_rejects_empty_work_item_type(
        self, client: TestClient, db_session: Session
    ):
        res = client.post(
            "/api/tickets",
            json={
                "workspace_slug": "loregarden",
                "title": "Empty type",
                "work_item_type": "",
            },
        )
        assert res.status_code == 422, res.text


# --- reparent initiative under initiative -----------------------------------


class TestInitiativeSelfParent:
    def test_reparent_initiative_under_initiative_rejected(self, db_session: Session):
        a = _create(db_session, title="Init A", work_item_type=_initiative())
        b = _create(db_session, title="Init B", work_item_type=_initiative())
        with pytest.raises(ValueError):
            reparent_ticket(db_session, a, b.id)

    def test_create_initiative_under_initiative_rejected(self, db_session: Session):
        parent = _create(db_session, title="Init parent", work_item_type=_initiative())
        with pytest.raises(ValueError):
            _create(
                db_session,
                title="Init child",
                work_item_type=_initiative(),
                parent_ticket_id=parent.id,
            )
