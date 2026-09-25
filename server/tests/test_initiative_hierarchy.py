"""INITIATIVE work-item type and hierarchy parent-assignment rules (lg-initiatives-cross-731).

Contracts for ACs 1–9. Updated by sibling 732: initiatives are created with
workspace_id=None; a null-workspace INITIATIVE may parent a workspace-bound
MILESTONE (parent.workspace_id equality only when parent is non-null).
"""

from __future__ import annotations

import inspect

import pytest
from fastapi.testclient import TestClient
from loregarden.models.domain import (
    VALID_HIERARCHY,
    WORKFLOW_WORK_ITEM_TYPES,
    Ticket,
    WorkItemType,
)
from loregarden.services.hierarchy_service import build_tree, reparent_ticket
from loregarden.services.subtree_auto_run import child_sort_key
from loregarden.services.ticket_service import TicketService
from sqlmodel import Session, select
from tests.domain_assignments import domain_assignment_source

# --- helpers -----------------------------------------------------------------


def _initiative() -> WorkItemType:
    """AC1 — fail with a clear assertion until WorkItemType.INITIATIVE exists."""
    try:
        return WorkItemType.INITIATIVE
    except AttributeError as exc:
        raise AssertionError(
            "WorkItemType.INITIATIVE is missing (value 'initiative') — R1 / AC1"
        ) from exc


def _validate_parent_assignment(child_type: WorkItemType, parent_type: WorkItemType | None) -> None:
    """AC2 seam — fail clearly until hierarchy_service.validate_parent_assignment lands."""
    try:
        from loregarden.services.hierarchy_service import validate_parent_assignment
    except ImportError as exc:
        raise AssertionError(
            "hierarchy_service.validate_parent_assignment is missing — R2 / AC2–AC6"
        ) from exc
    validate_parent_assignment(child_type, parent_type)


def _create(
    session: Session,
    *,
    title: str,
    work_item_type: WorkItemType,
    parent_ticket_id: str | None = None,
    workspace_slug: str | None = "loregarden",
) -> Ticket:
    # 732 — initiatives bind to no workspace; a non-empty slug is rejected (AC1).
    if work_item_type == _initiative():
        workspace_slug = None
    return TicketService(session).create_ticket(
        workspace_slug=workspace_slug,
        title=title,
        work_item_type=work_item_type,
        parent_ticket_id=parent_ticket_id,
    )


def _ticket_stub(
    *,
    work_item_type: WorkItemType,
    external_id: str,
    priority: int = 3,
    workspace_id: str = "ws",
) -> Ticket:
    return Ticket(
        external_id=external_id,
        workspace_id=workspace_id,
        title=external_id,
        work_item_type=work_item_type,
        priority=priority,
    )


# --- AC1 / AC5 vocabulary ----------------------------------------------------


class TestInitiativeVocabulary:
    """AC1, AC5 — enum + VALID_HIERARCHY + WORKFLOW_WORK_ITEM_TYPES."""

    def test_initiative_enum_value(self):
        assert _initiative().value == "initiative"

    def test_valid_hierarchy_initiative_allows_only_milestone(self):
        assert VALID_HIERARCHY[_initiative()] == [WorkItemType.MILESTONE]

    def test_workflow_work_item_types_excludes_initiative_and_stays_five(self):
        expected = frozenset(
            {
                WorkItemType.MILESTONE,
                WorkItemType.FEATURE,
                WorkItemType.CAPABILITY,
                WorkItemType.TASK,
                WorkItemType.BUG,
            }
        )
        assert WORKFLOW_WORK_ITEM_TYPES == expected
        assert _initiative() not in WORKFLOW_WORK_ITEM_TYPES
        assert len(WORKFLOW_WORK_ITEM_TYPES) == 5

    def test_workflow_work_item_types_is_not_frozenset_of_enum(self):
        """AC5 — must not be `frozenset(WorkItemType)` (would auto-include INITIATIVE)."""
        text = domain_assignment_source("WORKFLOW_WORK_ITEM_TYPES")
        assert "frozenset(WorkItemType)" not in text.replace(" ", ""), (
            "WORKFLOW_WORK_ITEM_TYPES must be an explicit five-type frozenset, "
            f"not frozenset(WorkItemType); got {text!r}"
        )


