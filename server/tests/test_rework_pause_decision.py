"""A stopped rework loop must hand a person a decision they can actually make.

Written from a live failure on blobert's `blob-procedural-sdf-36`. Four rounds
bounced `implement` <-> `script_review` and tripped `MAX_REWORK_REROUTES`. Only
the first round was a review finding — the other three were a reviewer that
exited 0 without printing its `<<<LOREGARDEN_STAGE_REPORT>>>` block (twice) and
`sqlite3.OperationalError: database is locked` (once), while every reviewer that
did report said *pass*.

The pause that produced was unusable in three separate ways, one per test group
below:

* the missing report block counted as a rejection, so infrastructure noise spent
  the loop budget the cap was measuring;
* the pause was filed as a HUMAN_ACTION handover, because the control plane's own
  message ("Paused for a human") matched the handover heuristic — so the card
  carried an empty `PreparedAction` and told an agent that never existed to
  prepare one, and neither of its buttons touched the ticket;
* `loregarden_requeue_ticket`, the tool left to escape with, reported success on
  a stage other than the current one while leaving the cursor on the blocked
  stage.
"""

import pytest
from loregarden.core.workflow_loader import get_template_stages, sync_workflow_templates
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
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
from loregarden.services.orchestration import ApprovalService
from loregarden.services.parallel_stage import member_result
from loregarden.services.rework_feedback import (
    MAX_REWORK_REROUTES,
    record_rework_feedback,
    rework_reroute_count,
)
from loregarden.services.workflow_state import initial_stages_json, parse_stage_map
from sqlmodel import Session, select


def _report(status: str, confidence: float, **extra: str) -> str:
    fields = "".join(f', "{k}": "{v}"' for k, v in extra.items())
    return (
        "Narrative output.\n"
        "<<<LOREGARDEN_STAGE_REPORT>>>\n"
        f'{{"status": "{status}", "confidence": {confidence}{fields}}}\n'
        "<<<END_STAGE_REPORT>>>\n"
    )


@pytest.fixture
def review_ticket(db_session: Session):
    """A ticket parked mid-`script_review`, the parallel stage the real one hit.

    Built in a fixture rather than inline so a broken setup is an ERROR that
    pytest reports, not a failed assertion attributed to the behaviour under
    test.
    """
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
    ).first()
    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert template and workspace
    stages = get_template_stages(template)

    ticket = Ticket(
        external_id="rework-pause-decision",
        workspace_id=workspace.id,
        title="Procedural locomotion drivers",
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
        run_code="orch_pause",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        current_stage_key="script_review",
    )
    db_session.add(orch_run)
    db_session.commit()
    db_session.refresh(orch_run)

    return ticket, orch_run, next(s for s in stages if s.key == "script_review")


def _pause_approval(db_session: Session, ticket: Ticket) -> Approval | None:
    return db_session.exec(
        select(Approval).where(
            Approval.ticket_id == ticket.id,
            Approval.kind == ApprovalKind.REWORK_PAUSE,
        )
    ).first()


def _fill_ledger_to_cap(db_session: Session, ticket: Ticket) -> None:
    for index in range(MAX_REWORK_REROUTES):
        record_rework_feedback(
            db_session,
            ticket,
            target_stage="implement",
            from_stage="script_review",
            context=f"round {index}: a real finding",
        )


# --------------------------------------------------------------------------- #
# A reviewer whose verdict cannot be read has not rejected anything            #
# --------------------------------------------------------------------------- #


def test_missing_stage_report_is_transient_not_a_rejection():
    """The exact shape of three of the four rounds that paused the real ticket.

    Fail-closed is unchanged — the member still carries a failure, so the stage
    cannot settle DONE. What changes is the *kind* of failure: unknown, not
    negative, so it pauses for a retry instead of rerouting the work upstream.
    """
    result = member_result(
        "static_qa", status=RunStatus.SUCCEEDED, stdout="I reviewed it and it is fine", stderr=""
    )

    assert result.failure, "a member that reported nothing must not count as a pass"
    assert result.transient is True
    assert result.report is None


