"""The rework-feedback ledger: a stage reroute must deliver its *full* fix
direction to the re-run agent, accumulate across rounds, and cap a runaway loop.

Before this, the single-stage reroute path funnelled reroute_context through
``record_blocking_issue`` (200-char cap → an Errors-tab artifact the agent never
reads), so a verifier's precise fix direction reached the implementer as only
"see the Errors tab for details" and the implementer re-guessed every round.
"""

from loregarden.agents.stage_context import build_orchestration_context
from loregarden.core.workflow_loader import get_template_stages, sync_workflow_templates
from loregarden.models.domain import (
    AgentRun,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.rework_feedback import (
    MAX_REWORK_REROUTES,
    record_rework_feedback,
    render_rework_feedback,
    rework_reroute_count,
)
from loregarden.services.workflow_state import initial_stages_json, stages_up_to_done_json
from sqlmodel import Session, select

# A finding longer than record_blocking_issue's 200-char inline cap — the exact
# case that used to be discarded down to a pointer.
LONG_FINDING = (
    "The interim loregarden_create_ticket denial in permission_bridge.py is "
    "unconditional and fires for every PermissionBridgeRunner caller, including "
    "the interactive Ticket Studio chat which the AC requires to stay ALLOWED. "
    "Fix: gate the deny on self.track_workflow_stage so orchestrated stage runs "
    "are denied but interactive triage runs are exempt, and add a regression "
    "test exercising the triage path specifically."
)


def _ticket(session: Session) -> Ticket:
    ws = session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    if ws is None:
        ws = Workspace(slug="loregarden", name="Loregarden", repo_path="/tmp/x")
        session.add(ws)
        session.commit()
        session.refresh(ws)
    ticket = Ticket(
        external_id="rework-ledger-test",
        workspace_id=ws.id,
        title="Rework ledger test",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="implement",
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


# --------------------------------------------------------------------------- #
# Ledger unit behaviour                                                        #
# --------------------------------------------------------------------------- #


def test_ledger_stores_full_context_without_truncation(db_session: Session):
    ticket = _ticket(db_session)
    assert len(LONG_FINDING) > 200  # would be lost by the inline blocking_issues cap

    record_rework_feedback(
        db_session, ticket, target_stage="implement", from_stage="verify", context=LONG_FINDING
    )

    assert rework_reroute_count(db_session, ticket, "implement") == 1
    rendered = render_rework_feedback(db_session, ticket, "implement")
    assert rendered == LONG_FINDING  # verbatim, in full


def test_ledger_accumulates_distinct_rounds_and_dedupes_identical(db_session: Session):
    ticket = _ticket(db_session)
    record_rework_feedback(
        db_session, ticket, target_stage="implement", from_stage="verify", context=LONG_FINDING
    )
    record_rework_feedback(
        db_session, ticket, target_stage="implement", from_stage="verify", context=LONG_FINDING
    )  # identical recurrence
    record_rework_feedback(
        db_session,
        ticket,
        target_stage="implement",
        from_stage="review",
        context="Also: cursor/print-mode runs bypass the bridge entirely.",
    )

    # Every reroute counts toward the cap, even identical ones.
    assert rework_reroute_count(db_session, ticket, "implement") == 3

    rendered = render_rework_feedback(db_session, ticket, "implement")
    # Distinct findings both survive; the identical one is collapsed to a single
    # rendering (union of what was asked, without N copies of the same text).
    assert "track_workflow_stage" in rendered
    assert "bypass the bridge" in rendered
    assert rendered.count("track_workflow_stage") == 1
    assert "### Round 1" in rendered and "### Round 2" in rendered


def test_ledger_is_scoped_per_target_stage(db_session: Session):
    ticket = _ticket(db_session)
    record_rework_feedback(
        db_session, ticket, target_stage="implement", from_stage="verify", context="impl note"
    )
    record_rework_feedback(
        db_session, ticket, target_stage="test-design", from_stage="verify", context="test note"
    )
    assert rework_reroute_count(db_session, ticket, "implement") == 1
    assert rework_reroute_count(db_session, ticket, "test-design") == 1
    assert render_rework_feedback(db_session, ticket, "implement") == "impl note"


# --------------------------------------------------------------------------- #
# Re-run agent context                                                         #
# --------------------------------------------------------------------------- #


def test_stage_context_uses_full_ledger_over_truncated_blocking_issues(db_session: Session):
    ticket = _ticket(db_session)
    record_rework_feedback(
        db_session, ticket, target_stage="implement", from_stage="verify", context=LONG_FINDING
    )
    # Simulate the UI field having only the short pointer it keeps for long input.
    ticket.blocking_issues = "Stage 'verify' hit a blocking issue — see the Errors tab for details."

    run = AgentRun(
        run_code="r",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        stage_key="implement",
    )
    stage = WorkflowStageDef(key="implement", name="Implement", order=8)

    text = build_orchestration_context(ticket=ticket, run=run, stage_def=stage, session=db_session)
    assert "track_workflow_stage" in text  # the full fix direction is present
    assert "see the Errors tab" not in text  # not the content-free pointer


def test_stage_context_falls_back_to_blocking_issues_without_ledger(db_session: Session):
    ticket = _ticket(db_session)
    ticket.blocking_issues = "Short legacy feedback."
    run = AgentRun(
        run_code="r",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        stage_key="implement",
    )
    stage = WorkflowStageDef(key="implement", name="Implement", order=8)

    text = build_orchestration_context(ticket=ticket, run=run, stage_def=stage, session=db_session)
    assert "Short legacy feedback." in text


# --------------------------------------------------------------------------- #
# Integration: single-stage (verify-style) reroute — the truncation fix        #
# --------------------------------------------------------------------------- #


def _report(status: str, confidence: float, reroute_to_stage="", reroute_context="") -> str:
    extra = ""
    if reroute_to_stage:
        extra += f', "reroute_to_stage": "{reroute_to_stage}"'
    if reroute_context:
        safe = reroute_context.replace('"', '\\"')
        extra += f', "reroute_context": "{safe}"'
    return (
        "Narrative.\n<<<LOREGARDEN_STAGE_REPORT>>>\n"
        f'{{"status": "{status}", "confidence": {confidence}{extra}}}\n'
        "<<<END_STAGE_REPORT>>>\n"
    )


def _setup_single_stage(db_session: Session, tmp_path, *, stage_key: str, next_agent: str):
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).first()
    assert template is not None
    ws = Workspace(slug="rework-int", name="Rework Int", repo_path=str(tmp_path))
    db_session.add(ws)
    db_session.commit()
    db_session.refresh(ws)

    stages = get_template_stages(template)
    ticket = Ticket(
        external_id="rework-int",
        workspace_id=ws.id,
        title="Rework integration",
        description="drive a needs_rework reroute",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key=stage_key,
        workflow_stage_status=StageStatus.PENDING,
        next_agent=next_agent,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id=template.id,
        current_stage_key=stage_key,
        stages_json=stages_up_to_done_json(stages, "test_design"),
    )
    db_session.add(instance)
    db_session.commit()
    return ticket, stages


def test_single_stage_reroute_delivers_full_context_not_a_pointer(
    db_session: Session, monkeypatch, tmp_path
):
    """End-to-end truncation fix: a >200-char reroute_context reaches the re-run
    agent in full via the ledger, while ticket.blocking_issues stays a short UI
    pointer."""
    from loregarden.agents.executors.cli import CliAgentExecutor
    from loregarden.services.orchestration_profile import OrchestrationProfile

    ticket, stages = _setup_single_stage(
        db_session, tmp_path, stage_key="test_break", next_agent="test_breaker"
    )

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        return self.orchestration.complete_run(
            run,
            status=RunStatus.SUCCEEDED,
            stdout=_report(
                "needs_rework", 0.93, reroute_to_stage="test_design", reroute_context=LONG_FINDING
            ),
            stderr="",
        )

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)

    builtin = BuiltinOrchestrator(db_session)
    builtin.execute(ticket, OrchestrationProfile(slug="rework-int"), max_stages=1)
    db_session.refresh(ticket)

    assert ticket.workflow_stage_key == "test_design"
    # UI field: short pointer, not the wall of text.
    assert len(ticket.blocking_issues) <= 200
    # Ledger: full context, delivered to the re-run agent.
    assert rework_reroute_count(db_session, ticket, "test_design") == 1
    rerun = AgentRun(
        run_code="r2",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="test_designer",
        stage_key="test_design",
    )
    stage_def = next(s for s in stages if s.key == "test_design")
    text = build_orchestration_context(
        ticket=ticket, run=rerun, stage_def=stage_def, stages=stages, session=db_session
    )
    assert "track_workflow_stage" in text
    assert "regression test exercising the triage path" in text


