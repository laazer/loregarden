"""Every transition-gate evaluation must leave an auditable trace.

Ticket 88: builtin_orchestrator.py discarded a passing GateRunResult's message
and never recorded anything at all for a gate that never ran. A passing gate
and a gate that never ran both produced zero rows everywhere — this file pins
a GateEvaluated domain event to fire on *every* evaluation, carrying the
outcome (passed | skipped | disabled | failed) and the preserved message.
"""

import json
import stat

from loregarden.core.workflow_loader import get_template_stages, sync_workflow_templates
from loregarden.models.domain import (
    AgentRun,
    Artifact,
    DomainEvent,
    EventType,
    OrchestrationRun,
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
    db_session: Session, tmp_path, external_id: str
) -> tuple[Ticket, OrchestrationProfile]:
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).first()
    assert template is not None

    ws = Workspace(slug=external_id, name=external_id, repo_path=str(tmp_path))
    db_session.add(ws)
    db_session.commit()
    db_session.refresh(ws)

    stages = get_template_stages(template)
    ticket = Ticket(
        external_id=external_id,
        workspace_id=ws.id,
        title="Gate domain event test",
        description="Verify a gate evaluation always emits a domain event",
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

    profile = OrchestrationProfile(slug=external_id, gates=GatesConfig(enabled=True))
    return ticket, profile


def _run_stage(
    db_session: Session, monkeypatch, ticket: Ticket, profile: OrchestrationProfile
) -> None:
    from loregarden.agents.executors.cli import CliAgentExecutor

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        return self.orchestration.complete_run(
            run,
            status=RunStatus.SUCCEEDED,
            stdout=_stage_report("pass", 0.95),
            stderr="",
        )

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)
    BuiltinOrchestrator(db_session).execute(ticket, profile, max_stages=1)


def _gate_events(db_session: Session, ticket: Ticket) -> list[DomainEvent]:
    return list(
        db_session.exec(
            select(DomainEvent).where(
                DomainEvent.ticket_id == ticket.id,
                DomainEvent.type == EventType.GATE_EVALUATED,
            )
        ).all()
    )


def _context_artifacts(db_session: Session, ticket: Ticket) -> list[Artifact]:
    return list(
        db_session.exec(
            select(Artifact).where(Artifact.ticket_id == ticket.id, Artifact.kind == "context")
        ).all()
    )


def test_passing_and_skipped_gates_render_distinguishable_context_artifacts(
    db_session: Session, monkeypatch, tmp_path
):
    """AC: the UI must distinguish 'gate skipped' from 'gate passed'. The
    context tab (client ArtifactView) renders any kind="context" artifact's
    title/rows generically — a passing gate must leave one with 'passed' in
    the title, and a skipped one with 'skipped', so they read differently
    without any client change.
    """
    passed_repo = tmp_path / "passed"
    passed_repo.mkdir()
    skipped_repo = tmp_path / "skipped"
    skipped_repo.mkdir()

    passed_ticket, passed_profile = _setup_ticket_at_test_break(
        db_session, passed_repo, "gate-ui-passed"
    )
    passed_profile.gates.commands = ["true"]
    _run_stage(db_session, monkeypatch, passed_ticket, passed_profile)

    skipped_ticket, skipped_profile = _setup_ticket_at_test_break(
        db_session, skipped_repo, "gate-ui-skipped"
    )
    _run_stage(db_session, monkeypatch, skipped_ticket, skipped_profile)

    passed_titles = [a.title for a in _context_artifacts(db_session, passed_ticket)]
    skipped_titles = [a.title for a in _context_artifacts(db_session, skipped_ticket)]

    assert any("passed" in t.lower() for t in passed_titles)
    assert not any("skipped" in t.lower() for t in passed_titles)
    assert any("skipped" in t.lower() for t in skipped_titles)
    assert not any("passed" in t.lower() for t in skipped_titles)


def test_passing_gate_emits_gate_evaluated_event_with_passed_outcome(
    db_session: Session, monkeypatch, tmp_path
):
    ticket, profile = _setup_ticket_at_test_break(db_session, tmp_path, "gate-event-passed")
    profile.gates.commands = ["true"]

    _run_stage(db_session, monkeypatch, ticket, profile)

    events = _gate_events(db_session, ticket)
    assert len(events) == 1
    payload = json.loads(events[0].payload_json)
    assert payload["outcome"] == "passed"
    assert payload["message"]  # preserved, not collapsed to ""
    assert payload.get("from_stage", payload.get("stage_key")) == "test_break"


def test_gates_disabled_still_emits_gate_evaluated_event_with_disabled_outcome(
    db_session: Session, monkeypatch, tmp_path
):
    # This is the crux of the bug: today, disabled gates never call
    # run_transition_gates at all, so "gates never ran" leaves zero rows —
    # identical to a gate that ran and passed.
    ticket, profile = _setup_ticket_at_test_break(db_session, tmp_path, "gate-event-disabled")
    profile.gates.enabled = False

    _run_stage(db_session, monkeypatch, ticket, profile)

    events = _gate_events(db_session, ticket)
    assert len(events) == 1
    payload = json.loads(events[0].payload_json)
    assert payload["outcome"] == "disabled"