def test_a_reported_rejection_is_still_a_rejection():
    """The control for the test above: this change must not blunt a real reject."""
    result = member_result(
        "static_qa",
        status=RunStatus.SUCCEEDED,
        stdout=_report("needs_rework", 0.9, reroute_to_stage="implement"),
        stderr="",
    )

    assert result.failure
    assert result.transient is False


def test_unreadable_reviewer_does_not_spend_the_loop_budget(
    db_session: Session, review_ticket, monkeypatch
):
    """The whole point of the classification: no ledger entry, so no round spent.

    Three rounds of this is what took the real ticket to its cap while every
    reviewer that managed to report said pass.
    """
    from loregarden.agents.executors.cli import CliAgentExecutor

    ticket, orch_run, review_def = review_ticket

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        run.status = RunStatus.SUCCEEDED
        run.stdout = "" if run.agent_id == "static_qa" else _report("pass", 0.9)
        run.stderr = ""
        self.session.add(run)
        self.session.commit()
        return run

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)

    ok, _ = BuiltinOrchestrator(db_session)._execute_parallel_stage(
        ticket, orch_run, review_def, "script_review"
    )
    db_session.refresh(ticket)

    assert ok is False, "a stage nobody reported on has not passed"
    assert rework_reroute_count(db_session, ticket, "implement") == 0
    assert ticket.workflow_stage_key == "script_review", "the work was not sent back"


# --------------------------------------------------------------------------- #
# The pause is a decision, and both answers move the ticket                    #
# --------------------------------------------------------------------------- #


def test_cap_files_a_decision_not_an_empty_handover(
    db_session: Session, review_ticket, monkeypatch
):
    """The card the real ticket produced said, to a person: "State what you
    already tried, what you built to reduce the remaining work, and the command
    to run." Nobody had been asked to prepare anything — the control plane wrote
    that block itself."""
    from loregarden.agents.executors.cli import CliAgentExecutor

    ticket, orch_run, review_def = review_ticket
    _fill_ledger_to_cap(db_session, ticket)

    def fake_execute(self, run: AgentRun, worker_ticket: Ticket, **kwargs):
        run.status = RunStatus.SUCCEEDED
        run.stdout = (
            _report("needs_rework", 0.9, reroute_to_stage="implement", reroute_context="round four")
            if run.agent_id == "static_qa"
            else _report("pass", 0.95)
        )
        run.stderr = ""
        self.session.add(run)
        self.session.commit()
        return run

    monkeypatch.setattr(CliAgentExecutor, "execute", fake_execute)

    BuiltinOrchestrator(db_session)._execute_parallel_stage(
        ticket, orch_run, review_def, "script_review"
    )
    db_session.refresh(ticket)

    assert ticket.state == TicketState.BLOCKED
    handover = db_session.exec(
        select(Approval).where(
            Approval.ticket_id == ticket.id, Approval.kind == ApprovalKind.HUMAN_ACTION
        )
    ).first()
    assert handover is None, "the control plane's own pause is not a handover of human work"

    pause = _pause_approval(db_session, ticket)
    assert pause is not None
    assert pause.stage_key == "script_review"
    # The rounds are ON the card, not behind a pointer to artifacts.
    assert "round 0: a real finding" in pause.impact
    assert "round four" in pause.impact


def test_the_pause_card_offers_somewhere_to_route(db_session: Session, review_ticket):
    """A reject with nowhere to send the work is the dead end this replaces."""
    from loregarden.services.approval_views import approval_to_view
    from loregarden.services.rework_pause import file_rework_pause

    ticket, _, _ = review_ticket
    pause = file_rework_pause(
        db_session, ticket, stage_key="script_review", target_stage="implement", message="Paused."
    )

    view = approval_to_view(db_session, pause)

    assert view["kind"] == "rework_pause"
    assert "implement" in {option["key"] for option in view["route_options"]}


