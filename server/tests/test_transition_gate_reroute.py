"""A workflow transition gate (lint/static-analysis, todo/handoff, etc.)
failing after a stage's agent reports success must reroute the ticket back to
that same stage for another automatic pass, not hard-block it for a human —
distinct from an agent-reported fail/needs_rework, which is handled by
_advance_stage_after_run before this gate check ever runs.
"""

import json

from loregarden.core.workflow_loader import get_template_stages, sync_workflow_templates
from loregarden.models.domain import (
    AgentRun,
    Artifact,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.orchestration_profile import GatesConfig, OrchestrationProfile
from loregarden.services.workflow_state import stages_up_to_done_json
from sqlmodel import Session, select


def _stage_report(status: str, confidence: float) -> str:
    return (
        "Narrative output from the agent.\n"
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        f'{{"status": "{status}", "confidence": {confidence}}}\n'
        "<<<END_STAGE_REPORT>>>\n"
    )


def _setup_ticket_at_test_break(
    db_session: Session, tmp_path
) -> tuple[Ticket, OrchestrationProfile]:
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).first()
    assert template is not None

    ws = Workspace(slug="gate-reroute-test", name="Gate Reroute Test", repo_path=str(tmp_path))
    db_session.add(ws)
    db_session.commit()
    db_session.refresh(ws)

    stages = get_template_stages(template)
    ticket = Ticket(
        external_id="gate-reroute-test",
        workspace_id=ws.id,
        title="Gate reroute test",
        description="Verify a transition gate failure reroutes rather than hard-blocks",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="test_break",
        workflow_stage_status=StageStatus.PENDING,
        next_agent="test_breaker",
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id=template.id,
        current_stage_key="test_break",
        stages_json=stages_up_to_done_json(stages, "test_design"),
    )
    db_session.add(instance)
    db_session.commit()

    profile = OrchestrationProfile(
        slug="gate-reroute-test",
        gates=GatesConfig(enabled=True, commands=["false"]),
    )
    return ticket, profile


def test_gate_failure_reroutes_to_same_stage_instead_of_blocking(
    db_session: Session, monkeypatch, tmp_path
):
    from loregarden.agents.executors.cli import CliAgentExecutor

    ticket, profile = _setup_ticket_at_test_break(db_session, tmp_path)

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        return self.orchestration.complete_run(
            run,
            status=RunStatus.SUCCEEDED,
            stdout=_stage_report("pass", 0.95),
            stderr="",
        )

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)

    builtin = BuiltinOrchestrator(db_session)
    builtin.execute(ticket, profile, max_stages=1)

    db_session.refresh(ticket)
    assert ticket.state != TicketState.BLOCKED
    assert ticket.workflow_stage_key == "test_break"
    assert ticket.workflow_stage_status == StageStatus.PENDING
    assert ticket.blocking_issues


def test_gate_failure_keeps_blocking_issues_short_and_files_full_output_as_error_artifact(
    db_session: Session, monkeypatch, tmp_path
):
    """The workflow pane renders ticket.blocking_issues directly and verbatim —
    a raw lefthook/pylint/etc. dump there is unreadable. The full gate output
    belongs in the Errors tab (an Artifact with kind="error"), not inline."""
    from loregarden.agents.executors.cli import CliAgentExecutor

    ticket, profile = _setup_ticket_at_test_break(db_session, tmp_path)
    # Opt out of the inline agent-fix retry so this exercises the terminal
    # pause-for-human path, where blocking_issues is deliberately a short pointer.
    profile.gates.autofix_agent_fallback = False

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        return self.orchestration.complete_run(
            run,
            status=RunStatus.SUCCEEDED,
            stdout=_stage_report("pass", 0.95),
            stderr="",
        )

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)

    builtin = BuiltinOrchestrator(db_session)
    builtin.execute(ticket, profile, max_stages=1)

    db_session.refresh(ticket)
    assert len(ticket.blocking_issues) < 200
    assert "Errors tab" in ticket.blocking_issues
    assert "(command:" not in ticket.blocking_issues

    error_artifacts = db_session.exec(
        select(Artifact).where(Artifact.ticket_id == ticket.id, Artifact.kind == "error")
    ).all()
    assert len(error_artifacts) == 1
    content = json.loads(error_artifacts[0].content_json)
    assert content["stage_key"] == "test_break"
    assert "(command:" in content["message"]


def test_gate_passing_advances_normally(db_session: Session, monkeypatch, tmp_path):
    from loregarden.agents.executors.cli import CliAgentExecutor
    from loregarden.services.workflow_state import parse_stage_map

    ticket, profile = _setup_ticket_at_test_break(db_session, tmp_path)
    profile.gates.commands = ["true"]

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        return self.orchestration.complete_run(
            run,
            status=RunStatus.SUCCEEDED,
            stdout=_stage_report("pass", 0.95),
            stderr="",
        )

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)

    builtin = BuiltinOrchestrator(db_session)
    builtin.execute(ticket, profile, max_stages=1)

    db_session.refresh(ticket)
    instance, stages = builtin.orch._resolve_stages(ticket)
    stage_map = parse_stage_map(instance, stages)

    assert ticket.state != TicketState.BLOCKED
    assert not ticket.blocking_issues
    # A passing gate must not reroute test_break back to itself — it should be
    # marked DONE, same as if gates were disabled entirely.
    assert stage_map["test_break"] == StageStatus.DONE


