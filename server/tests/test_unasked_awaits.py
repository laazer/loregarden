"""A stage awaiting a person has a pending approval asking that person.

blob-mesh-surface-152/153/154 sat at `script_review/awaiting` from 2026-10-02
with no approval row, run or orchestration ever written: the manual edit set
the status, and Work items listed them under "Needs me" with nothing in the
inbox to act on. `services.unasked_awaits` blocks the rows left that way and
refuses the manual spelling; these prove each writer, and that every function
writing `StageStatus.AWAITING` says how it raises its approval.

Tickets are built through `TicketService`, so each carries the workflow
instance a real ticket has.
"""

from __future__ import annotations

import ast
import functools
import json
from collections.abc import Callable
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    StageStatus,
    Ticket,
    TicketState,
    UpdateTicketRequest,
    WorkflowInstance,
)
from loregarden.services import orchestration as orchestration_module
from loregarden.services.invariant_registry import AWAITING_STAGES_ARE_ASKED
from loregarden.services.invariants import run_invariant
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.unasked_awaits import UNASKED_POINTER, unasked_awaits
from loregarden.services.workflow_service import resolve_ticket_stages
from loregarden.services.workflow_state import set_stage_status
from sqlmodel import Session, select
from tests.factories import make_task_in_tree


def _instance(session: Session, ticket: Ticket) -> WorkflowInstance:
    instance = session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).one()
    session.refresh(instance)
    return instance


def _statuses(session: Session, ticket: Ticket) -> dict[str, str]:
    return {row["key"]: row["status"] for row in json.loads(_instance(session, ticket).stages_json)}


def _second_stage(session: Session, ticket: Ticket) -> str:
    _, stages = resolve_ticket_stages(session, ticket)
    return sorted(stages, key=lambda s: s.order)[1].key


def _ask(session: Session, ticket: Ticket, stage_key: str) -> Approval:
    approval = Approval(
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        kind=ApprovalKind.WORKFLOW_GATE,
        title="Resolve exit actions",
        level="medium",
        stage_key=stage_key,
        impact="A person signs off.",
        status=ApprovalStatus.PENDING,
    )
    session.add(approval)
    session.commit()
    return approval


@pytest.fixture(name="ticket")
def ticket_fixture(db_session: Session) -> Ticket:
    ticket = make_task_in_tree(db_session, "unasked awaits")
    ticket.state = TicketState.IN_PROGRESS
    db_session.add(ticket)
    db_session.commit()
    return ticket


# ---- the manual writers -----------------------------------------------------


def _stage_updates(session: Session, ticket: Ticket, key: str) -> None:
    """The Update State modal's per-stage selects."""
    OrchestrationService(session).update_ticket_manual(
        ticket,
        UpdateTicketRequest(title="renamed", stage_updates={key: StageStatus.AWAITING}),
    )


def _one_stage(session: Session, ticket: Ticket, key: str) -> None:
    """The stage overflow menu: one stage, one status."""
    OrchestrationService(session).update_ticket_manual(
        ticket,
        UpdateTicketRequest(title="renamed", stage_key=key, stage_status=StageStatus.AWAITING),
    )


def _cursor_pair(session: Session, ticket: Ticket, key: str) -> None:
    """The modal's cursor fields, `workflow_stage_key` + `workflow_stage_status`."""
    OrchestrationService(session).update_ticket_manual(
        ticket,
        UpdateTicketRequest(
            title="renamed", workflow_stage_key=key, workflow_stage_status=StageStatus.AWAITING
        ),
    )


def _mcp_set_ticket_workflow(session: Session, ticket: Ticket, key: str) -> None:
    """`loregarden_set_ticket_workflow`, the spelling the blob-mesh tickets most likely took."""
    name = "loregarden_set_ticket_workflow"
    args = {"ticket_id": ticket.external_id, "stage_key": key, "stage_status": "awaiting"}
    execute_tool(session, name, normalize_tool_arguments(name, args))


MANUAL_WRITERS: dict[str, Callable[[Session, Ticket, str], None]] = {
    "stage_updates": _stage_updates,
    "stage_key + stage_status": _one_stage,
    "workflow_stage_key + workflow_stage_status": _cursor_pair,
    "loregarden_set_ticket_workflow": _mcp_set_ticket_workflow,
}


