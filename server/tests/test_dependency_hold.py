"""A ticket waiting on unmet prerequisites is held at every dispatch, and resumed when they land.

lg-milestone-that-1315 was filed waiting on 1314 with the wait written only in
prose. It was dispatched; a planner linked the edge mid-run; triage was then
dispatched three more times past the edge and given a repair turn for a wait no
agent could shorten. These pin the hold at each dispatch path, the operator's
recorded way past it, and the release when the prerequisite lands.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch

import pytest
from loregarden.models.domain import (
    OrchestrationDriver,
    OrchestratorDecision,
    StageStatus,
    Ticket,
    TicketState,
)
from loregarden.services.dependency_hold import (
    DependencyHold,
    holding_prerequisites,
    override_prerequisites,
    refuse_held_dispatch,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.seed import seed_database
from loregarden.services.ticket_dependencies import TicketDependencyService
from sqlmodel import Session, select
from tests.factories import make_workspace_ticket
from tests.history_helpers import decision_kinds, decision_payloads

SERVER = Path(__file__).resolve().parents[1]


@pytest.fixture(name="pair")
def pair_fixture(db_session: Session) -> tuple[Ticket, Ticket]:
    """A dependent waiting on a prerequisite that is still in progress."""
    seed_database(db_session)
    prerequisite = make_workspace_ticket(db_session, "hold-prereq")
    dependent = make_workspace_ticket(db_session, "hold-dependent")
    OrchestrationService(db_session).ensure_workflow_instance(dependent, commit=True)
    TicketDependencyService(db_session).add_dependency(dependent.id, prerequisite.id)
    return dependent, prerequisite


def test_start_run_holds_a_ticket_on_an_unfinished_prerequisite(db_session, pair):
    dependent, prerequisite = pair
    stage = dependent.workflow_stage_key

    with pytest.raises(DependencyHold, match="hold-prereq"):
        OrchestrationService(db_session).start_run(dependent)

    db_session.refresh(dependent)
    # Refused before a single write: the stage did not start, and nothing blocked.
    assert dependent.workflow_stage_key == stage
    assert dependent.workflow_stage_status is not StageStatus.RUNNING
    assert dependent.state is not TicketState.BLOCKED
    (payload,) = decision_payloads(db_session, dependent.id)
    assert payload["decision"] == OrchestratorDecision.HELD_ON_PREREQUISITES.value
    assert payload["prerequisites"][0]["external_id"] == prerequisite.external_id
    assert payload["prerequisites"][0]["reason"] == "not_done"


def test_a_ticket_with_no_unmet_prerequisite_is_not_held(db_session, pair):
    dependent, prerequisite = pair
    prerequisite.state = TicketState.WONT_DO
    db_session.add(prerequisite)
    db_session.commit()

    refuse_held_dispatch(db_session, dependent, stage_key="triage")

    assert decision_kinds(db_session, dependent.id) == []


def test_an_override_covers_the_prerequisites_it_saw_and_no_later_one(db_session, pair):
    dependent, _ = pair
    overridden = override_prerequisites(db_session, dependent, stage_key="triage", actor="operator")
    assert [entry.ticket.external_id for entry in overridden] == ["hold-prereq"]
    assert holding_prerequisites(db_session, dependent) == []
    refuse_held_dispatch(db_session, dependent, stage_key="triage")

    later = make_workspace_ticket(db_session, "hold-later")
    TicketDependencyService(db_session).add_dependency(dependent.id, later.id)

    held = holding_prerequisites(db_session, dependent)
    assert [entry.ticket.external_id for entry in held] == ["hold-later"]
    assert OrchestratorDecision.PREREQUISITES_OVERRIDDEN.value in decision_kinds(
        db_session, dependent.id
    )


def test_mcp_start_stage_is_held(db_session, pair):
    from loregarden.services.orchestration_callbacks import OrchestrationCallbackService

    dependent, _ = pair
    callbacks = OrchestrationCallbackService(db_session)
    run = callbacks.start_orchestration_run(
        dependent, driver=OrchestrationDriver.EXTERNAL_MCP, profile_slug="default"
    )

    with pytest.raises(DependencyHold):
        callbacks.start_stage(run, dependent, stage_key=dependent.workflow_stage_key)

    db_session.refresh(dependent)
    assert dependent.workflow_stage_status is not StageStatus.RUNNING


def test_a_pipeline_agent_may_not_start_a_stage_past_its_prerequisites(db_session, pair):
    from loregarden.mcp.tools import execute_tool

    dependent, _ = pair
    with pytest.raises(ValueError, match="ignore_prerequisites"):
        execute_tool(
            db_session,
            "loregarden_start_stage",
            {"run_id": "x", "stage_key": "triage", "ignore_prerequisites": True},
            orchestrated=True,
        )


# --- every dispatch path is held -------------------------------------------
#
# A path that starts a stage without reaching `start_run` needs its own hold.
# Enumerated from source: a new function that sets a stage RUNNING or creates an
# AgentRun must be added here, held or exempt with a reason, or this fails.

#: (module, function) → how it is held.
DISPATCHERS: dict[tuple[str, str], str] = {
    ("services/orchestration.py", "start_run"): "refuse_held_dispatch",
    ("services/orchestration_callbacks.py", "start_stage"): "refuse_held_dispatch",
    ("services/stage_fanout_service.py", "launch_fanout"): "refuse_held_dispatch",
}

#: Every helper that writes a stage status, by the name it is called through.
_STATUS_WRITERS = frozenset({"set_stage_status", "_set_stage_status", "finalize_stage"})


def _sets_stage_running(node: ast.AST) -> bool:
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):  # py-org: allow-isinstance - AST node types
            continue
        name = getattr(call.func, "attr", getattr(call.func, "id", ""))
        if name not in _STATUS_WRITERS:
            continue
        values = [*call.args, *(keyword.value for keyword in call.keywords)]
        if any(getattr(value, "attr", "") == "RUNNING" for value in values):
            return True
    return False


def _calls(node: ast.AST, name: str) -> bool:
    return any(
        getattr(call.func, "attr", getattr(call.func, "id", "")) == name
        for call in ast.walk(node)
        if isinstance(call, ast.Call)  # py-org: allow-isinstance - AST node types
    )


def _stage_starters() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    root = SERVER / "loregarden"
    for path in root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "RUNNING" not in source:
            continue
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.FunctionDef) and _sets_stage_running(
                node
            ):  # py-org: allow-isinstance - AST node types
                found.add((str(path.relative_to(root)), node.name))
    return found


#: Functions that set a stage RUNNING and are not dispatches, with why.
NOT_DISPATCHES: dict[tuple[str, str], str] = {
    ("agents/executors/permission_bridge.py", "_resume_after_approval"): (
        "puts the stage of a run that is already executing back to RUNNING once its "
        "approval is answered; the run was dispatched, and held, through start_run"
    ),
}


def test_every_function_that_starts_a_stage_is_held():
    starters = _stage_starters()
    unknown = starters - DISPATCHERS.keys() - NOT_DISPATCHES.keys()
    assert not unknown, (
        "These set a stage RUNNING without being listed as held or exempt: "
        f"{sorted(unknown)}. Call `refuse_held_dispatch` before the write, or list "
        "them in NOT_DISPATCHES with the reason they start no work."
    )


def test_every_listed_dispatcher_actually_holds():
    root = SERVER / "loregarden"
    for (module, function), how in DISPATCHERS.items():
        tree = ast.parse((root / module).read_text(encoding="utf-8"))
        node = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef)
            and n.name == function  # py-org: allow-isinstance - AST node types
        )
        assert _calls(node, how), f"{module}::{function} no longer calls {how}"


# --- the REST surface ----------------------------------------------------------


def test_rest_creates_a_ticket_with_its_prerequisites(client, db_session, pair):
    _, prerequisite = pair
    res = client.post(
        "/api/tickets",
        json={
            "workspace_slug": "loregarden",
            "title": "Filed with its wait",
            "work_item_type": "milestone",
            "depends_on": [prerequisite.external_id],
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    (dependency,) = body["dependencies"]
    assert dependency["id"] == prerequisite.id
    assert dependency["unmet_reason"] == "not_done"
    assert dependency["workspace_slug"] == "loregarden"
    assert [ref["external_id"] for ref in body["waiting_on"]] == [prerequisite.external_id]


def test_rest_refuses_an_unknown_prerequisite_before_creating_anything(client, db_session, pair):
    res = client.post(
        "/api/tickets",
        json={
            "workspace_slug": "loregarden",
            "title": "Waits on nothing",
            "work_item_type": "milestone",
            "depends_on": ["00000000-0000-4000-8000-000000000002"],
        },
    )
    assert res.status_code == 404
    assert not db_session.exec(select(Ticket).where(Ticket.title == "Waits on nothing")).first()


def test_rest_start_is_refused_while_held_and_allowed_when_forced(client, db_session, pair):
    dependent, _ = pair
    held = client.post(f"/api/tickets/{dependent.id}/start", json={"manual": True})
    assert held.status_code == 409
    assert "hold-prereq" in held.json()["detail"]

    with patch("loregarden.api.tickets.schedule_agent_run"):
        forced = client.post(
            f"/api/tickets/{dependent.id}/start",
            json={"manual": True, "ignore_prerequisites": True},
        )
    assert forced.status_code == 200, forced.text
    (dependency,) = forced.json()["dependencies"]
    assert dependency["overridden"] is True
    assert forced.json()["blocked_on"] != "tickets"