def test_single_stage_reroute_blocks_for_human_at_cap(db_session: Session, monkeypatch, tmp_path):
    from loregarden.agents.executors.cli import CliAgentExecutor
    from loregarden.services.orchestration_profile import OrchestrationProfile

    ticket, stages = _setup_single_stage(
        db_session, tmp_path, stage_key="test_break", next_agent="test_breaker"
    )
    # Pre-seed the ledger to the cap so the next reroute is the (MAX+1)th.
    for i in range(MAX_REWORK_REROUTES):
        record_rework_feedback(
            db_session,
            ticket,
            target_stage="test_design",
            from_stage="test_break",
            context=f"prior round {i}",
        )

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        return self.orchestration.complete_run(
            run,
            status=RunStatus.SUCCEEDED,
            stdout=_report(
                "needs_rework",
                0.9,
                reroute_to_stage="test_design",
                reroute_context="same finding again",
            ),
            stderr="",
        )

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)

    builtin = BuiltinOrchestrator(db_session)
    builtin.execute(ticket, OrchestrationProfile(slug="rework-int"), max_stages=1)
    db_session.refresh(ticket)

    assert ticket.state == TicketState.BLOCKED
    assert "rework loop" in ticket.blocking_issues.lower()


# --------------------------------------------------------------------------- #
# Integration: parallel (review) reroute path                                  #
# --------------------------------------------------------------------------- #


