"""A transient/infrastructure agent failure (API/usage limit, overload) during a
parallel review stage must PAUSE the stage for a human/resume — not be treated as
a rework rejection and rerouted upstream, which wastes a cycle and (with the
rework loop cap) inches toward blocking for the wrong reason.
"""

from loregarden.core.workflow_loader import get_template_stages, sync_workflow_templates
from loregarden.models.domain import (
    AgentRun,
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
from loregarden.services.rework_feedback import rework_reroute_count
from loregarden.services.stage_report import is_transient_failure
from loregarden.services.workflow_state import initial_stages_json
from sqlmodel import Session, select

# A Claude CLI final result line for an API/usage-limit death: FAILED, no report.
API_ERROR_STDOUT = (
    '{"type":"result","subtype":"error","terminal_reason":"api_error",'
    '"usage":{"input_tokens":10},"uuid":"x"}'
)


# --------------------------------------------------------------------------- #
# Classifier unit behaviour                                                    #
# --------------------------------------------------------------------------- #


def test_is_transient_failure_detects_api_error_terminal_reason():
    assert is_transient_failure(API_ERROR_STDOUT, "") is True


def test_is_transient_failure_detects_usage_and_rate_limit_text():
    assert is_transient_failure("", "Claude usage limit reached. Try later.") is True
    assert is_transient_failure("", "429 rate limit exceeded") is True
    assert is_transient_failure("Overloaded", "") is True


def test_is_transient_failure_detects_cli_auth_failure():
    """A CLI that cannot authenticate never reached the model, so it has no
    opinion on the work — rerouting upstream on it loops plan against triage."""
    assert (
        is_transient_failure(
            "",
            "Error: Cursor couldn't find your saved login in the macOS keychain.\n"
            "Log out and sign back in: run `agent logout`, then start agent again.",
        )
        is True
    )
    assert (
        is_transient_failure(
            "",
            "Error: The macOS keychain item already exists "
            "(errSecDuplicateItem, security exit code 45).",
        )
        is True
    )
    assert is_transient_failure("", "Invalid API key · Please run /login") is True


def test_is_transient_failure_ignores_genuine_failures():
    # A real crash / assertion is not transient — must not be masked as retryable.
    assert is_transient_failure("", "Traceback (most recent call last): AssertionError") is False
    assert is_transient_failure("crashed", "") is False
    # A clean stage report that happens to mention a word must not trip it via
    # stdout alone — callers gate on FAILED status first, but be conservative:
    assert is_transient_failure('{"terminal_reason":"completed"}', "") is False


# --------------------------------------------------------------------------- #
# Integration: parallel review stage                                           #
# --------------------------------------------------------------------------- #


def _report(status: str, confidence: float, reroute_to_stage="", reroute_context="") -> str:
    extra = ""
    if reroute_to_stage:
        extra += f', "reroute_to_stage": "{reroute_to_stage}"'
    if reroute_context:
        extra += f', "reroute_context": "{reroute_context}"'
    return (
        "Narrative.\n<<<LOREGARDEN_STAGE_REPORT>>>\n"
        f'{{"status": "{status}", "confidence": {confidence}{extra}}}\n'
        "<<<END_STAGE_REPORT>>>\n"
    )


def _setup_review(db_session: Session):
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).first()
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert template and ws
    stages = get_template_stages(template)
    ticket = Ticket(
        external_id="transient-review",
        workspace_id=ws.id,
        title="Transient review",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="script_review",
        workflow_stage_status=StageStatus.RUNNING,
        next_agent="gdscript_reviewer",
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    db_session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key="script_review",
            stages_json=initial_stages_json(stages),
        )
    )
    orch_run = OrchestrationRun(
        run_code="orch_transient",
        ticket_id=ticket.id,
        workspace_id=ws.id,
        current_stage_key="script_review",
    )
    db_session.add(orch_run)
    db_session.commit()
    review_def = next(s for s in stages if s.key == "script_review")
    return ticket, orch_run, review_def


def test_transient_only_failure_blocks_stage_not_reroute(db_session: Session, monkeypatch):
    from loregarden.agents.executors.cli import CliAgentExecutor

    ticket, orch_run, review_def = _setup_review(db_session)

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        if run.agent_id == "static_qa":
            run.status = RunStatus.FAILED  # died on an API/usage limit, no report
            run.stdout = API_ERROR_STDOUT
            run.stderr = ""
        else:
            run.status = RunStatus.SUCCEEDED
            run.stdout = _report("pass", 0.95)
            run.stderr = ""
        self.session.add(run)
        self.session.commit()
        return run

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)

    builtin = BuiltinOrchestrator(db_session)
    ok, _ = builtin._execute_parallel_stage(ticket, orch_run, review_def, "script_review")

    assert ok is False
    db_session.refresh(ticket)
    # Paused for a human, NOT rerouted to implementation.
    assert ticket.state == TicketState.BLOCKED
    assert ticket.workflow_stage_key == "script_review"
    assert "transient" in ticket.blocking_issues.lower()
    # The loop budget must be untouched — no rework-ledger entry was recorded.
    assert rework_reroute_count(db_session, ticket, "implementation") == 0


def test_genuine_rejection_still_reroutes_despite_a_transient_sibling(
    db_session: Session, monkeypatch
):
    from loregarden.agents.executors.cli import CliAgentExecutor

    ticket, orch_run, review_def = _setup_review(db_session)

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        if run.agent_id == "static_qa":
            run.status = RunStatus.FAILED  # transient
            run.stdout = API_ERROR_STDOUT
        elif run.agent_id == "gdscript_reviewer":
            run.status = RunStatus.SUCCEEDED  # genuine rejection
            run.stdout = _report(
                "needs_rework",
                0.9,
                reroute_to_stage="implementation",
                reroute_context="Real finding: missing acid weak-point check",
            )
        else:
            run.status = RunStatus.SUCCEEDED
            run.stdout = _report("pass", 0.95)
        run.stderr = ""
        self.session.add(run)
        self.session.commit()
        return run

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)

    builtin = BuiltinOrchestrator(db_session)
    ok, _ = builtin._execute_parallel_stage(ticket, orch_run, review_def, "script_review")

    # A genuine rejection takes precedence over the transient sibling: reroute,
    # not block.
    assert ok is True
    db_session.refresh(ticket)
    assert ticket.state != TicketState.BLOCKED
    assert ticket.workflow_stage_key == "implementation"
    assert rework_reroute_count(db_session, ticket, "implementation") == 1