def test_approving_accepts_the_stage_and_clears_the_block(db_session: Session, review_ticket):
    """The move that had to be made by hand on the real ticket: every reviewer
    passed at the reviewed commit, so accept the stage and carry on."""
    from loregarden.services.rework_pause import file_rework_pause

    ticket, _, _ = review_ticket
    _fill_ledger_to_cap(db_session, ticket)
    ticket.blocking_issues = "Rework loop: 'implement' has been rerouted 3× ..."
    ticket.state = TicketState.BLOCKED
    db_session.add(ticket)
    db_session.commit()
    pause = file_rework_pause(
        db_session, ticket, stage_key="script_review", target_stage="implement", message="Paused."
    )

    # `_resume_orchestration` short-circuits because the fixture's run is still
    # active, so this covers the state change and NOT the dispatch that follows
    # it — that machinery is the gate path's, unchanged here, and exercising it
    # would check a branch out for real.
    ApprovalService(db_session).resolve(pause.id, approved=True)
    db_session.refresh(ticket)

    instance = db_session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).first()
    stages = get_template_stages(
        db_session.exec(
            select(WorkflowTemplate).where(WorkflowTemplate.slug == "blobert-tdd")
        ).first()
    )
    assert parse_stage_map(instance, stages)["script_review"] is StageStatus.DONE
    assert ticket.blocking_issues == "", "the resolved pause must not leave its reason behind"
    assert ticket.state != TicketState.BLOCKED


def test_approving_clears_the_block_before_state_is_re_derived(db_session: Session, review_ticket):
    """Order matters, and the happy path hides it.

    `set_stage_status` reconciles `ticket.state` on its way out, and
    `_derive_ticket_state` calls any ticket BLOCKED whose `blocking_issues` is
    set while the cursor sits on a BLOCKED/RUNNING/AWAITING stage. Clearing the
    text after the status write therefore derives from a pause already resolved.
    It looks fine when the next stage is PENDING — which is why this pins the
    case where it is not, by leaving the stage after `script_review` AWAITING.
    """
    from loregarden.services.rework_pause import file_rework_pause
    from loregarden.services.workflow_state import set_stage_status

    ticket, _, _ = review_ticket
    svc = ApprovalService(db_session)
    instance, stages = svc.orchestration._resolve_stages(ticket)
    set_stage_status(ticket, instance, stages, "ac_gate", StageStatus.AWAITING)
    ticket.blocking_issues = "Rework loop: 'implement' has been rerouted 3x ..."
    ticket.state = TicketState.BLOCKED
    db_session.add_all([ticket, instance])
    db_session.commit()

    pause = file_rework_pause(
        db_session, ticket, stage_key="script_review", target_stage="implement", message="Paused."
    )
    svc.resolve(pause.id, approved=True)
    db_session.refresh(ticket)

    assert ticket.blocking_issues == ""
    assert ticket.state != TicketState.BLOCKED, (
        "state was derived from the pause message this resolution just cleared"
    )


def test_resolving_gives_the_loop_its_budget_back(db_session: Session, review_ticket):
    """Otherwise a resolved pause buys one round before asking the identical
    question again, and the operator is consulted every round forever."""
    from loregarden.services.rework_pause import file_rework_pause

    ticket, _, _ = review_ticket
    _fill_ledger_to_cap(db_session, ticket)
    assert rework_reroute_count(db_session, ticket, "implement") == MAX_REWORK_REROUTES

    pause = file_rework_pause(
        db_session, ticket, stage_key="script_review", target_stage="implement", message="Paused."
    )
    ApprovalService(db_session).resolve(pause.id, approved=False, response_text="go again")
    db_session.refresh(ticket)

    assert rework_reroute_count(db_session, ticket, "implement") == 0
    # And the reject actually moved the work, rather than only clearing a
    # counter: a budget reset on a ticket still parked at the blocked stage
    # would be the same dead end with a tidier ledger.
    assert ticket.workflow_stage_key == "implement"
    assert "go again" in ticket.blocking_issues