def test_autofix_fixers_clear_gate_advances_without_reroute(
    db_session: Session, monkeypatch, tmp_path
):
    """A mechanical fixer that satisfies the gate on the second run makes the
    failure invisible: the stage advances, nothing is rerouted, and no
    blocking_issues are surfaced for a human to triage."""
    from loregarden.agents.executors.cli import CliAgentExecutor
    from loregarden.services.workflow_state import parse_stage_map

    ticket, profile = _setup_ticket_at_test_break(db_session, tmp_path)
    # Gate fails until a marker file exists; the fixer creates it.
    profile.gates.commands = ["test -f {workspace_root}/fixed.txt"]
    profile.gates.autofix_commands = ["touch {workspace_root}/fixed.txt"]

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        return self.orchestration.complete_run(
            run,
            status=RunStatus.SUCCEEDED,
            stdout=_stage_report("pass", 0.95),
            stderr="",
        )

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)

    builtin = BuiltinOrchestrator(db_session)
    builtin.execute(ticket, profile, max_stages=1)

    db_session.refresh(ticket)
    instance, stages = builtin.orch._resolve_stages(ticket)
    stage_map = parse_stage_map(instance, stages)

    assert (tmp_path / "fixed.txt").is_file()
    assert ticket.state != TicketState.BLOCKED
    assert not ticket.blocking_issues
    assert stage_map["test_break"] == StageStatus.DONE
    # No reroute artifact was filed — the fix was invisible.
    error_artifacts = db_session.exec(
        select(Artifact).where(Artifact.ticket_id == ticket.id, Artifact.kind == "error")
    ).all()
    assert error_artifacts == []


def test_autofix_agent_fallback_retries_inline_then_pauses_after_max_attempts(
    db_session: Session, monkeypatch, tmp_path
):
    """When mechanical fixers can't satisfy the gate, the stage is rerouted back
    to its own agent for a bounded number of inline retries before finally
    pausing for a human — the loop must not spin forever."""
    from loregarden.agents.executors.cli import CliAgentExecutor

    ticket, profile = _setup_ticket_at_test_break(db_session, tmp_path)
    profile.gates.commands = ["false"]  # never satisfiable
    profile.gates.autofix_agent_fallback = True
    profile.gates.autofix_max_agent_attempts = 2

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        return self.orchestration.complete_run(
            run,
            status=RunStatus.SUCCEEDED,
            stdout=_stage_report("pass", 0.95),
            stderr="",
        )

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)

    builtin = BuiltinOrchestrator(db_session)
    builtin.execute(ticket, profile, max_stages=10)

    db_session.refresh(ticket)
    # Initial run + exactly autofix_max_agent_attempts inline retries.
    runs = db_session.exec(
        select(AgentRun).where(AgentRun.ticket_id == ticket.id, AgentRun.stage_key == "test_break")
    ).all()
    assert len(runs) == 3
    # Exhausted: rerouted for rework and paused with a short human-facing pointer.
    assert ticket.state != TicketState.BLOCKED
    assert ticket.workflow_stage_key == "test_break"
    assert ticket.workflow_stage_status == StageStatus.PENDING
    assert "Errors tab" in ticket.blocking_issues
    assert len(ticket.blocking_issues) < 200


def test_autofix_agent_fallback_budget_persists_across_separate_orchestration_runs(
    db_session: Session, monkeypatch, tmp_path
):
    """The inline-retry budget must not reset just because a new orchestration
    run starts (e.g. an operator re-triggering Agents Assemble, or an
    auto-resume, after the first run paused) — otherwise a stage whose gate can
    never pass (a persistent environment issue, not something an agent can fix)
    gets another full budget of automatic attempts every time, cycling
    indefinitely instead of ever durably surfacing to a human.
    """
    from loregarden.agents.executors.cli import CliAgentExecutor

    ticket, profile = _setup_ticket_at_test_break(db_session, tmp_path)
    profile.gates.commands = ["false"]  # never satisfiable
    profile.gates.autofix_agent_fallback = True
    profile.gates.autofix_max_agent_attempts = 2

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        return self.orchestration.complete_run(
            run,
            status=RunStatus.SUCCEEDED,
            stdout=_stage_report("pass", 0.95),
            stderr="",
        )

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)

    builtin = BuiltinOrchestrator(db_session)
    builtin.execute(ticket, profile, max_stages=10)
    db_session.refresh(ticket)
    runs_after_first_call = db_session.exec(
        select(AgentRun).where(AgentRun.ticket_id == ticket.id, AgentRun.stage_key == "test_break")
    ).all()
    assert len(runs_after_first_call) == 3  # initial + 2 inline retries, budget exhausted

    # Simulate the ticket getting re-triggered by a fresh orchestration run
    # (e.g. the operator clicking Agents Assemble again) — a brand-new
    # BuiltinOrchestrator with no in-memory state from the first call.
    ticket.blocking_issues = ""
    ticket.workflow_stage_status = StageStatus.PENDING
    db_session.add(ticket)
    db_session.commit()

    BuiltinOrchestrator(db_session).execute(ticket, profile, max_stages=10)

    db_session.refresh(ticket)
    runs_after_second_call = db_session.exec(
        select(AgentRun).where(AgentRun.ticket_id == ticket.id, AgentRun.stage_key == "test_break")
    ).all()
    # Exactly one more run — the stage's own agent re-running once on the fresh
    # call — then straight to blocked. No *additional* inline auto-fix retries
    # were spent, since the persisted budget was already used up.
    assert len(runs_after_second_call) == len(runs_after_first_call) + 1
    assert ticket.workflow_stage_key == "test_break"
    assert ticket.workflow_stage_status == StageStatus.PENDING
    assert "Errors tab" in ticket.blocking_issues
