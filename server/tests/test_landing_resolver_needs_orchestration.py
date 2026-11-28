"""A landing resolver armed where no orchestration runs gets one, or a block.

`_offer_conflict_resolution` armed the terminal stage (PENDING, repair pin)
from wherever a landing ran, including the terminal stage sweep, which runs
only when no orchestration does. Nothing consumed the pin, so the next sweep
landed, conflicted and armed again, every ~90s, never spending an attempt.
Six tickets looped that way on 2026-11-27.
"""

from __future__ import annotations

import ast
import functools
from pathlib import Path
from unittest import mock

import pytest
from loregarden.agents.registry import REPAIR_AGENT_ID
from loregarden.models.domain import (
    OrchestrationRun,
    OrchestrationRunStatus,
    QueuedRun,
    QueuePosition,
    StageStatus,
    TicketState,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services import stranded_resolver_pins
from loregarden.services.invariant_registry import INVARIANTS
from loregarden.services.stranded_resolver_pins import (
    LANDING_RESOLVERS_HAVE_A_LOOP,
    stranded_resolver_tickets,
)
from loregarden.services.terminal_stage_sweep import finish_parked_terminal_tickets
from sqlmodel import select
from tests.factories import make_orchestration_run
from tests.history_helpers import decision_payloads
from tests.test_landing_conflict_resolver import (  # noqa: F401 - imported as pytest fixtures
    _land,
    conflicted_fixture,
    milestone_fixture,
    repo_fixture,
    session_fixture,
    workspace_fixture,
)
from tests.worktree_helpers import blocking_text

SERVICES = Path(__file__).resolve().parents[1] / "loregarden" / "services"


@pytest.fixture(name="scheduled")
def scheduled_fixture():
    """The resumes `serve_stranded_resolver` hands to the background starter, not run."""
    with mock.patch.object(stranded_resolver_pins, "schedule_interrupted_resumes") as schedule:
        yield schedule


def _live_runs(session, ticket):
    return session.exec(
        select(OrchestrationRun)
        .where(OrchestrationRun.ticket_id == ticket.id)
        .where(
            OrchestrationRun.status.in_(  # type: ignore[attr-defined]
                (OrchestrationRunStatus.QUEUED, OrchestrationRunStatus.RUNNING)
            )
        )
    ).all()


def _resolver_dispatches(session, ticket) -> int:
    return sum(
        1
        for payload in decision_payloads(session, ticket.id)
        if payload["decision"] == "dispatched_landing_resolver"
    )


def _ended(session, run: OrchestrationRun, *, worked: bool) -> None:
    """End `run`; `worked` renews its lease, so `did_nothing` reads it as a run that did work."""
    run.status = OrchestrationRunStatus.SUCCEEDED
    if worked:
        run.last_seen_at = utcnow()
    session.add(run)
    session.commit()


def test_a_conflict_found_by_the_sweep_starts_an_orchestration_for_its_resolver(
    session, conflicted, scheduled
):
    finished = finish_parked_terminal_tickets(session, ticket_id=conflicted.id)

    session.refresh(conflicted)
    assert [t.id for t in finished] == [conflicted.id]
    assert conflicted.state is not TicketState.BLOCKED, "a resolver is coming"
    assert conflicted.next_agent == REPAIR_AGENT_ID
    assert len(_live_runs(session, conflicted)) == 1, "an orchestration now holds the pin"
    [requests] = scheduled.call_args.args
    assert [r.ticket_id for r in requests] == [conflicted.id]
    assert requests[0].stop_at_stage_key == conflicted.workflow_stage_key


def test_the_sweep_does_not_land_a_pinned_ticket_again(session, conflicted, scheduled):
    """The loop itself: every pass re-landed, re-armed and logged another dispatch."""
    finish_parked_terminal_tickets(session, ticket_id=conflicted.id)
    dispatches = _resolver_dispatches(session, conflicted)
    for run in _live_runs(session, conflicted):
        _ended(session, run, worked=True)

    again = finish_parked_terminal_tickets(session, ticket_id=conflicted.id)

    assert again == [], "a pinned ticket is the resolver's, not the sweep's"
    assert _resolver_dispatches(session, conflicted) == dispatches


def test_a_pin_left_by_an_orchestration_that_ended_is_given_a_new_one(
    session, conflicted, scheduled
):
    _land(session, conflicted)
    armed_by = session.exec(
        select(OrchestrationRun).where(OrchestrationRun.ticket_id == conflicted.id)
    ).one()
    _ended(session, armed_by, worked=True)

    violations = LANDING_RESOLVERS_HAVE_A_LOOP.detect(session)
    assert [v.subject_id for v in violations] == [conflicted.id]
    assert LANDING_RESOLVERS_HAVE_A_LOOP.repair(session, violations) == 1

    session.refresh(conflicted)
    assert conflicted.next_agent == REPAIR_AGENT_ID
    assert len(_live_runs(session, conflicted)) == 1
    assert LANDING_RESOLVERS_HAVE_A_LOOP.detect(session) == [], "a held pin is not stranded"


def test_no_second_start_after_an_orchestration_that_did_nothing(session, conflicted, scheduled):
    """The bound on the start: a run that started nothing would be followed by another."""
    _land(session, conflicted)
    armed_by = session.exec(
        select(OrchestrationRun).where(OrchestrationRun.ticket_id == conflicted.id)
    ).one()
    _ended(session, armed_by, worked=False)

    LANDING_RESOLVERS_HAVE_A_LOOP.repair(session, LANDING_RESOLVERS_HAVE_A_LOOP.detect(session))

    session.refresh(conflicted)
    assert conflicted.state is TicketState.BLOCKED
    assert conflicted.next_agent == ""
    assert _live_runs(session, conflicted) == []
    text = blocking_text(session, conflicted)
    assert armed_by.run_code in text, "the block names the run that did nothing"
    assert "loregarden_start_orchestration" in text
    scheduled.assert_not_called()
    assert stranded_resolver_tickets(session) == []


def test_a_pin_waiting_in_a_lane_is_not_stranded(session, conflicted):
    _land(session, conflicted)
    session.add(
        QueuedRun(
            workspace_id=conflicted.workspace_id,
            ticket_id=conflicted.id,
            status=QueuePosition.QUEUED,
        )
    )
    session.commit()

    assert LANDING_RESOLVERS_HAVE_A_LOOP.detect(session) == []


def test_a_pin_its_orchestration_still_holds_is_not_stranded(session, conflicted):
    _land(session, conflicted)
    live = make_orchestration_run(
        session, workspace_id=conflicted.workspace_id, ticket_id=conflicted.id, run_code="ORCH-live"
    )
    live.status = OrchestrationRunStatus.RUNNING
    session.add(live)
    session.commit()

    assert LANDING_RESOLVERS_HAVE_A_LOOP.detect(session) == []


def _callee(func: ast.expr) -> str:
    if type(func) is ast.Name:
        return func.id
    if type(func) is ast.Attribute:
        return func.attr
    return ""


@functools.cache
def _service_calls() -> dict[str, frozenset[str]]:
    """What every service function calls, keyed module::function; parsed once per session."""
    calls: dict[str, frozenset[str]] = {}
    for path in sorted(SERVICES.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if type(fn) is ast.FunctionDef:
                calls[f"{path.relative_to(SERVICES)}::{fn.name}"] = frozenset(
                    _callee(node.func) for node in ast.walk(fn) if type(node) is ast.Call
                )
    return calls


def _calls_by_function(name: str) -> set[str]:
    """Every service function other than `name` itself that calls `name`."""
    return {
        key
        for key, called in _service_calls().items()
        if name in called and not key.endswith(f"::{name}")
    }


def test_every_writer_that_arms_a_repair_turn_has_a_loop_to_run_it():
    """Enumerates the writers so a new one is read, not assumed.

    Each arms a pin only an orchestration's loop consumes, and each is served:
    `block_repair` refuses a block recorded outside a run (`_run_refusal`),
    `ci_autofix_dispatch` starts its orchestration itself, and the landing
    resolver is served by the sweep at once and by the invariant otherwise.
    """
    assert _calls_by_function("arm_repair_turn") == {
        "block_repair.py::offer_repair",
        "ci_autofix_dispatch.py::dispatch_ci_fix",
        "landing.py::_offer_conflict_resolution",
    }, "a new writer arms a repair turn: show what runs it, then add it here"
    assert "terminal_stage_sweep.py::finish_parked_terminal_tickets" in _calls_by_function(
        "serve_stranded_resolver"
    )
    assert LANDING_RESOLVERS_HAVE_A_LOOP in INVARIANTS


def test_the_block_names_why_it_could_not_start(session, conflicted, scheduled):
    """An admission that takes nothing is a block with that reason, not a silent pin."""
    _land(session, conflicted, in_orchestration=False)
    with mock.patch.object(stranded_resolver_pins, "admit_resumes", return_value=([], [])):
        LANDING_RESOLVERS_HAVE_A_LOOP.repair(session, LANDING_RESOLVERS_HAVE_A_LOOP.detect(session))

    session.refresh(conflicted)
    assert conflicted.state is TicketState.BLOCKED
    assert "admitted no orchestration" in blocking_text(session, conflicted)
    assert conflicted.workflow_stage_status is not StageStatus.PENDING
