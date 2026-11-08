"""A stage the criteria check guards cannot be started or finished past it, by any path.

The drivers check before they choose a stage (`test_criterion_gate`). These are
the paths that start or finish one themselves: a stage started by hand, the
terminal sweep, and every other dispatcher, found by reading the source.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest
from loregarden.models.domain import StageStatus, Ticket
from loregarden.services.criterion_check import CriteriaCheck, check_criteria
from loregarden.services.criterion_gate import check_before_dispatch
from loregarden.services.gate_dispatch_check import GateCriteriaUnanswered
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.terminal_stage_sweep import finish_parked_terminal_tickets
from loregarden.services.workflow_service import resolve_ticket_stages, workflow_instance_for
from loregarden.services.workflow_state import parse_stage_map
from sqlmodel import Session
from tests.dispatch_enumeration import (
    NOT_DISPATCHES,
    callers_of,
    calls,
    function_node,
    stage_starters,
)
from tests.test_criterion_gate import (  # noqa: F401 — fixtures used by name
    CRITERIA,
    _decisions,
    _met,
    _next,
    _orch_run,
    _ticket_on,
    checkout_fixture,
    env_fixture,
    ticket_fixture,
)

PASSING = "server/tests/test_arms.py::test_reads_width"
FAILING = "server/tests/test_arms.py::test_names_the_arm"

#: A workflow with no gate stage: its terminal stage is the one the check guards.
NO_GATE_SHAPE = [
    {"key": "implement", "name": "Implement", "agent_id": "backend_implementer", "order": 1},
    {"key": "verify", "name": "Verify", "agent_id": "verifier", "order": 2},
    {"key": "done", "name": "Done", "order": 3, "terminal": True},
]


@pytest.fixture(name="head")
def head_fixture(env, checkout):  # noqa: ARG001 — env points the check at the checkout
    """Point the ticket's HEAD (`evidence.resolve_head_sha`) at the same checkout."""
    with patch("loregarden.services.evidence.resolve_ticket_root", return_value=checkout):
        yield checkout


@pytest.fixture(name="parked")
def parked_fixture(db_session: Session) -> Ticket:
    """A ticket on a gate-less workflow, every stage done, parked on its terminal stage.

    The cursor on the terminal stage, pending, with nothing live behind it: how a
    cleared landing block leaves it, which is what the sweep selects.
    """
    ticket = _ticket_on(db_session, NO_GATE_SHAPE, through_gate=False)
    ticket.workflow_stage_key = "done"
    ticket.workflow_stage_status = StageStatus.PENDING
    db_session.add(ticket)
    db_session.commit()
    return ticket


def _status(session: Session, ticket: Ticket, key: str) -> StageStatus:
    _, stages = resolve_ticket_stages(session, ticket)
    return parse_stage_map(workflow_instance_for(session, ticket.id), stages)[key]


def _start_gate_by_hand(session: Session, ticket: Ticket) -> Ticket:
    return OrchestrationCallbackService(session).start_stage(
        _orch_run(session, ticket), ticket, stage_key="gate"
    )


# --- a stage started by hand ---------------------------------------------------


def test_a_gate_started_by_hand_with_a_criterion_unanswered_is_refused_and_sent_back(
    db_session, ticket, env
):
    _met(db_session, ticket, CRITERIA[0], PASSING)
    with pytest.raises(GateCriteriaUnanswered, match="not answered for"):
        _start_gate_by_hand(db_session, ticket)
    db_session.refresh(ticket)
    assert _status(db_session, ticket, "gate") == StageStatus.PENDING
    assert _next(db_session, ticket) == "verify"
    assert any("criteria_sent_to_verify" in p for p in _decisions(db_session, ticket))


def test_a_gate_started_by_hand_with_a_failing_test_is_refused(db_session, ticket, env):
    _met(db_session, ticket, CRITERIA[0], PASSING)
    _met(db_session, ticket, CRITERIA[1], FAILING)
    with pytest.raises(GateCriteriaUnanswered):
        _start_gate_by_hand(db_session, ticket)
    assert _next(db_session, ticket) == "verify"


def test_a_gate_started_by_hand_with_every_criterion_met_starts_checked(db_session, ticket, env):
    for criterion in CRITERIA:
        _met(db_session, ticket, criterion, PASSING)
    _start_gate_by_hand(db_session, ticket)
    assert _status(db_session, ticket, "gate") == StageStatus.RUNNING
    assert any("criteria_checked" in p for p in _decisions(db_session, ticket))


def test_a_gate_started_by_hand_whose_tests_cannot_run_blocks_the_ticket(db_session, ticket, env):
    for criterion in CRITERIA:
        _met(db_session, ticket, criterion, PASSING)
    with (
        patch(
            "loregarden.services.criterion_gate.check_criteria",
            return_value=_unavailable(),
        ),
        pytest.raises(GateCriteriaUnanswered),
    ):
        _start_gate_by_hand(db_session, ticket)
    db_session.refresh(ticket)
    assert ticket.state.value == "blocked"