# --- AC2/3/4/6 unit seam: validate_parent_assignment -------------------------


class TestValidateParentAssignment:
    """AC2–AC4, AC6 via the hierarchy_service pure helper (R2)."""

    def test_parentless_initiative_ok(self):
        _validate_parent_assignment(_initiative(), None)

    def test_parentless_milestone_ok(self):
        _validate_parent_assignment(WorkItemType.MILESTONE, None)

    def test_milestone_under_initiative_ok(self):
        _validate_parent_assignment(WorkItemType.MILESTONE, _initiative())

    @pytest.mark.parametrize(
        "child",
        [
            WorkItemType.FEATURE,
            WorkItemType.CAPABILITY,
            WorkItemType.TASK,
            WorkItemType.BUG,
        ],
    )
    def test_non_milestone_under_initiative_rejected(self, child: WorkItemType):
        with pytest.raises(ValueError):
            _validate_parent_assignment(child, _initiative())

    @pytest.mark.parametrize(
        "parent",
        [
            WorkItemType.FEATURE,
            WorkItemType.CAPABILITY,
            WorkItemType.TASK,
            WorkItemType.BUG,
            WorkItemType.MILESTONE,
        ],
    )
    def test_milestone_under_non_initiative_rejected(self, parent: WorkItemType):
        with pytest.raises(ValueError):
            _validate_parent_assignment(WorkItemType.MILESTONE, parent)

    @pytest.mark.parametrize(
        "parent_name",
        [
            "INITIATIVE",
            "MILESTONE",
            "FEATURE",
            "CAPABILITY",
            "TASK",
            "BUG",
        ],
    )
    def test_initiative_with_any_parent_rejected(self, parent_name: str):
        parent = _initiative() if parent_name == "INITIATIVE" else WorkItemType[parent_name]
        with pytest.raises(ValueError):
            _validate_parent_assignment(_initiative(), parent)

    def test_feature_still_requires_parent(self):
        with pytest.raises(ValueError, match="requires a parent"):
            _validate_parent_assignment(WorkItemType.FEATURE, None)


# --- AC2/3/4/6 TicketService create ------------------------------------------


class TestTicketServiceInitiativeParents:
    """Create path — null-workspace INITIATIVE may parent a bound MILESTONE (732)."""

    def test_create_parentless_initiative(self, db_session: Session):
        initiative = _create(db_session, title="Root initiative", work_item_type=_initiative())
        assert initiative.parent_ticket_id is None
        assert initiative.work_item_type == _initiative()
        assert initiative.workspace_id is None

    def test_create_initiative_with_parent_rejected(self, db_session: Session):
        milestone = _create(
            db_session, title="Plain milestone", work_item_type=WorkItemType.MILESTONE
        )
        with pytest.raises(ValueError):
            _create(
                db_session,
                title="Initiative with parent",
                work_item_type=_initiative(),
                parent_ticket_id=milestone.id,
            )

    def test_create_milestone_under_initiative_same_workspace(self, db_session: Session):
        initiative = _create(db_session, title="Init for milestone", work_item_type=_initiative())
        milestone = _create(
            db_session,
            title="Milestone under init",
            work_item_type=WorkItemType.MILESTONE,
            parent_ticket_id=initiative.id,
        )
        assert milestone.parent_ticket_id == initiative.id
        assert initiative.workspace_id is None
        assert milestone.workspace_id is not None

    @pytest.mark.parametrize(
        "child_type",
        [
            WorkItemType.FEATURE,
            WorkItemType.CAPABILITY,
            WorkItemType.TASK,
            WorkItemType.BUG,
        ],
    )
    def test_create_non_milestone_under_initiative_rejected(
        self, db_session: Session, child_type: WorkItemType
    ):
        initiative = _create(
            db_session,
            title=f"Init reject {child_type.value}",
            work_item_type=_initiative(),
        )
        with pytest.raises(ValueError):
            _create(
                db_session,
                title=f"Bad {child_type.value}",
                work_item_type=child_type,
                parent_ticket_id=initiative.id,
            )

    def test_create_milestone_under_feature_still_rejected(self, db_session: Session):
        milestone = _create(db_session, title="Root m", work_item_type=WorkItemType.MILESTONE)
        feature = _create(
            db_session,
            title="Feature parent",
            work_item_type=WorkItemType.FEATURE,
            parent_ticket_id=milestone.id,
        )
        with pytest.raises(ValueError):
            _create(
                db_session,
                title="Milestone under feature",
                work_item_type=WorkItemType.MILESTONE,
                parent_ticket_id=feature.id,
            )