def test_the_reset_keeps_the_feedback_the_next_round_needs(db_session: Session, review_ticket):
    """A reset that cleared the budget by deleting the rows would clear it by
    throwing away the findings the rework is supposed to act on."""
    from loregarden.services.rework_feedback import render_rework_feedback
    from loregarden.services.rework_pause import file_rework_pause

    ticket, _, _ = review_ticket
    _fill_ledger_to_cap(db_session, ticket)
    pause = file_rework_pause(
        db_session, ticket, stage_key="script_review", target_stage="implement", message="Paused."
    )

    ApprovalService(db_session).resolve(pause.id, approved=False)

    assert "round 0: a real finding" in render_rework_feedback(db_session, ticket, "implement")


def test_an_unattended_run_cannot_sign_off_its_own_pause(db_session: Session, review_ticket):
    """The reason this is not a WORKFLOW_GATE. `auto_resolve_awaiting_gate` looks
    for a pending gate on the stage; had the pause been one, an auto_approve run
    would approve it and walk through the cap raised to stop it looping."""
    from loregarden.services.rework_pause import file_rework_pause
    from loregarden.services.subtree_auto_run import auto_resolve_awaiting_gate

    ticket, orch_run, _ = review_ticket
    pause = file_rework_pause(
        db_session, ticket, stage_key="script_review", target_stage="implement", message="Paused."
    )

    assert auto_resolve_awaiting_gate(db_session, ticket, orch_run, "script_review") is False
    db_session.refresh(pause)
    assert pause.status is ApprovalStatus.PENDING

    with pytest.raises(ValueError, match="workflow-gate"):
        ApprovalService(db_session).auto_resolve(pause.id, orchestration_run_id=orch_run.id)


# --------------------------------------------------------------------------- #
# Requeue must not report success having left the cursor behind               #
# --------------------------------------------------------------------------- #


def test_requeue_of_another_stage_moves_the_cursor(db_session: Session, review_ticket):
    """`stage_key` + `stage_status` sets one stage's status and returns without
    touching the cursor, so requeuing `ac_gate` left the ticket pointing at the
    blocked `script_review` and the next start resumed exactly where it stuck."""
    from loregarden.mcp.ticket_ops_tools import _requeue_ticket
    from loregarden.services.orchestration_callbacks import OrchestrationCallbackService

    ticket, _, _ = review_ticket
    svc = OrchestrationCallbackService(db_session)

    _requeue_ticket(
        db_session,
        svc,
        {"ticket_id": ticket.id, "stage_key": "ac_gate", "reason": "script_review really passed"},
    )
    db_session.refresh(ticket)

    assert ticket.workflow_stage_key == "ac_gate"


def test_requeue_says_when_another_stage_still_holds_the_ticket(db_session: Session, review_ticket):
    """Clearing one stage's block while another stays BLOCKED reads as "the block
    is gone" unless the outcome says otherwise."""
    import json

    from loregarden.mcp.ticket_ops_tools import _requeue_ticket
    from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
    from loregarden.services.workflow_state import set_stage_status

    ticket, _, _ = review_ticket
    svc = OrchestrationCallbackService(db_session)
    instance, stages = svc.orch._resolve_stages(ticket)
    set_stage_status(ticket, instance, stages, "script_review", StageStatus.BLOCKED)
    db_session.add_all([ticket, instance])
    db_session.commit()

    payload = json.loads(
        _requeue_ticket(
            db_session,
            svc,
            {"ticket_id": ticket.id, "stage_key": "ac_gate", "reason": "moving on"},
        )
    )

    assert payload["requeued"]["blocked_stages"] == ["script_review"]
    assert "stage 'script_review' still blocked" in payload["requeued"]["note"]