def _unavailable() -> CriteriaCheck:
    return CriteriaCheck(gaps=[], unavailable="no runner")


def test_a_check_that_passed_at_head_is_not_rerun_when_the_stage_starts(db_session, ticket, head):
    for criterion in CRITERIA:
        _met(db_session, ticket, criterion, PASSING)
    check_before_dispatch(db_session, ticket, "gate", None)
    with patch("loregarden.services.criterion_gate.check_criteria") as rerun:
        check_before_dispatch(db_session, ticket, "gate", None)
    rerun.assert_not_called()


def test_a_stage_the_check_does_not_guard_starts_without_it(db_session, ticket, env):
    with patch("loregarden.services.criterion_gate.check_criteria") as check:
        check_before_dispatch(db_session, ticket, "review", None)
    check.assert_not_called()


# --- the terminal sweep --------------------------------------------------------


def test_the_terminal_sweep_does_not_finish_a_ticket_with_a_criterion_unanswered(
    db_session, parked, env
):
    assert _next(db_session, parked) == "done"
    finished = finish_parked_terminal_tickets(db_session, ticket_id=parked.id)
    assert finished == []
    db_session.refresh(parked)
    assert parked.state.value != "done"
    assert _next(db_session, parked) == "verify"
    assert any("criteria_sent_to_verify" in p for p in _decisions(db_session, parked))


# --- every path ----------------------------------------------------------------

#: Every function that starts a stage and asks the check first.
GUARDED_STARTERS = {
    ("services/orchestration.py", "start_run"),
    ("services/orchestration_callbacks.py", "start_stage"),
    ("services/stage_fanout_service.py", "launch_fanout"),
}

#: Every function that finishes a workflow (lands it and marks its terminal stage
#: done) and asks the check first.
GUARDED_FINISHERS = {
    ("services/orchestration.py", "finalize_workflow"),
    ("services/orchestration.py", "advance_stage"),
}


def test_every_function_that_starts_a_stage_asks_the_criteria_check():
    unknown = stage_starters() - GUARDED_STARTERS - NOT_DISPATCHES.keys()
    assert not unknown, (
        f"These set a stage RUNNING without the criteria check: {sorted(unknown)}. "
        "Call `refuse_unchecked_gate` before the write, or list them in NOT_DISPATCHES."
    )
    for module, function in GUARDED_STARTERS:
        assert calls(function_node(module, function), "refuse_unchecked_gate"), (
            f"{module}::{function} no longer asks the criteria check"
        )


def test_every_function_that_finishes_a_workflow_asks_the_criteria_check():
    assert callers_of("_finish_workflow") == GUARDED_FINISHERS, (
        "A new caller of `_finish_workflow` lands a ticket; it must call "
        "`refuse_unchecked_gate` for the terminal stage before any write, and be listed here."
    )
    for module, function in GUARDED_FINISHERS:
        assert calls(function_node(module, function), "refuse_unchecked_gate"), (
            f"{module}::{function} no longer asks the criteria check"
        )


def test_a_commit_after_the_check_reruns_it_when_the_stage_starts(db_session, ticket, head):
    for criterion in CRITERIA:
        _met(db_session, ticket, criterion, PASSING)
    check_before_dispatch(db_session, ticket, "gate", None)
    (head / "later.txt").write_text("changed")
    for args in (["add", "."], ["commit", "-q", "-m", "later"]):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            cwd=head,
            check=True,
            env={"PATH": "/usr/bin:/bin"},
        )
    with patch("loregarden.services.criterion_gate.check_criteria", wraps=check_criteria) as rerun:
        check_before_dispatch(db_session, ticket, "gate", None)
    rerun.assert_called_once()


def test_with_no_run_to_block_under_a_check_that_cannot_run_refuses_the_start(
    db_session, ticket, env
):
    for criterion in CRITERIA:
        _met(db_session, ticket, criterion, PASSING)
    with (
        patch("loregarden.services.criterion_gate.check_criteria", return_value=_unavailable()),
        pytest.raises(GateCriteriaUnanswered, match="no runner"),
    ):
        check_before_dispatch(db_session, ticket, "gate", None)
    db_session.refresh(ticket)
    assert ticket.state.value != "blocked"


def test_a_check_with_no_commit_to_name_is_rerun_when_the_stage_starts(db_session, ticket, env):
    for criterion in CRITERIA:
        _met(db_session, ticket, criterion, PASSING)
    with patch("loregarden.services.criterion_gate.resolve_head_sha", return_value=""):
        check_before_dispatch(db_session, ticket, "gate", None)
        with patch(
            "loregarden.services.criterion_gate.check_criteria", wraps=check_criteria
        ) as rerun:
            check_before_dispatch(db_session, ticket, "gate", None)
    rerun.assert_called_once()