# --- AC2/4/6/9 reparent ------------------------------------------------------


class TestReparentInitiativeRules:
    def test_reparent_milestone_under_initiative(self, db_session: Session):
        initiative = _create(db_session, title="Reparent init", work_item_type=_initiative())
        milestone = _create(
            db_session, title="Orphan milestone", work_item_type=WorkItemType.MILESTONE
        )
        reparent_ticket(db_session, milestone, initiative.id)
        db_session.commit()
        assert milestone.parent_ticket_id == initiative.id

    def test_reparent_milestone_under_feature_rejected(self, db_session: Session):
        root = _create(db_session, title="Root for reparent", work_item_type=WorkItemType.MILESTONE)
        feature = _create(
            db_session,
            title="Feature for reparent",
            work_item_type=WorkItemType.FEATURE,
            parent_ticket_id=root.id,
        )
        other = _create(db_session, title="Other milestone", work_item_type=WorkItemType.MILESTONE)
        with pytest.raises(ValueError):
            reparent_ticket(db_session, other, feature.id)

    def test_reparent_initiative_to_none(self, db_session: Session):
        initiative = _create(db_session, title="Init clear parent", work_item_type=_initiative())
        reparent_ticket(db_session, initiative, None)
        db_session.commit()
        assert initiative.parent_ticket_id is None

    def test_reparent_initiative_under_milestone_rejected(self, db_session: Session):
        initiative = _create(db_session, title="Init cannot hang", work_item_type=_initiative())
        milestone = _create(
            db_session, title="Cannot parent init", work_item_type=WorkItemType.MILESTONE
        )
        with pytest.raises(ValueError):
            reparent_ticket(db_session, initiative, milestone.id)


# --- AC9 import --------------------------------------------------------------


class TestImportInitiativeAgreement:
    def test_import_milestone_under_initiative(self, client: TestClient, db_session: Session):
        initiative = _create(db_session, title="Import init parent", work_item_type=_initiative())
        res = client.post(
            "/api/tickets/import",
            json={
                "workspace_slug": "loregarden",
                "tickets": [
                    {
                        "title": "Imported milestone under init",
                        "work_item_type": "milestone",
                        "parent_ticket_id": initiative.id,
                    }
                ],
            },
        )
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["created_count"] == 1
        child = db_session.get(Ticket, body["ticket_ids"][0])
        assert child is not None
        assert child.parent_ticket_id == initiative.id
        assert child.work_item_type == WorkItemType.MILESTONE

    def test_import_feature_under_initiative_rejected(
        self, client: TestClient, db_session: Session
    ):
        initiative = _create(
            db_session, title="Import init reject feature", work_item_type=_initiative()
        )
        res = client.post(
            "/api/tickets/import",
            json={
                "workspace_slug": "loregarden",
                "tickets": [
                    {
                        "title": "Imported feature under init",
                        "work_item_type": "feature",
                        "parent_ticket_id": initiative.id,
                    }
                ],
            },
        )
        assert res.status_code == 400
        assert body_mentions_reject(res.json())

    def test_import_milestone_under_feature_still_rejected(
        self, client: TestClient, db_session: Session
    ):
        root = _create(db_session, title="Import root m", work_item_type=WorkItemType.MILESTONE)
        feature = _create(
            db_session,
            title="Import feature parent",
            work_item_type=WorkItemType.FEATURE,
            parent_ticket_id=root.id,
        )
        res = client.post(
            "/api/tickets/import",
            json={
                "workspace_slug": "loregarden",
                "tickets": [
                    {
                        "title": "Imported milestone under feature",
                        "work_item_type": "milestone",
                        "parent_ticket_id": feature.id,
                    }
                ],
            },
        )
        assert res.status_code == 400