@pytest.mark.parametrize("writer", MANUAL_WRITERS.values(), ids=MANUAL_WRITERS.keys())
def test_no_manual_writer_sets_awaiting_without_an_approval(
    db_session: Session, ticket: Ticket, writer: Callable[[Session, Ticket, str], None]
):
    key = _second_stage(db_session, ticket)
    before = _statuses(db_session, ticket)

    with pytest.raises(ValueError, match="no pending approval"):
        writer(db_session, ticket, key)
    db_session.rollback()
    db_session.refresh(ticket)

    # Refused before any field moved: the title sent alongside is not applied.
    assert _statuses(db_session, ticket) == before
    assert ticket.title == "unasked awaits"
    assert unasked_awaits(db_session) == []


@pytest.mark.parametrize("writer", MANUAL_WRITERS.values(), ids=MANUAL_WRITERS.keys())
def test_a_manual_writer_may_set_awaiting_where_an_approval_asks(
    db_session: Session, ticket: Ticket, writer: Callable[[Session, Ticket, str], None]
):
    """The status is reachable where it is true: the documented recovery still works."""
    key = _second_stage(db_session, ticket)
    _ask(db_session, ticket, key)

    writer(db_session, ticket, key)

    assert _statuses(db_session, ticket)[key] == StageStatus.AWAITING.value


def test_an_approval_at_another_stage_does_not_count(db_session: Session, ticket: Ticket):
    _, stages = resolve_ticket_stages(db_session, ticket)
    first, second = (s.key for s in sorted(stages, key=lambda s: s.order)[:2])
    _ask(db_session, ticket, first)

    with pytest.raises(ValueError, match=second):
        _one_stage(db_session, ticket, second)


# ---- the gate writer --------------------------------------------------------


def _seeded(client: TestClient, session: Session, external_id: str) -> Ticket:
    for row in client.get("/api/tickets").json():
        if external_id in (row["external_id"], row["legacy_external_id"]):
            return session.get(Ticket, row["id"])
    raise AssertionError(f"ticket not found: {external_id}")


def test_enter_human_gate_raises_its_approval_before_parking(
    client: TestClient, db_session: Session
):
    """The park used to commit first: a window in which the reconcile timer
    reads an awaiting stage nobody has been asked about yet."""
    ticket = _seeded(client, db_session, "04-workflow-template-overrides")
    real = orchestration_module.create_workflow_gate_approval
    status_when_asked: list[StageStatus] = []

    def spy(session: Session, target: Ticket, stage_key: str, stage_name: str, **kwargs):
        status_when_asked.append(_statuses(session, target)[stage_key])
        return real(session, target, stage_key, stage_name, **kwargs)

    with mock.patch.object(orchestration_module, "create_workflow_gate_approval", side_effect=spy):
        OrchestrationService(db_session).enter_human_gate(ticket, stage_key="approval")
    db_session.refresh(ticket)

    assert status_when_asked and status_when_asked[0] != StageStatus.AWAITING.value
    assert ticket.workflow_stage_status == StageStatus.AWAITING
    assert unasked_awaits(db_session) == []


# ---- the invariant ----------------------------------------------------------


def _park_unasked(session: Session, ticket: Ticket, key: str) -> None:
    """The state the blob-mesh tickets were in, written as the old manual edit wrote it."""
    _, stages = resolve_ticket_stages(session, ticket)
    instance = _instance(session, ticket)
    set_stage_status(ticket, instance, stages, key, StageStatus.AWAITING)
    ticket.workflow_stage_key = key
    session.add_all([ticket, instance])
    session.commit()


def test_the_invariant_blocks_an_unasked_await_and_leaves_an_asked_one(db_session: Session):
    unasked = make_task_in_tree(db_session, "parked by hand")
    asked = make_task_in_tree(db_session, "parked by a gate")
    for ticket in (unasked, asked):
        ticket.state = TicketState.IN_PROGRESS
        db_session.add(ticket)
    db_session.commit()
    key = _second_stage(db_session, unasked)
    _park_unasked(db_session, unasked, key)
    _ask(db_session, asked, key)
    _park_unasked(db_session, asked, key)

    found = [(item.ticket.id, item.stage_key) for item in unasked_awaits(db_session)]
    assert found == [(unasked.id, key)]

    check = run_invariant(db_session, AWAITING_STAGES_ARE_ASKED)
    db_session.refresh(unasked)
    db_session.refresh(asked)

    assert check.repaired == 1
    assert unasked.state == TicketState.BLOCKED
    assert unasked.workflow_stage_status == StageStatus.BLOCKED
    assert _statuses(db_session, unasked)[key] == StageStatus.BLOCKED.value
    assert unasked.blocking_issues == UNASKED_POINTER.format(stage=key)
    assert asked.workflow_stage_status == StageStatus.AWAITING
    assert unasked_awaits(db_session) == []