def test_zero_commands_configured_emits_skipped_not_passed(
    db_session: Session, monkeypatch, tmp_path
):
    # gates.enabled=True with nothing configured to run must not read the
    # same as an actually-executed passing gate.
    ticket, profile = _setup_ticket_at_test_break(db_session, tmp_path, "gate-event-skipped")

    _run_stage(db_session, monkeypatch, ticket, profile)

    events = _gate_events(db_session, ticket)
    assert len(events) == 1
    payload = json.loads(events[0].payload_json)
    assert payload["outcome"] == "skipped"
    assert payload["outcome"] != "passed"


def test_disabled_outcome_context_artifact_is_distinguishable_from_skipped_too(
    db_session: Session, monkeypatch, tmp_path
):
    """AC only names 'skipped' vs 'passed' explicitly, but 'disabled' must be
    its own third bucket too — a workspace with gates turned off entirely is a
    different fact from one that left them on but configured nothing to run,
    and an operator scanning the context tab needs to tell those apart.
    """
    disabled_repo = tmp_path / "disabled"
    disabled_repo.mkdir()
    skipped_repo = tmp_path / "skipped2"
    skipped_repo.mkdir()

    disabled_ticket, disabled_profile = _setup_ticket_at_test_break(
        db_session, disabled_repo, "gate-ui-disabled"
    )
    disabled_profile.gates.enabled = False
    _run_stage(db_session, monkeypatch, disabled_ticket, disabled_profile)

    skipped_ticket, skipped_profile = _setup_ticket_at_test_break(
        db_session, skipped_repo, "gate-ui-skipped2"
    )
    _run_stage(db_session, monkeypatch, skipped_ticket, skipped_profile)

    disabled_titles = [a.title for a in _context_artifacts(db_session, disabled_ticket)]
    skipped_titles = [a.title for a in _context_artifacts(db_session, skipped_ticket)]

    assert any("disabled" in t.lower() for t in disabled_titles)
    assert not any("skipped" in t.lower() for t in disabled_titles)
    assert any("skipped" in t.lower() for t in skipped_titles)
    assert not any("disabled" in t.lower() for t in skipped_titles)


def test_repeated_gate_failures_across_bounded_retries_each_emit_a_distinct_event(
    db_session: Session, monkeypatch, tmp_path
):
    """Order-dependency / statefulness check: a gate that fails on every one of
    the bounded autofix-agent retries must leave one GATE_EVALUATED row per
    evaluation, not a single upserted-in-place row. Collapsing these would make
    'failed once and got fixed' indistinguishable from 'has been failing on
    every retry and still is' — exactly the kind of gap this ticket exists to
    close, just one level deeper (across time, not just across outcome kinds).
    """
    ticket, profile = _setup_ticket_at_test_break(db_session, tmp_path, "gate-event-retries")
    profile.gates.commands = ["false"]
    profile.gates.autofix_agent_fallback = True
    profile.gates.autofix_max_agent_attempts = 2

    # Round 1: fails, attempts=0 < max=2 -> rerouted back to test_break.
    _run_stage(db_session, monkeypatch, ticket, profile)
    # Round 2: fails again, attempts=1 < max=2 -> rerouted again.
    _run_stage(db_session, monkeypatch, ticket, profile)
    # Round 3: fails again, attempts=2 == max=2 -> exhausted, blocks.
    _run_stage(db_session, monkeypatch, ticket, profile)

    events = _gate_events(db_session, ticket)
    assert len(events) == 3
    assert all(json.loads(e.payload_json)["outcome"] == "failed" for e in events)


def test_failing_gate_emits_failed_event_with_preserved_message(
    db_session: Session, monkeypatch, tmp_path
):
    script = tmp_path / "fail.sh"
    script.write_text("#!/bin/sh\necho GATE_FAIL_MARKER_88 1>&2\nexit 1\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    ticket, profile = _setup_ticket_at_test_break(db_session, tmp_path, "gate-event-failed")
    profile.gates.commands = ["./fail.sh"]
    # Skip the automatic-fixer/agent-retry paths so exactly one evaluation
    # happens this pass.
    profile.gates.autofix_agent_fallback = False

    _run_stage(db_session, monkeypatch, ticket, profile)

    events = _gate_events(db_session, ticket)
    assert len(events) == 1
    payload = json.loads(events[0].payload_json)
    assert payload["outcome"] == "failed"
    assert "GATE_FAIL_MARKER_88" in payload["message"]


def test_gate_evaluation_does_not_claim_an_agent_run(db_session: Session, monkeypatch, tmp_path):
    """`run_id` on an event or artifact references `agent_runs`. A transition
    gate is evaluated by the orchestrator between stages, so there is no agent
    run to name — writing the orchestration run's id there points at a row that
    does not exist in the table the column references. The orchestration run
    still has to be recoverable, so it rides in the payload, as STAGE_STARTED
    already does.
    """
    ticket, profile = _setup_ticket_at_test_break(db_session, tmp_path, "gate-event-run-id")
    profile.gates.commands = ["true"]

    _run_stage(db_session, monkeypatch, ticket, profile)

    events = _gate_events(db_session, ticket)
    assert len(events) == 1
    assert events[0].run_id is None
    orchestration_run_id = json.loads(events[0].payload_json)["orchestration_run_id"]
    assert db_session.get(OrchestrationRun, orchestration_run_id) is not None

    gate_artifacts = [a for a in _context_artifacts(db_session, ticket) if "Gate" in a.title]
    assert gate_artifacts
    assert all(a.run_id is None for a in gate_artifacts)
