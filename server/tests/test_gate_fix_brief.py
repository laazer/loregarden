"""Whoever takes a gate fix is told what the gate said, and may make the change it asks for.

A transition gate that fails after a stage passed is retried by an agent: the
stage's own, or the debugger for a failing test, up to
``autofix_max_agent_attempts`` times. The gate's output went to that agent
only through ``ticket.blocking_issues``, which `_prepare_stage_start` blanks
the moment the retry is dispatched, so every retry ran without being told why
it was there. lg-reference-sources-1912's test_designer was rerun three times
against a size gate (``file is 1269 lines (max 1200); split into smaller
modules``), re-verified its own suite each time, reported ``pass``, and the
ticket blocked with no repair turn.

So the brief is kept where dispatch cannot erase it, it authorises the
restructuring a gate asks for, and when the retries are spent the repair agent
gets its one turn on the gate failure too.
"""

from __future__ import annotations

import shlex
from datetime import timedelta

import pytest
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.agents.registry import DEBUGGER_AGENT_ID, REPAIR_AGENT_ID
from loregarden.agents.stage_context import build_orchestration_context
from loregarden.models.domain import AgentRun, RunStatus, Ticket, TicketState
from loregarden.models.domain.enums import utcnow
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.gate_recovery import GATE_FIX_AUTHORITY
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.repair_brief import gate_fix_brief_for, record_gate_fix_brief
from tests.history_helpers import decision_kinds
from tests.test_transition_gate_reroute import _setup_ticket_at_test_break, _stage_report

SIZE_GATE = "client/src/big.test.tsx: file is 1269 lines (max 1200); split into smaller modules"
TEST_GATE = "tests/test_x.py::test_y FAILED\n1 failed, 2 passed in 0.10s"


def _failing_gate(output: str) -> str:
    """A gate command (split with shlex, no shell) that prints `output` and fails."""
    return shlex.join(["sh", "-c", 'printf "%s\\n" "$0"; exit 1', output])


def _drive(db_session, tmp_path, monkeypatch, *, gate_output: str, agent_attempts: int):
    """Run the ticket against a gate that never passes; each dispatch's agent and prompt."""
    ticket, profile = _setup_ticket_at_test_break(db_session, tmp_path)
    profile.gates.commands = [_failing_gate(gate_output)]
    profile.gates.autofix_agent_fallback = True
    profile.gates.autofix_max_agent_attempts = agent_attempts
    dispatched: list[tuple[str, str]] = []

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        dispatched.append(
            (
                run.agent_id,
                build_orchestration_context(
                    ticket=worker_ticket, run=run, stage_def=None, session=self.session
                ),
            )
        )
        return OrchestrationService(self.session).complete_run(
            run, status=RunStatus.SUCCEEDED, stdout=_stage_report("pass", 0.95), stderr=""
        )

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)
    BuiltinOrchestrator(db_session).execute(ticket, profile, max_stages=10)
    db_session.refresh(ticket)
    return ticket, dispatched


@pytest.mark.parametrize(
    ("gate_output", "retry_agent"),
    [(SIZE_GATE, "test_breaker"), (TEST_GATE, DEBUGGER_AGENT_ID)],
    ids=["stage-agent", "debugger"],
)
def test_a_gate_fix_retry_is_told_what_the_gate_said_and_may_restructure(
    db_session, tmp_path, monkeypatch, gate_output, retry_agent
):
    _, dispatched = _drive(
        db_session, tmp_path, monkeypatch, gate_output=gate_output, agent_attempts=1
    )

    (first_agent, first_prompt), (agent, prompt) = dispatched[0], dispatched[1]
    assert first_agent == "test_breaker"
    assert gate_output.splitlines()[0] not in first_prompt, "nothing had failed yet"
    assert agent == retry_agent
    assert gate_output.splitlines()[0] in prompt
    assert GATE_FIX_AUTHORITY in prompt


def test_when_the_retries_are_spent_the_repair_agent_takes_the_gate_failure(
    db_session, tmp_path, monkeypatch
):
    ticket, dispatched = _drive(
        db_session, tmp_path, monkeypatch, gate_output=SIZE_GATE, agent_attempts=1
    )

    assert [agent for agent, _ in dispatched] == ["test_breaker", "test_breaker", REPAIR_AGENT_ID]
    repair_prompt = dispatched[-1][1]
    assert SIZE_GATE in repair_prompt
    assert GATE_FIX_AUTHORITY in repair_prompt
    # One repair turn, then a person: the stage does not cycle.
    assert ticket.state is TicketState.BLOCKED
    kinds = decision_kinds(db_session, ticket.id)
    assert kinds.count("dispatched_repair") == 1
    assert "repair_escalated" in kinds


def test_a_gate_fix_brief_reaches_only_the_dispatch_it_was_armed_for(db_session, tmp_path):
    """A later run of the same stage (a rework round, a requeue) must not read
    an old gate failure as its reason for being there."""
    ticket, _ = _setup_ticket_at_test_break(db_session, tmp_path)
    record_gate_fix_brief(db_session, ticket, stage_key="test-break", brief="the gate said X")
    db_session.commit()
    now = utcnow() + timedelta(seconds=1)
    armed = _run(db_session, ticket, created_at=now)
    later = _run(db_session, ticket, created_at=now + timedelta(seconds=1))

    assert gate_fix_brief_for(db_session, ticket, run=armed) == "the gate said X"
    assert gate_fix_brief_for(db_session, ticket, run=later) == ""


def _run(db_session, ticket: Ticket, *, created_at) -> AgentRun:
    run = AgentRun(
        run_code=f"run_{created_at.timestamp():.0f}_{ticket.id[:4]}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="test_breaker",
        stage_key="test-break",
        status=RunStatus.SUCCEEDED,
        created_at=created_at,
    )
    db_session.add(run)
    db_session.commit()
    return run
