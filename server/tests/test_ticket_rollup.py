"""A parent's state follows its children.

Nothing moved a parent except orchestrating the parent itself, and the queue now
runs one ticket per lane — so children routinely finish without their parent ever
running, and a feature whose every child is done stayed "in progress" forever.
"""

from __future__ import annotations

import inspect
import json
import typing
from unittest.mock import patch

import pytest
from loregarden.core.event_bus import event_bus
from loregarden.models.domain import (
    DomainEvent,
    EventType,
    Ticket,
    TicketState,
    UpdateTicketRequest,
    WorkItemType,
    Workspace,
)
from loregarden.services import ticket_rollup
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.ticket_rollup import (
    derive_parent_state,
    has_children,
    reconcile_all_parents,
    reconcile_ancestors,
)
from sqlalchemy import text
from sqlmodel import Session, select


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session):
    ws = Workspace(slug="proj", name="proj", repo_path=".")
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="workspace_b")
def workspace_b_fixture(session):
    """Second Workspace row for cross-workspace initiative children (R2–R5)."""
    ws = Workspace(slug="proj-b", name="proj-b", repo_path=".")
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _initiative_type() -> WorkItemType:
    """R1 — INITIATIVE must exist after merge of lg-initiatives-cross-733; no stubs."""
    try:
        return WorkItemType.INITIATIVE
    except AttributeError as exc:
        raise AssertionError(
            "WorkItemType.INITIATIVE missing — merge loregarden/lg-initiatives-cross-733 first"
        ) from exc