def _setup_parallel_review(db_session: Session):
    from loregarden.models.domain import OrchestrationRun

    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).first()
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert template and ws
    stages = get_template_stages(template)
    ticket = Ticket(
        external_id="rework-parallel",
        workspace_id=ws.id,
        title="Rework parallel",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="script_review",
        workflow_stage_status=StageStatus.RUNNING,
        next_agent="gdscript_reviewer",
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id=template.id,
        current_stage_key="script_review",
        stages_json=initial_stages_json(stages),
    )
    db_session.add(instance)
    orch_run = OrchestrationRun(
        run_code="orch_rework",
        ticket_id=ticket.id,
        workspace_id=ws.id,
        current_stage_key="script_review",
    )
    db_session.add(orch_run)
    db_session.commit()
    review_def = next(s for s in stages if s.key == "script_review")
    return ticket, orch_run, review_def


def test_parallel_reroute_records_full_ledger_below_cap(db_session: Session, monkeypatch):
    from loregarden.agents.executors.cli import CliAgentExecutor

    ticket, orch_run, review_def = _setup_parallel_review(db_session)

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        if run.agent_id == "static_qa":
            run.status = RunStatus.SUCCEEDED
            run.stdout = _report(
                "needs_rework", 0.9, reroute_to_stage="implementation", reroute_context=LONG_FINDING
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

    assert ok is True
    db_session.refresh(ticket)
    assert ticket.workflow_stage_key == "implementation"
    assert ticket.state != TicketState.BLOCKED
    assert rework_reroute_count(db_session, ticket, "implementation") == 1
    assert render_rework_feedback(db_session, ticket, "implementation") == LONG_FINDING


def test_parallel_reroute_blocks_for_human_at_cap(db_session: Session, monkeypatch):
    from loregarden.agents.executors.cli import CliAgentExecutor

    ticket, orch_run, review_def = _setup_parallel_review(db_session)
    for i in range(MAX_REWORK_REROUTES):
        record_rework_feedback(
            db_session,
            ticket,
            target_stage="implementation",
            from_stage="script_review",
            context=f"prior {i}",
        )

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        if run.agent_id == "static_qa":
            run.status = RunStatus.SUCCEEDED
            run.stdout = _report(
                "needs_rework",
                0.9,
                reroute_to_stage="implementation",
                reroute_context="same finding yet again",
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

    assert ok is False
    db_session.refresh(ticket)
    assert ticket.state == TicketState.BLOCKED
    assert "rework loop" in ticket.blocking_issues.lower()