def test_a_closed_ticket_is_not_this_invariants(db_session: Session, ticket: Ticket):
    """Its open stages close with it (`closed_tickets_have_closed_workflows`)."""
    _park_unasked(db_session, ticket, _second_stage(db_session, ticket))
    OrchestrationService(db_session).update_ticket_manual(
        ticket, UpdateTicketRequest(state=TicketState.DONE)
    )
    assert unasked_awaits(db_session) == []


# ---- every writer of the status ---------------------------------------------

_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "loregarden"

#: Every function that writes `StageStatus.AWAITING` itself, and how a person
#: is asked. A writer that passes a status it was handed (the manual edit) is
#: not found by the scan; `MANUAL_WRITERS` above covers those.
AWAITING_WRITERS: dict[str, str] = {
    "agents/executors/permission_bridge.py:_create_question_approval": (
        "same commit: the CLI_QUESTION approval is added before the session commits"
    ),
    "agents/executors/permission_bridge.py:_create_permission_approval": (
        "same commit: the CLI_PERMISSION approval is added before the session commits"
    ),
    "services/gate_approvals.py:request_exit_action_gate": (
        "after: create_workflow_gate_approval commits the gate, and it raises if there is none"
    ),
    "services/orchestration_callbacks.py:complete_stage": (
        "after: gate_on_orchestrator_pass returned the gate it opened"
    ),
    "services/orchestration_callbacks.py:request_approval": (
        "same commit: the approval is added before the session commits"
    ),
    "services/run_completion.py:_advance_clean_exit": (
        "after: create_workflow_gate_approval with the outstanding actions, never None"
    ),
    "services/orchestration.py:enter_human_gate": (
        "after: _ensure_exit_action_gate_approval, and DONE when it raised none"
    ),
    "testing/prod_shape_tree.py:_park": (
        "same commit: the scenario adds the pending gate approval beside the cursor"
    ),
}


def _is_awaiting(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "AWAITING"
        and isinstance(node.value, ast.Name)
        and node.value.id == "StageStatus"
    )


def _assigns_awaiting(value: ast.AST) -> bool:
    if isinstance(value, ast.IfExp):
        return _assigns_awaiting(value.body) or _assigns_awaiting(value.orelse)
    return _is_awaiting(value)


def _writes_awaiting(function: ast.AST) -> bool:
    """A call handed the status, or a name, attribute or item assigned it."""
    for node in ast.walk(function):
        if isinstance(node, ast.Call) and any(
            _is_awaiting(arg) for arg in [*node.args, *(k.value for k in node.keywords)]
        ):
            return True
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            if _assigns_awaiting(node.value):
                return True
    return False


@functools.cache
def _discovered_awaiting_writers() -> frozenset[str]:
    found: set[str] = set()
    for path in sorted(_SOURCE_ROOT.rglob("*.py")):
        text = path.read_text()
        if "StageStatus.AWAITING" not in text:
            continue
        relative = path.relative_to(_SOURCE_ROOT).as_posix()
        for node in ast.walk(ast.parse(text, filename=relative)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inner = {
                    child.name
                    for child in ast.walk(node)
                    if child is not node
                    and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                if _writes_awaiting(node) and not inner:
                    found.add(f"{relative}:{node.name}")
    return frozenset(found)


def test_every_writer_of_awaiting_is_enumerated():
    discovered = _discovered_awaiting_writers()

    unenumerated = sorted(discovered - set(AWAITING_WRITERS))
    assert not unenumerated, (
        "These set a stage awaiting a person and are not in AWAITING_WRITERS. Raise the "
        "approval in the same commit or before it, and record how here — an awaiting stage "
        f"with nothing in the inbox waits on nobody: {unenumerated}"
    )
    gone = sorted(set(AWAITING_WRITERS) - discovered)
    assert not gone, f"AWAITING_WRITERS names functions that no longer write it: {gone}"
