"""Each gate has its own timeout, and a timeout is its own outcome (lg-milestone-that-2487).

On 2026-11-07 a probe that answers in 4ms idle blew its 5s budget at load
average ~30, and the outcome read UNAVAILABLE, the same words as a missing
tool. A timeout now says so, names the timeout and the host load, keeps the
command's last output, and is retried once before it blocks.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
import yaml
from loregarden.config import settings
from loregarden.models.domain import (
    ActivityHighlightKind,
    BlockKind,
    DomainEvent,
    EventType,
    GateFailureKind,
    GateOutcome,
    Ticket,
    TicketState,
    Workspace,
)
from loregarden.services import gate_runner
from loregarden.services.gate_eval import build_episodes
from loregarden.services.gate_runner import (
    TIMEOUT_OUTPUT_TAIL,
    _lease_ttl,
    run_transition_gates,
)
from loregarden.services.invariant_registry import UNAVAILABLE_GATES_ARE_NOT_WORK
from loregarden.services.orchestration_profile import (
    DEFAULT_GATE_TIMEOUT_SECONDS,
    GatesConfig,
    OrchestrationProfile,
    orchestration_dir,
    resolve_orchestration_profile,
    update_gates_config,
)
from loregarden.services.workspace_activity import _highlight
from pydantic import ValidationError
from sqlmodel import Session
from tests.test_gate_domain_events import _gate_events, _run_stage, _setup_ticket_at_test_break
from tests.test_gate_eval import ev
from tests.test_unavailable_gate_is_not_work import _unavailable_gate_blocks


@pytest.fixture(autouse=True)
def _no_capacity_ledger(monkeypatch):
    """These tests are about the commands; `test_gate_capacity.py` covers the lease."""
    monkeypatch.setattr(settings, "docker_capacity_enabled", False)


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as db:
        yield db


@pytest.fixture
def gate(session, tmp_path):
    """Run one gate command, with an optional timeout of its own."""
    ws = Workspace(slug="demo", name="Demo", repo_path=str(tmp_path))
    ticket = Ticket(id="tid", external_id="T-1", workspace_id="ws", title="Test")

    def run(command: str, timeout: int | None = None):
        gates = GatesConfig(
            enabled=True,
            commands=[command],
            command_timeouts={command: timeout} if timeout else {},
        )
        profile = OrchestrationProfile(slug="demo", gates=gates)
        return run_transition_gates(session, profile, ws, ticket, from_stage="a", to_stage="b")

    return run


#: First run: leave a marker and hang. Any later run: pass at once.
SLOW_ONCE = "sh -c 'if [ -e marker ]; then exit 0; fi; touch marker; sleep 5'"


# ---- a timeout of its own -------------------------------------------------


def test_a_command_named_in_the_profile_gets_its_own_timeout():
    gates = GatesConfig(commands=["fast", "slow"], command_timeouts={"slow": 900})
    assert gates.timeout_for("slow") == 900
    assert gates.timeout_for("fast") == DEFAULT_GATE_TIMEOUT_SECONDS


def test_the_runner_hands_each_command_its_own_timeout(gate):
    with patch.object(gate_runner.subprocess, "run", wraps=gate_runner.subprocess.run) as run:
        gate("true", timeout=7)
    assert run.call_args.kwargs["timeout"] == 7


def test_a_command_with_no_timeout_of_its_own_gets_the_default(gate):
    with patch.object(gate_runner.subprocess, "run", wraps=gate_runner.subprocess.run) as run:
        gate("true")
    assert run.call_args.kwargs["timeout"] == DEFAULT_GATE_TIMEOUT_SECONDS


def test_a_timeout_for_a_command_that_is_not_listed_is_refused():
    with pytest.raises(ValidationError, match="not in gates.commands"):
        GatesConfig(commands=["ruff check"], command_timeouts={"ruff  check": 60})


def test_a_timeout_must_be_positive():
    with pytest.raises(ValidationError):
        GatesConfig(commands=["x"], command_timeouts={"x": 0})


def test_the_editor_keeps_a_timeout_and_drops_one_whose_command_it_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "repo_root", tmp_path)
    ws = Workspace(slug="timeouts", name="Timeouts", repo_path=".")
    path = orchestration_dir() / f"{ws.slug}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "slug: timeouts\n"
        "gates:\n"
        "  enabled: true\n"
        "  commands: [suite, probe]\n"
        "  command_timeouts: {suite: 900, probe: 30}\n",
        encoding="utf-8",
    )

    update_gates_config(ws, GatesConfig(enabled=True, commands=["suite"]))

    assert yaml.safe_load(path.read_text())["gates"]["command_timeouts"] == {"suite": 900}
    assert resolve_orchestration_profile(ws).gates.timeout_for("suite") == 900


def test_the_lease_outlives_every_command_and_its_retry():
    gates = GatesConfig(commands=["a", "b"], command_timeouts={"a": 1000})
    assert _lease_ttl(gates, ["a", "b"]) >= 2 * (1000 + DEFAULT_GATE_TIMEOUT_SECONDS) + (
        2 * DEFAULT_GATE_TIMEOUT_SECONDS
    )


# ---- a timeout is its own outcome -------------------------------------------


def test_a_timeout_is_timeout_naming_the_budget_and_the_host_load(gate):
    result = gate("sleep 5", timeout=1)

    assert result.ok is False
    assert result.outcome is GateOutcome.TIMEOUT
    assert "timed out after 1s" in result.message
    assert "load average" in result.message
    assert result.outcome.reached_no_verdict


def test_the_host_load_is_named_from_the_load_average():
    with (
        patch.object(gate_runner.os, "getloadavg", return_value=(29.84, 1.0, 1.0)),
        patch.object(gate_runner.os, "cpu_count", return_value=10),
    ):
        assert gate_runner._host_load() == "load average 29.8 on 10 CPUs"
    with patch.object(gate_runner.os, "getloadavg", side_effect=OSError):
        assert gate_runner._host_load() == "host load unknown"


def test_a_failure_and_a_missing_tool_are_not_timeouts(gate):
    assert gate("sh -c 'exit 1'").outcome is GateOutcome.FAILED
    assert gate("no-such-gate-binary-2487").outcome is GateOutcome.UNAVAILABLE


def test_a_timed_out_commands_last_output_is_kept(gate):
    result = gate(
        'sh -c \'head -c 5000 /dev/zero | tr "\\0" x; echo; echo LAST-WORDS; '
        "echo on-stderr >&2; sleep 5'",
        timeout=1,
    )

    assert result.outcome is GateOutcome.TIMEOUT
    assert result.stdout.endswith("LAST-WORDS")
    assert len(result.stdout) == TIMEOUT_OUTPUT_TAIL
    assert result.stderr == "on-stderr"


def test_a_tail_cut_inside_a_utf8_sequence_still_reads():
    assert gate_runner._tail("é".encode()[:1] + b" end") == "\ufffd end"
    assert gate_runner._tail(None) == ""


# ---- retried once -------------------------------------------------------------


def test_a_timeout_is_retried_once_and_the_retry_recorded(gate):
    result = gate(SLOW_ONCE, timeout=1)

    assert result.ok is True and result.outcome is GateOutcome.PASSED
    assert result.timeout_retries == (SLOW_ONCE,)
    assert "passed only on a retry" in result.message


def test_a_timeout_on_the_retry_too_blocks_saying_so(gate):
    with patch.object(gate_runner, "_run_command", wraps=gate_runner._run_command) as run:
        result = gate("sleep 5", timeout=1)

    assert run.call_count == 2
    assert result.outcome is GateOutcome.TIMEOUT
    assert result.message.endswith("on its retry too")
    assert result.timeout_retries == ("sleep 5",)


@pytest.mark.parametrize("command", ["no-such-gate-binary-2487", "sh -c 'exit 1'"])
def test_an_unavailable_or_failed_gate_is_not_retried(gate, command):
    with patch.object(gate_runner, "_run_command", wraps=gate_runner._run_command) as run:
        result = gate(command)

    assert run.call_count == 1
    assert result.timeout_retries == ()


def test_the_retry_is_on_the_gate_evaluation_event(db_session, monkeypatch, tmp_path):
    ticket, profile = _setup_ticket_at_test_break(db_session, tmp_path, "gate-timeout-retry")
    profile.gates.commands = [SLOW_ONCE]
    profile.gates.command_timeouts = {SLOW_ONCE: 1}
    monkeypatch.chdir(tmp_path)

    _run_stage(db_session, monkeypatch, ticket, profile)

    payload = json.loads(_gate_events(db_session, ticket)[0].payload_json)
    assert payload["outcome"] == "passed"
    assert payload["timeout_retries"] == [SLOW_ONCE]


# ---- who it goes to --------------------------------------------------------


def test_a_timeout_blocks_for_a_person_as_harness_not_for_the_agent(db_session):
    ticket = _unavailable_gate_blocks(
        db_session,
        "Gate command timed out after 5s (load average 30.1 on 10 CPUs), on its retry too",
        outcome=GateOutcome.TIMEOUT,
    )

    assert ticket.state is TicketState.BLOCKED
    assert ticket.block_kind is BlockKind.HARNESS
    assert "timed out twice" in ticket.blocking_issues
    assert "Raise its timeout" in ticket.blocking_issues


def test_a_timed_out_gate_filed_as_work_is_refiled_as_harness(db_session):
    ticket = _unavailable_gate_blocks(
        db_session, "Gate command timed out after 5s, on its retry too", outcome=GateOutcome.TIMEOUT
    )
    ticket.block_kind = BlockKind.WORK
    db_session.add(ticket)
    db_session.commit()

    assert [v.subject_id for v in UNAVAILABLE_GATES_ARE_NOT_WORK.detect(db_session)] == [ticket.id]


def test_a_timeout_is_a_harness_attempt_in_the_gate_episodes():
    timed_out = ev(GateOutcome.TIMEOUT, "Gate command timed out after 300s", minute=0)
    [episode] = build_episodes([timed_out, ev(GateOutcome.PASSED, minute=5)])
    assert episode.attempts == 2
    assert episode.failure_kinds == [GateFailureKind.HARNESS]


def test_a_timeout_is_a_failed_gate_on_the_workspace_activity():
    event = DomainEvent(
        type=EventType.GATE_EVALUATED,
        payload_json=json.dumps({"outcome": "timeout", "stage_key": "implement"}),
    )
    assert _highlight(event) == (ActivityHighlightKind.GATE_FAILED, "implement")