def _ticket(
    session: Session,
    workspace: Workspace | None,
    code: str,
    *,
    state: TicketState = TicketState.BACKLOG,
    parent: Ticket | None = None,
    work_item_type: WorkItemType = WorkItemType.TASK,
    locked: bool = False,
) -> Ticket:
    """Create a ticket. Pass workspace=None → workspace_id=None (initiatives only)."""
    ticket = Ticket(
        external_id=code,
        workspace_id=workspace.id if workspace is not None else None,
        title=code,
        state=state,
        work_item_type=work_item_type,
        parent_ticket_id=parent.id if parent else None,
        state_locked=locked,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _cross_workspace_initiative(
    session: Session,
    workspace_a: Workspace,
    workspace_b: Workspace,
    *,
    initiative_state: TicketState = TicketState.IN_PROGRESS,
    milestone_a_state: TicketState = TicketState.BACKLOG,
    milestone_b_state: TicketState = TicketState.BACKLOG,
    locked: bool = False,
) -> tuple[Ticket, Ticket, Ticket]:
    """Initiative (NULL workspace) with one milestone in each of two Workspace rows."""
    initiative = _ticket(
        session,
        None,
        "I-1",
        state=initiative_state,
        work_item_type=_initiative_type(),
        locked=locked,
    )
    ma = _ticket(
        session,
        workspace_a,
        "MA-1",
        state=milestone_a_state,
        parent=initiative,
        work_item_type=WorkItemType.MILESTONE,
    )
    mb = _ticket(
        session,
        workspace_b,
        "MB-1",
        state=milestone_b_state,
        parent=initiative,
        work_item_type=WorkItemType.MILESTONE,
    )
    return initiative, ma, mb


# ---- the rule itself ---------------------------------------------------


@pytest.mark.parametrize(
    ("children", "expected"),
    [
        ([], None),
        ([TicketState.DONE, TicketState.DONE], TicketState.DONE),
        # wont_do is a resolution, not a gap.
        ([TicketState.DONE, TicketState.WONT_DO], TicketState.DONE),
        ([TicketState.BACKLOG, TicketState.BACKLOG], TicketState.BACKLOG),
        ([TicketState.DONE, TicketState.BACKLOG], TicketState.IN_PROGRESS),
        ([TicketState.IN_PROGRESS], TicketState.IN_PROGRESS),
        # Blocked outranks "mostly finished" — it is the urgent fact about a tree.
        ([TicketState.DONE, TicketState.BLOCKED], TicketState.BLOCKED),
        ([TicketState.BLOCKED, TicketState.BACKLOG], TicketState.BLOCKED),
    ],
)
def test_derive_parent_state(children, expected):
    assert derive_parent_state(children) is expected


# ---- pushing upward ----------------------------------------------------


def test_finishing_the_last_child_finishes_the_parent(session, workspace):
    parent = _ticket(
        session,
        workspace,
        "F-1",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.FEATURE,
    )
    _ticket(session, workspace, "T-1", state=TicketState.DONE, parent=parent)
    last = _ticket(session, workspace, "T-2", state=TicketState.DONE, parent=parent)

    changed = reconcile_ancestors(session, last)

    assert [t.id for t in changed] == [parent.id]
    session.refresh(parent)
    assert parent.state == TicketState.DONE
    assert parent.last_updated_by == "rollup"


def test_rollup_climbs_the_whole_chain(session, workspace):
    """A task finishing can complete its capability, feature and milestone."""
    milestone = _ticket(
        session,
        workspace,
        "M-1",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.MILESTONE,
    )
    feature = _ticket(
        session,
        workspace,
        "F-1",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.FEATURE,
        parent=milestone,
    )
    capability = _ticket(
        session,
        workspace,
        "C-1",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.CAPABILITY,
        parent=feature,
    )
    task = _ticket(session, workspace, "T-1", state=TicketState.DONE, parent=capability)

    changed = reconcile_ancestors(session, task)

    assert {t.external_id for t in changed} == {"C-1", "F-1", "M-1"}
    for node in (capability, feature, milestone):
        session.refresh(node)
        assert node.state == TicketState.DONE


def test_rollup_stops_at_the_first_ancestor_that_does_not_move(session, workspace):
    milestone = _ticket(
        session,
        workspace,
        "M-1",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.MILESTONE,
    )
    _ticket(session, workspace, "F-other", state=TicketState.BACKLOG, parent=milestone)
    feature = _ticket(
        session,
        workspace,
        "F-1",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.FEATURE,
        parent=milestone,
    )
    task = _ticket(session, workspace, "T-1", state=TicketState.DONE, parent=feature)

    changed = reconcile_ancestors(session, task)

    # The feature completes; the milestone still has an open sibling feature.
    assert [t.external_id for t in changed] == ["F-1"]
    session.refresh(milestone)
    assert milestone.state == TicketState.IN_PROGRESS


def test_a_blocked_child_blocks_the_parent(session, workspace):
    parent = _ticket(
        session,
        workspace,
        "F-1",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.FEATURE,
    )
    _ticket(session, workspace, "T-1", state=TicketState.DONE, parent=parent)
    stuck = _ticket(session, workspace, "T-2", state=TicketState.BLOCKED, parent=parent)

    reconcile_ancestors(session, stuck)

    session.refresh(parent)
    assert parent.state == TicketState.BLOCKED


def test_reopening_a_child_reopens_the_parent(session, workspace):
    parent = _ticket(
        session, workspace, "F-1", state=TicketState.DONE, work_item_type=WorkItemType.FEATURE
    )
    child = _ticket(session, workspace, "T-1", state=TicketState.IN_PROGRESS, parent=parent)

    reconcile_ancestors(session, child)

    session.refresh(parent)
    assert parent.state == TicketState.IN_PROGRESS


# ---- what it must not touch --------------------------------------------


def test_a_locked_parent_is_left_alone(session, workspace):
    """`state_locked` is how an operator says "I decided this"."""
    parent = _ticket(
        session,
        workspace,
        "F-1",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.FEATURE,
        locked=True,
    )
    child = _ticket(session, workspace, "T-1", state=TicketState.DONE, parent=parent)

    assert reconcile_ancestors(session, child) == []
    session.refresh(parent)
    assert parent.state == TicketState.IN_PROGRESS


def test_an_abandoned_parent_is_left_alone(session, workspace):
    parent = _ticket(
        session, workspace, "F-1", state=TicketState.WONT_DO, work_item_type=WorkItemType.FEATURE
    )
    child = _ticket(session, workspace, "T-1", state=TicketState.DONE, parent=parent)

    assert reconcile_ancestors(session, child) == []
    session.refresh(parent)
    assert parent.state == TicketState.WONT_DO


def test_a_childless_ticket_has_no_parent_to_move(session, workspace):
    orphan = _ticket(session, workspace, "T-1", state=TicketState.DONE)
    assert reconcile_ancestors(session, orphan) == []


def test_a_parent_cycle_does_not_hang(session, workspace):
    """The schema permits what the hierarchy rules forbid."""
    a = _ticket(session, workspace, "A", state=TicketState.IN_PROGRESS)
    b = _ticket(session, workspace, "B", state=TicketState.IN_PROGRESS, parent=a)
    a.parent_ticket_id = b.id
    session.add(a)
    session.commit()

    reconcile_ancestors(session, b)  # must return rather than loop


# ---- the startup sweep -------------------------------------------------


def test_the_sweep_corrects_stale_parents(session, workspace):
    stale = _ticket(
        session,
        workspace,
        "F-1",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.FEATURE,
    )
    _ticket(session, workspace, "T-1", state=TicketState.DONE, parent=stale)
    _ticket(session, workspace, "T-2", state=TicketState.DONE, parent=stale)

    changed = reconcile_all_parents(session)

    assert [t.external_id for t in changed] == ["F-1"]
    session.refresh(stale)
    assert stale.state == TicketState.DONE


def test_the_sweep_settles_a_whole_tree_in_one_pass(session, workspace):
    """Deepest first, or a corrected capability would not reach its milestone
    until the next boot."""
    milestone = _ticket(
        session,
        workspace,
        "M-1",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.MILESTONE,
    )
    feature = _ticket(
        session,
        workspace,
        "F-1",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.FEATURE,
        parent=milestone,
    )
    capability = _ticket(
        session,
        workspace,
        "C-1",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.CAPABILITY,
        parent=feature,
    )
    _ticket(session, workspace, "T-1", state=TicketState.DONE, parent=capability)

    reconcile_all_parents(session)

    for node in (capability, feature, milestone):
        session.refresh(node)
        assert node.state == TicketState.DONE, f"{node.external_id} did not settle in one pass"


def test_the_sweep_is_idempotent(session, workspace):
    parent = _ticket(
        session,
        workspace,
        "F-1",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.FEATURE,
    )
    _ticket(session, workspace, "T-1", state=TicketState.DONE, parent=parent)

    assert len(reconcile_all_parents(session)) == 1
    assert reconcile_all_parents(session) == []


# ---- cross-workspace initiative rollup (lg-initiatives-cross-754) ------


def test_r1_initiative_null_workspace_smoke_two_milestones(session, workspace, workspace_b):
    """R1: INITIATIVE + Optional workspace_id + binding CHECK; smoke insert commits."""
    initiative_type = _initiative_type()
    assert initiative_type.value == "initiative"

    # Optional[str] / str | None — not a bare required str.
    hint = typing.get_type_hints(Ticket)["workspace_id"]
    assert type(None) in typing.get_args(hint), (
        f"Ticket.workspace_id must be Optional; got {hint!r}"
    )

    ddl = (
        session.connection()
        .execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"))
        .scalar_one()
    )
    compact = "".join(ddl.split())
    assert "ck_tickets_workspace_binding" in ddl
    assert "(workspace_idISNULL)=(work_item_type='initiative')" in compact

    initiative, ma, mb = _cross_workspace_initiative(session, workspace, workspace_b)
    assert initiative.workspace_id is None
    assert initiative.work_item_type == initiative_type
    assert ma.workspace_id == workspace.id
    assert mb.workspace_id == workspace_b.id
    assert ma.workspace_id != mb.workspace_id
    # Prove the rows survived commit under the FK + CHECK (not merely constructed).
    assert session.get(Ticket, initiative.id) is not None
    assert session.get(Ticket, ma.id) is not None
    assert session.get(Ticket, mb.id) is not None


def test_r2a_half_closed_cross_workspace_stays_in_progress(session, workspace, workspace_b):
    """R2a: one workspace DONE, the other still open → initiative stays IN_PROGRESS."""
    initiative, ma, mb = _cross_workspace_initiative(
        session,
        workspace,
        workspace_b,
        milestone_a_state=TicketState.DONE,
        milestone_b_state=TicketState.IN_PROGRESS,
    )

    reconcile_ancestors(session, ma)

    session.refresh(initiative)
    assert initiative.state == TicketState.IN_PROGRESS
    assert initiative.workspace_id is None


def test_r2b_both_done_cross_workspace_rolls_initiative_to_done(session, workspace, workspace_b):
    """R2b: both milestones DONE → reconcile_ancestors sets initiative DONE via rollup."""
    initiative, ma, mb = _cross_workspace_initiative(
        session,
        workspace,
        workspace_b,
        milestone_a_state=TicketState.DONE,
        milestone_b_state=TicketState.DONE,
    )

    changed = reconcile_ancestors(session, mb)

    assert initiative.id in {t.id for t in changed}
    session.refresh(initiative)
    assert initiative.state == TicketState.DONE
    assert initiative.last_updated_by == "rollup"
    assert initiative.workspace_id is None


def test_r2c_reopen_child_reopens_cross_workspace_initiative(session, workspace, workspace_b):
    """R2c: after both DONE, reopening any child returns initiative to IN_PROGRESS."""
    initiative, ma, mb = _cross_workspace_initiative(
        session,
        workspace,
        workspace_b,
        milestone_a_state=TicketState.DONE,
        milestone_b_state=TicketState.DONE,
    )
    reconcile_ancestors(session, mb)
    session.refresh(initiative)
    assert initiative.state == TicketState.DONE

    ma.state = TicketState.IN_PROGRESS
    session.add(ma)
    session.commit()

    reconcile_ancestors(session, ma)

    session.refresh(initiative)
    assert initiative.state == TicketState.IN_PROGRESS
    assert initiative.workspace_id is None


def test_r3_sweep_lands_stale_cross_workspace_initiative(session, workspace, workspace_b):
    """R3: reconcile_all_parents corrects a stale initiative with DONE children in two WSs."""
    initiative, _ma, _mb = _cross_workspace_initiative(
        session,
        workspace,
        workspace_b,
        initiative_state=TicketState.IN_PROGRESS,
        milestone_a_state=TicketState.DONE,
        milestone_b_state=TicketState.DONE,
    )

    changed = reconcile_all_parents(session)

    assert initiative.id in {t.id for t in changed}
    session.refresh(initiative)
    assert initiative.state == TicketState.DONE
    assert initiative.workspace_id is None


def test_r4a_locked_initiative_refuses_rollup(session, workspace, workspace_b):
    """R4a: state_locked initiative with DONE children across workspaces is untouched."""
    initiative, _ma, mb = _cross_workspace_initiative(
        session,
        workspace,
        workspace_b,
        initiative_state=TicketState.IN_PROGRESS,
        milestone_a_state=TicketState.DONE,
        milestone_b_state=TicketState.DONE,
        locked=True,
    )

    assert reconcile_ancestors(session, mb) == []
    session.refresh(initiative)
    assert initiative.state == TicketState.IN_PROGRESS
    assert initiative.workspace_id is None


def test_r4b_wont_do_initiative_refuses_rollup(session, workspace, workspace_b):
    """R4b: unlocked WONT_DO initiative stays WONT_DO despite DONE children."""
    initiative, _ma, mb = _cross_workspace_initiative(
        session,
        workspace,
        workspace_b,
        initiative_state=TicketState.WONT_DO,
        milestone_a_state=TicketState.DONE,
        milestone_b_state=TicketState.DONE,
    )

    assert reconcile_ancestors(session, mb) == []
    session.refresh(initiative)
    assert initiative.state == TicketState.WONT_DO


def test_r4c_parked_initiative_refuses_rollup(session, workspace, workspace_b):
    """R4c: unlocked PARKED initiative stays PARKED despite DONE children."""
    initiative, _ma, mb = _cross_workspace_initiative(
        session,
        workspace,
        workspace_b,
        initiative_state=TicketState.PARKED,
        milestone_a_state=TicketState.DONE,
        milestone_b_state=TicketState.DONE,
    )

    assert reconcile_ancestors(session, mb) == []
    session.refresh(initiative)
    assert initiative.state == TicketState.PARKED


def test_r5_rollup_child_parent_queries_have_no_workspace_id_predicate():
    """R5: rollup walks by parent_ticket_id only — no workspace_id SQL predicate.

    Behavioral cover: R2b would go red under a hypothetical
    child.workspace_id == parent.workspace_id filter (NULL parent never matches).
    """
    sources = {
        name: inspect.getsource(getattr(ticket_rollup, name))
        for name in (
            "_child_states",
            "has_children",
            "reconcile_ancestors",
            "reconcile_all_parents",
        )
    }
    for name, src in sources.items():
        # Strip comments and string literals loosely: reject any where() predicate
        # that names workspace_id. parent_ticket_id walks must remain.
        assert "workspace_id" not in src, f"{name} must not filter on workspace_id; got:\n{src}"
        if name in ("_child_states", "has_children"):
            assert "parent_ticket_id" in src


def _child_states_filtered_by_parent_workspace(
    session: Session, parent_id: str
) -> list[TicketState]:
    """Adversarial mutation: same-workspace child filter (the regression R5 forbids).

    Equating child.workspace_id to parent.workspace_id drops every child of a
    NULL-workspace initiative — SQL NULL never equals a concrete workspace_id —
    so both DONE milestones vanish from the tally and the parent never rolls up.
    """
    parent = session.get(Ticket, parent_id)
    assert parent is not None
    return list(
        session.exec(
            select(Ticket.state).where(
                Ticket.parent_ticket_id == parent_id,
                Ticket.workspace_id == parent.workspace_id,
            )
        ).all()
    )


def _has_children_filtered_by_parent_workspace(session: Session, ticket_id: str) -> bool:
    """Adversarial mutation: has_children gated on matching workspace_id."""
    parent = session.get(Ticket, ticket_id)
    assert parent is not None
    return (
        session.exec(
            select(Ticket.id)
            .where(
                Ticket.parent_ticket_id == ticket_id,
                Ticket.workspace_id == parent.workspace_id,
            )
            .limit(1)
        ).first()
        is not None
    )


def test_r5_adversarial_same_workspace_filter_strands_push_rollup(session, workspace, workspace_b):
    """R5 adversarial: child.workspace_id == parent.workspace_id leaves I stuck IN_PROGRESS.

    Under the real (unfiltered) walk, R2b requires DONE. This mutation proves the
    failure mode the suite must catch — if production ever grows that predicate,
    R2b goes red for the same reason this assertion holds under the patch.
    """
    initiative, _ma, mb = _cross_workspace_initiative(
        session,
        workspace,
        workspace_b,
        milestone_a_state=TicketState.DONE,
        milestone_b_state=TicketState.DONE,
    )
    assert initiative.workspace_id is None
    assert mb.workspace_id != initiative.workspace_id

    with patch.object(ticket_rollup, "_child_states", _child_states_filtered_by_parent_workspace):
        changed = ticket_rollup.reconcile_ancestors(session, mb)

    assert initiative.id not in {t.id for t in changed}
    session.refresh(initiative)
    assert initiative.state == TicketState.IN_PROGRESS, (
        "same-workspace child filter must strand a NULL-workspace initiative; "
        "if this ever passes under the mutation, R2b no longer detects the regression"
    )
    assert initiative.workspace_id is None


def test_r5_adversarial_same_workspace_filter_strands_sweep(session, workspace, workspace_b):
    """R5 adversarial (R3 path): filtered _child_states also strands reconcile_all_parents."""
    initiative, _ma, _mb = _cross_workspace_initiative(
        session,
        workspace,
        workspace_b,
        initiative_state=TicketState.IN_PROGRESS,
        milestone_a_state=TicketState.DONE,
        milestone_b_state=TicketState.DONE,
    )

    with patch.object(ticket_rollup, "_child_states", _child_states_filtered_by_parent_workspace):
        changed = ticket_rollup.reconcile_all_parents(session)

    assert initiative.id not in {t.id for t in changed}
    session.refresh(initiative)
    assert initiative.state == TicketState.IN_PROGRESS
    assert initiative.workspace_id is None


def test_r5_adversarial_has_children_workspace_filter_hides_cross_ws_kids(
    session, workspace, workspace_b
):
    """R5 adversarial: has_children must not equate workspace_id — else NULL parents look childless."""
    initiative, _ma, _mb = _cross_workspace_initiative(session, workspace, workspace_b)

    assert has_children(session, initiative.id) is True

    with patch.object(ticket_rollup, "has_children", _has_children_filtered_by_parent_workspace):
        assert ticket_rollup.has_children(session, initiative.id) is False, (
            "workspace-eq has_children would hide every child of a NULL-workspace initiative"
        )


def test_r2b_order_independent_reconcile_from_either_done_child(session, workspace, workspace_b):
    """Adversarial order: push from MA (ws1) must also land I at DONE when both are DONE.

    R2b only exercises reconcile from MB. A walk that accidentally keyed off the
    triggering child's workspace could pass R2b and still fail the other direction.
    """
    initiative, ma, mb = _cross_workspace_initiative(
        session,
        workspace,
        workspace_b,
        milestone_a_state=TicketState.DONE,
        milestone_b_state=TicketState.DONE,
    )
    assert ma.workspace_id != mb.workspace_id

    changed = reconcile_ancestors(session, ma)

    assert initiative.id in {t.id for t in changed}
    session.refresh(initiative)
    assert initiative.state == TicketState.DONE
    assert initiative.last_updated_by == "rollup"
    assert initiative.workspace_id is None


def test_cross_workspace_done_and_wont_do_still_resolves_initiative(
    session, workspace, workspace_b
):
    """Combinatorial: DONE + WONT_DO across two workspaces is fully resolved → DONE."""
    initiative, ma, mb = _cross_workspace_initiative(
        session,
        workspace,
        workspace_b,
        milestone_a_state=TicketState.DONE,
        milestone_b_state=TicketState.WONT_DO,
    )

    reconcile_ancestors(session, ma)

    session.refresh(initiative)
    assert initiative.state == TicketState.DONE
    assert initiative.workspace_id is None


def _ticket_state_changed(session: Session, ticket_id: str) -> list[DomainEvent]:
    """TicketStateChanged rows for one ticket, newest first. Not filtered by workspace."""
    return event_bus.list_recent(
        session,
        ticket_id=ticket_id,
        types=(EventType.TICKET_STATE_CHANGED,),
    )


def test_manual_blocked_task_announces_initiative_and_feature(session, workspace, workspace_b):
    """R4.1 / R1.1 / R2.1 / R2.2 / R2.3: a workspace-B manual edit announces both ancestors.

    Tree, all unlocked: initiative (workspace_id None, IN_PROGRESS) with a feature
    in workspace B and a milestone in workspace A, both IN_PROGRESS. The edited
    ticket is a task under the feature, IN_PROGRESS. BLOCKED is not terminal, so
    the manual path reaches reconcile_ancestors without a git lookup. The feature
    and the initiative both leave IN_PROGRESS. The milestone does not move.
    The leaf row stays {state, manual: true} and is not either ancestor row.
    """
    initiative = _ticket(
        session,
        None,
        "I-evt",
        state=TicketState.IN_PROGRESS,
        work_item_type=_initiative_type(),
    )
    feature = _ticket(
        session,
        workspace_b,
        "F-evt",
        state=TicketState.IN_PROGRESS,
        parent=initiative,
        work_item_type=WorkItemType.FEATURE,
    )
    milestone = _ticket(
        session,
        workspace,
        "M-evt",
        state=TicketState.IN_PROGRESS,
        parent=initiative,
        work_item_type=WorkItemType.MILESTONE,
    )
    task = _ticket(
        session,
        workspace_b,
        "T-evt",
        state=TicketState.IN_PROGRESS,
        parent=feature,
        work_item_type=WorkItemType.TASK,
    )

    OrchestrationService(session).update_ticket_manual(
        task, UpdateTicketRequest(state=TicketState.BLOCKED)
    )

    session.refresh(task)
    session.refresh(feature)
    session.refresh(initiative)
    session.refresh(milestone)
    assert task.state == TicketState.BLOCKED
    assert feature.state == TicketState.BLOCKED
    assert initiative.state == TicketState.BLOCKED
    assert milestone.state == TicketState.IN_PROGRESS

    initiative_rows = _ticket_state_changed(session, initiative.id)
    feature_rows = _ticket_state_changed(session, feature.id)
    missing = [
        name
        for name, rows in (("initiative", initiative_rows), ("feature", feature_rows))
        if not rows
    ]
    assert not missing, f"missing TicketStateChanged for {missing}"
    assert len(initiative_rows) == 1
    assert len(feature_rows) == 1

    initiative_event = initiative_rows[0]
    assert initiative_event.type == EventType.TICKET_STATE_CHANGED
    assert initiative_event.ticket_id == initiative.id
    assert initiative_event.workspace_id is None
    assert json.loads(initiative_event.payload_json) == {
        "state": TicketState.BLOCKED.value,
        "actor": "rollup",
    }

    feature_event = feature_rows[0]
    assert feature_event.type == EventType.TICKET_STATE_CHANGED
    assert feature_event.ticket_id == feature.id
    assert feature_event.workspace_id == workspace_b.id
    assert json.loads(feature_event.payload_json) == {
        "state": TicketState.BLOCKED.value,
        "actor": "rollup",
    }

    # SQL equality: a NULL workspace_id does not match workspace B.
    watched = event_bus.list_recent(session, workspace_id=workspace_b.id)
    assert initiative.id not in {row.ticket_id for row in watched}
    assert feature.id in {row.ticket_id for row in watched}

    assert _ticket_state_changed(session, milestone.id) == []

    leaf_rows = _ticket_state_changed(session, task.id)
    assert len(leaf_rows) == 1
    assert leaf_rows[0].workspace_id == workspace_b.id
    assert json.loads(leaf_rows[0].payload_json) == {
        "state": TicketState.BLOCKED.value,
        "manual": True,
    }


def test_reconcile_all_parents_writes_no_ticket_state_changed(session, workspace, workspace_b):
    """R3.1 / R4.2 / R2.4: the boot sweep corrects parents and publishes nothing.

    Deepest first: a DONE task pulls its feature to DONE, then that feature plus
    a DONE milestone in the other workspace pull the initiative to DONE. Neither
    parent gets a TicketStateChanged row. ticket_rollup does not call event_hub.
    """
    initiative = _ticket(
        session,
        None,
        "I-sweep",
        state=TicketState.IN_PROGRESS,
        work_item_type=_initiative_type(),
    )
    feature = _ticket(
        session,
        workspace_b,
        "F-sweep",
        state=TicketState.IN_PROGRESS,
        parent=initiative,
        work_item_type=WorkItemType.FEATURE,
    )
    _ticket(
        session,
        workspace_b,
        "T-sweep",
        state=TicketState.DONE,
        parent=feature,
        work_item_type=WorkItemType.TASK,
    )
    _ticket(
        session,
        workspace,
        "M-sweep",
        state=TicketState.DONE,
        parent=initiative,
        work_item_type=WorkItemType.MILESTONE,
    )

    changed = reconcile_all_parents(session)

    changed_ids = {parent.id for parent in changed}
    assert feature.id in changed_ids
    assert initiative.id in changed_ids
    session.refresh(feature)
    session.refresh(initiative)
    assert feature.state == TicketState.DONE
    assert initiative.state == TicketState.DONE
    assert _ticket_state_changed(session, feature.id) == []
    assert _ticket_state_changed(session, initiative.id) == []
    assert event_bus.list_recent(session, types=(EventType.TICKET_STATE_CHANGED,)) == []
    assert "event_hub" not in inspect.getsource(ticket_rollup)


def _assert_one_rollup(session: Session, ticket: Ticket, workspace_id: str | None) -> None:
    """Exactly one TicketStateChanged, payload `{state, actor: rollup}`, workspace passed through."""
    rows = _ticket_state_changed(session, ticket.id)
    assert len(rows) == 1, f"{ticket.external_id} has {len(rows)} TicketStateChanged rows"
    event = rows[0]
    assert event.type == EventType.TICKET_STATE_CHANGED
    assert event.ticket_id == ticket.id
    assert event.workspace_id == workspace_id
    assert json.loads(event.payload_json) == {
        "state": ticket.state.value,
        "actor": "rollup",
    }


def test_reconcile_ancestors_announces_each_changed_parent_once(session, workspace, workspace_b):
    """R1.1 / R1.2 / R2.1 / R2.2 on the function itself, not only the manual caller.

    Publishing from ticket_manual_edit would turn the manual-path test green and
    leave this red. A second walk returns an empty changed list and must not
    write a second row. The milestone is not on the walk and stays silent.
    """
    initiative = _ticket(
        session,
        None,
        "I-direct",
        state=TicketState.IN_PROGRESS,
        work_item_type=_initiative_type(),
    )
    feature = _ticket(
        session,
        workspace_b,
        "F-direct",
        state=TicketState.IN_PROGRESS,
        parent=initiative,
        work_item_type=WorkItemType.FEATURE,
    )
    milestone = _ticket(
        session,
        workspace,
        "M-direct",
        state=TicketState.IN_PROGRESS,
        parent=initiative,
        work_item_type=WorkItemType.MILESTONE,
    )
    task = _ticket(
        session,
        workspace_b,
        "T-direct",
        state=TicketState.BLOCKED,
        parent=feature,
        work_item_type=WorkItemType.TASK,
    )

    changed = reconcile_ancestors(session, task)

    assert [node.external_id for node in changed] == ["F-direct", "I-direct"]
    session.refresh(feature)
    session.refresh(initiative)
    session.refresh(milestone)
    assert feature.state == TicketState.BLOCKED
    assert initiative.state == TicketState.BLOCKED
    assert milestone.state == TicketState.IN_PROGRESS
    _assert_one_rollup(session, initiative, None)
    _assert_one_rollup(session, feature, workspace_b.id)
    assert _ticket_state_changed(session, milestone.id) == []
    assert _ticket_state_changed(session, task.id) == []

    watched = event_bus.list_recent(session, workspace_id=workspace_b.id)
    watched_ids = {row.ticket_id for row in watched}
    assert initiative.id not in watched_ids
    assert feature.id in watched_ids

    assert reconcile_ancestors(session, task) == []
    _assert_one_rollup(session, initiative, None)
    _assert_one_rollup(session, feature, workspace_b.id)


def test_a_locked_ancestor_is_not_announced(session, workspace_b):
    """R1.1: a parent that does not move is not in changed and gets no row.

    The feature moves. The initiative is locked, so the walk stops there.
    An implementation that announces every ancestor it visits writes an
    initiative row this assertion rejects.
    """
    initiative = _ticket(
        session,
        None,
        "I-locked",
        state=TicketState.IN_PROGRESS,
        work_item_type=_initiative_type(),
        locked=True,
    )
    feature = _ticket(
        session,
        workspace_b,
        "F-locked",
        state=TicketState.IN_PROGRESS,
        parent=initiative,
        work_item_type=WorkItemType.FEATURE,
    )
    task = _ticket(
        session,
        workspace_b,
        "T-locked",
        state=TicketState.BLOCKED,
        parent=feature,
        work_item_type=WorkItemType.TASK,
    )

    changed = reconcile_ancestors(session, task)

    assert [node.external_id for node in changed] == ["F-locked"]
    session.refresh(feature)
    session.refresh(initiative)
    assert feature.state == TicketState.BLOCKED
    assert initiative.state == TicketState.IN_PROGRESS
    assert initiative.state_locked is True
    _assert_one_rollup(session, feature, workspace_b.id)
    assert _ticket_state_changed(session, initiative.id) == []
    assert _ticket_state_changed(session, task.id) == []


def test_an_empty_ancestor_walk_writes_no_rollup_event(session, workspace_b):
    """R1.2: changed is empty, so nothing is announced — including the parent that blocked the walk."""
    feature = _ticket(
        session,
        workspace_b,
        "F-still",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.FEATURE,
        locked=True,
    )
    task = _ticket(
        session,
        workspace_b,
        "T-still",
        state=TicketState.BLOCKED,
        parent=feature,
        work_item_type=WorkItemType.TASK,
    )

    assert reconcile_ancestors(session, task) == []
    session.refresh(feature)
    assert feature.state == TicketState.IN_PROGRESS
    assert _ticket_state_changed(session, feature.id) == []
    assert _ticket_state_changed(session, task.id) == []
    assert event_bus.list_recent(session, types=(EventType.TICKET_STATE_CHANGED,)) == []


def test_a_later_ancestor_failure_does_not_commit_the_earlier_rollup(session, workspace_b):
    """R1: the chain commits once, after the walk.

    EventBus.publish commits. A helper call inside the loop would leave the
    feature BLOCKED after rollback. The commit and the announce both belong
    after the loop, so this failure leaves the feature IN_PROGRESS and silent.
    """
    initiative = _ticket(
        session,
        None,
        "I-split",
        state=TicketState.IN_PROGRESS,
        work_item_type=_initiative_type(),
    )
    feature = _ticket(
        session,
        workspace_b,
        "F-split",
        state=TicketState.IN_PROGRESS,
        parent=initiative,
        work_item_type=WorkItemType.FEATURE,
    )
    task = _ticket(
        session,
        workspace_b,
        "T-split",
        state=TicketState.BLOCKED,
        parent=feature,
        work_item_type=WorkItemType.TASK,
    )
    real = ticket_rollup.reconcile_parent
    calls = {"n": 0}

    def fail_on_the_second(db: Session, parent: Ticket) -> bool:
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("second ancestor")
        return real(db, parent)

    # CHECKPOINT: a raise on the second ancestor stands in for a mid-walk publish.
    with patch.object(ticket_rollup, "reconcile_parent", fail_on_the_second):
        with pytest.raises(RuntimeError, match="second ancestor"):
            reconcile_ancestors(session, task)

    session.rollback()
    session.refresh(feature)
    session.refresh(initiative)
    assert feature.state == TicketState.IN_PROGRESS
    assert initiative.state == TicketState.IN_PROGRESS
    assert calls["n"] == 2
    assert event_bus.list_recent(session, types=(EventType.TICKET_STATE_CHANGED,)) == []