def body_mentions_reject(payload: dict) -> bool:
    detail = payload.get("detail", payload.get("errors", ""))
    text = detail if isinstance(detail, str) else str(detail)
    return bool(text)


# --- AC9 finalize ------------------------------------------------------------


class TestFinalizeInitiativeAgreement:
    def test_finalize_initiative_with_milestone_child(
        self, client: TestClient, db_session: Session
    ):
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "fin-init-01",
                        "title": "Finalize Initiative",
                        "work_item_type": "initiative",
                        "children": [
                            {
                                "external_id": "fin-ms-under-init-01",
                                "title": "Finalize Milestone Under Init",
                                "work_item_type": "milestone",
                                "children": [],
                            }
                        ],
                    }
                ],
            },
        )
        assert res.status_code == 201, res.text
        assert res.json()["total_created"] == 2
        initiative = db_session.exec(
            select(Ticket).where(Ticket.legacy_external_id == "fin-init-01")
        ).first()
        milestone = db_session.exec(
            select(Ticket).where(Ticket.legacy_external_id == "fin-ms-under-init-01")
        ).first()
        assert initiative is not None
        assert milestone is not None
        assert initiative.work_item_type == _initiative()
        assert milestone.parent_ticket_id == initiative.id
        assert initiative.workspace_id is None
        assert milestone.workspace_id is not None

    def test_finalize_initiative_with_feature_child_rejected(
        self, client: TestClient, db_session: Session
    ):
        res = client.post(
            "/api/tickets/finalize-hierarchy",
            json={
                "workspace_slug": "loregarden",
                "hierarchy": [
                    {
                        "external_id": "fin-init-bad-feat",
                        "title": "Bad Initiative",
                        "work_item_type": "initiative",
                        "children": [
                            {
                                "external_id": "fin-feat-under-init",
                                "title": "Feature under init",
                                "work_item_type": "feature",
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
                select(Ticket).where(Ticket.legacy_external_id == "fin-init-bad-feat")
            ).first()
            is None
        )


# --- R4 sort order -----------------------------------------------------------


class TestInitiativeSortOrder:
    def test_child_sort_key_initiative_before_milestone(self):
        initiative = _ticket_stub(work_item_type=_initiative(), external_id="a-init", priority=3)
        milestone = _ticket_stub(
            work_item_type=WorkItemType.MILESTONE, external_id="a-ms", priority=3
        )
        assert child_sort_key(initiative) < child_sort_key(milestone)

    def test_build_tree_orders_initiative_before_milestone(self, db_session: Session):
        initiative = _create(db_session, title="Sort init", work_item_type=_initiative())
        milestone = _create(
            db_session, title="Sort milestone", work_item_type=WorkItemType.MILESTONE
        )
        # Same priority so type_order decides among roots.
        initiative.priority = 3
        milestone.priority = 3
        db_session.add(initiative)
        db_session.add(milestone)
        db_session.commit()

        tree = build_tree(db_session, [initiative, milestone])
        types = [n.work_item_type for n in tree]
        assert types.index(_initiative()) < types.index(WorkItemType.MILESTONE)


# --- AC8: no unconditional milestone-parent short-circuit --------------------


class TestNoUnconditionalMilestoneParentReject:
    """AC8 — type legality only from hierarchy_service parent-assignment + VALID_HIERARCHY."""

    _CALL_PATH_MODULES = (
        "loregarden.services.ticket_service",
        "loregarden.services.hierarchy_service",
        "loregarden.services.ticket_import_service",
        "loregarden.api.tickets",
    )

    def test_call_paths_drop_unconditional_milestone_parent_message(self):
        needles = (
            "Milestones cannot have a parent",
            "milestones cannot have a parent",
        )
        offenders: list[str] = []
        for mod_name in self._CALL_PATH_MODULES:
            mod = __import__(mod_name, fromlist=["*"])
            source = inspect.getsource(mod)
            for needle in needles:
                if needle in source:
                    offenders.append(f"{mod_name}: {needle!r}")
        assert not offenders, (
            "Unconditional milestone-parent short-circuit still present:\n" + "\n".join(offenders)
        )
