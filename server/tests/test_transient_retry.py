"""A single-agent stage whose run died of infrastructure gets a bounded
automatic re-dispatch instead of a blocked ticket.

The parallel path has classified transient failures since it was written; this is
the path that never asked. What it used to do with a run killed by a server
reload is what it did with any other failure: block the ticket, put the
provider's stderr in front of a human, and wait. 82 of 179 failed runs died that
way, each under a message that said to re-run the stage.

These tests pin both halves — the retry happens, and it stops. A retry with no
ceiling is not an improvement on blocking, it is a way to spend a night of CLI
runs on a CLI that can never authenticate.
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
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_profile import RetryBudgetConfig
from loregarden.services.rework_feedback import rework_reroute_count
from loregarden.services.stage_retry_budget import count_stage_dispatches
from loregarden.services.stage_transient_retry import (
    count_transient_retries,
    stage_rearmed_for_latest_run,
)
from loregarden.services.workflow_state import initial_stages_json
from sqlmodel import Session, select

TRANSIENT_RETRIES = RetryBudgetConfig().max_transient_retries

# The Claude CLI's own wording for an infrastructure death: FAILED, no report.
API_ERROR_STDOUT = (
    '{"type":"result","subtype":"error","terminal_reason":"api_error",'
    '"usage":{"input_tokens":10},"uuid":"x"}'
)
# The control plane reaping a run whose lease nobody renewed: infrastructure,
# and — unlike a server reload — not something the boot sweep already resumes.
LEASE_STDERR = "Agent run lease expired: nothing has renewed it for 11 minutes"
KEYCHAIN_STDERR = "Error: Cursor couldn't find your saved login in the macOS keychain."
# A restart. Classified transient, but deliberately NOT retried here: its
# recovery belongs to `orchestration_recovery`.
RELOAD_STDERR = (
    "Agent run interrupted before completion (server reload or worker stopped). "
    "Re-run the stage to continue."
)

PASS_REPORT = (
    "Narrative.\n<<<LOREGARDEN_STAGE_REPORT>>>\n"
    '{"status": "pass", "confidence": 0.95}\n'
    "<<<END_STAGE_REPORT>>>\n"
)
FAIL_REPORT = (
    "Narrative.\n<<<LOREGARDEN_STAGE_REPORT>>>\n"
    '{"status": "fail", "confidence": 0.9, "reroute_to_stage": "implement",'
    ' "reroute_context": "the work is wrong"}\n'
    "<<<END_STAGE_REPORT>>>\n"
)


def _ticket_on_stage(db_session: Session, stage_key: str = "testing") -> Ticket:
    """A ticket parked mid-run on a single-agent stage of the live template."""
    sync_workflow_templates(db_session)
    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "loregarden-tdd")
    ).first()
    ws = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert template and ws
    stages = get_template_stages(template)
    assert any(stage.key == stage_key for stage in stages)

    ticket = Ticket(
        external_id=f"transient-retry-{stage_key}",
        workspace_id=ws.id,
        title="Transient retry",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key=stage_key,
        workflow_stage_status=StageStatus.RUNNING,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    db_session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key=stage_key,
            stages_json=initial_stages_json(stages),
        )
    )
    db_session.commit()
    return ticket


def _run(db_session: Session, ticket: Ticket, stage_key: str = "testing") -> AgentRun:
    run = AgentRun(
        run_code=f"run-{ticket.id[:6]}-{count_transient_retries(db_session, ticket.id, stage_key)}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="test_designer",
        stage_key=stage_key,
        status=RunStatus.RUNNING,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run


def _die(db_session: Session, ticket: Ticket, *, stdout="", stderr="", status=RunStatus.FAILED):
    """Finish one run of `testing` the way the executor would, and settle it."""
    run = _run(db_session, ticket)
    OrchestrationService(db_session).complete_run(run, status=status, stdout=stdout, stderr=stderr)
    db_session.refresh(ticket)
    return run


# --------------------------------------------------------------------------- #
# The retry happens                                                            #
# --------------------------------------------------------------------------- #


def test_an_infrastructure_death_re_arms_the_stage_instead_of_blocking(db_session: Session):
    ticket = _ticket_on_stage(db_session)

    _die(db_session, ticket, stderr=LEASE_STDERR)

    assert ticket.state != TicketState.BLOCKED
    assert ticket.workflow_stage_status is StageStatus.PENDING
    assert ticket.workflow_stage_key == "testing"
    # Nothing in `blocking_issues`: reconcile_workflow_state reads that text to
    # decide a ticket is blocked, so prose left there un-arms the retry.
    assert ticket.blocking_issues == ""
    assert count_transient_retries(db_session, ticket.id, "testing") == 1


def test_the_retry_is_visible_even_though_the_ticket_is_not_blocked(db_session: Session):
    """A stage that silently ran twice is the failure mode this must not become."""
    ticket = _ticket_on_stage(db_session)

    _die(db_session, ticket, stderr=KEYCHAIN_STDERR)

    markers = db_session.exec(
        select(Artifact)
        .where(Artifact.ticket_id == ticket.id)
        .where(Artifact.kind == "stage_transient_retry")
    ).all()
    assert len(markers) == 1
    payload = json.loads(markers[0].content_json)
    assert "re-armed automatically" in payload["message"]
    assert "keychain" in payload["message"]
    assert payload["reason"] == "infrastructure"
    # The retry note must not masquerade as the ticket's blocking diagnosis.
    assert ticket.blocking_issues == ""


def test_the_orchestrator_is_told_to_re_dispatch_the_re_armed_stage(db_session: Session):
    ticket = _ticket_on_stage(db_session)

    run = _die(db_session, ticket, stdout=API_ERROR_STDOUT)

    assert stage_rearmed_for_latest_run(db_session, ticket, "testing") is True
    # Keyed to the run that died, not merely to the stage being PENDING.
    assert run.id
    other = _run(db_session, ticket)
    assert stage_rearmed_for_latest_run(db_session, ticket, "testing") is False, (
        "a newer run of the stage must not inherit the previous run's re-arm"
    )
    assert other.stage_key == "testing"


def test_a_clean_exit_with_no_stage_report_keeps_its_fail_closed_block(db_session: Session):
    """The one exclusion worth arguing, since `parallel_stage` does call this
    transient — but it pauses for a human there, where this path would re-run the
    agent. The scenario `test_missing_stage_report_fail_closed` was written for is
    a gatekeeper that decided REJECT in prose and lost its sentinel: the verdict
    existed. Re-running it risks the second pass returning `pass`, turning a
    recorded rejection into a promotion. Three of the twelve tickets blocked when
    this was written were blocked this way, and that is the right place for them.
    """
    ticket = _ticket_on_stage(db_session)

    _die(
        db_session,
        ticket,
        status=RunStatus.SUCCEEDED,
        stdout="AC Gate decision: **REJECT**. I attempted complete_stage but it was cancelled.",
    )

    assert count_transient_retries(db_session, ticket.id, "testing") == 0
    assert ticket.workflow_stage_status is StageStatus.BLOCKED
    assert "LOREGARDEN_STAGE_REPORT" in ticket.blocking_issues


def test_a_transient_retry_does_not_spend_the_runaway_backstop(db_session: Session):
    """The two counters measure different things and must not share a budget."""
    ticket = _ticket_on_stage(db_session)

    _die(db_session, ticket, stderr=LEASE_STDERR)
    _die(db_session, ticket, stderr=LEASE_STDERR)

    assert count_transient_retries(db_session, ticket.id, "testing") == 2
    assert count_stage_dispatches(db_session, ticket.id, "testing") == 0


# --------------------------------------------------------------------------- #
# The retry stops                                                              #
# --------------------------------------------------------------------------- #


def test_the_retries_are_bounded_and_the_block_names_the_environment(db_session: Session):
    ticket = _ticket_on_stage(db_session)

    for _ in range(TRANSIENT_RETRIES):
        _die(db_session, ticket, stderr=LEASE_STDERR)
        assert ticket.state != TicketState.BLOCKED

    _die(db_session, ticket, stderr=LEASE_STDERR)

    assert ticket.state == TicketState.BLOCKED
    assert ticket.workflow_stage_status is StageStatus.BLOCKED
    # Over the inline limit, so the prose is filed for the Errors tab and
    # `blocking_issues` holds the pointer to it.
    error = db_session.exec(
        select(Artifact).where(Artifact.ticket_id == ticket.id).where(Artifact.kind == "error")
    ).first()
    assert error is not None
    blocked_message = json.loads(error.content_json)["message"]
    assert str(TRANSIENT_RETRIES) in blocked_message
    assert "environment" in blocked_message
    # The counter stops at the budget; the refusal does not charge itself.
    assert count_transient_retries(db_session, ticket.id, "testing") == TRANSIENT_RETRIES


def test_a_settled_stage_starts_its_next_life_with_a_full_budget(db_session: Session):
    ticket = _ticket_on_stage(db_session)
    _die(db_session, ticket, stderr=LEASE_STDERR)
    assert count_transient_retries(db_session, ticket.id, "testing") == 1

    _die(db_session, ticket, status=RunStatus.SUCCEEDED, stdout=PASS_REPORT)

    assert count_transient_retries(db_session, ticket.id, "testing") == 0


# --------------------------------------------------------------------------- #
# What is not a transient failure                                              #
# --------------------------------------------------------------------------- #


def test_a_cancelled_run_is_not_retried(db_session: Session):
    """A human stopped it. Re-dispatching would fight the stop."""
    ticket = _ticket_on_stage(db_session)

    _die(db_session, ticket, status=RunStatus.CANCELLED, stderr="stopped")

    assert count_transient_retries(db_session, ticket.id, "testing") == 0
    assert stage_rearmed_for_latest_run(db_session, ticket, "testing") is False


def test_an_agent_reported_rejection_still_goes_to_rework(db_session: Session):
    ticket = _ticket_on_stage(db_session)

    _die(db_session, ticket, status=RunStatus.FAILED, stdout=FAIL_REPORT)

    assert count_transient_retries(db_session, ticket.id, "testing") == 0
    assert rework_reroute_count(db_session, ticket, "implement") == 1


def test_a_genuine_crash_is_not_retried(db_session: Session):
    """An AssertionError is the work failing, not the machine."""
    ticket = _ticket_on_stage(db_session)

    _die(
        db_session,
        ticket,
        stderr="Traceback (most recent call last): AssertionError: expected 2, got 3",
    )

    assert count_transient_retries(db_session, ticket.id, "testing") == 0
    assert ticket.workflow_stage_status is StageStatus.BLOCKED


def test_a_provider_usage_limit_still_blocks_with_its_reset_window(db_session: Session):
    """Retrying before the window clears spends a retry on the same wall — and
    the operator needs the reset time, not a fifth attempt."""
    ticket = _ticket_on_stage(db_session)

    _die(db_session, ticket, stderr="Claude usage limit reached · resets 3pm")

    assert count_transient_retries(db_session, ticket.id, "testing") == 0
    assert ticket.workflow_stage_status is StageStatus.BLOCKED


def test_a_server_reload_is_left_to_the_boot_sweep(db_session: Session):
    """The restart messages are transient, and somebody else's to fix. Re-arming
    the stage here would leave a PENDING stage at boot with no driver behind it,
    and drop the ticket out of `resume_interrupted_orchestrations`'s query on the
    way — trading a recovery that works for one that only looks busy."""
    ticket = _ticket_on_stage(db_session)

    _die(db_session, ticket, stderr=RELOAD_STDERR)

    assert count_transient_retries(db_session, ticket.id, "testing") == 0
    assert ticket.workflow_stage_status is StageStatus.BLOCKED
    assert ticket.blocking_issues == RELOAD_STDERR


def test_disabling_the_transient_budget_restores_the_old_behaviour(
    db_session: Session, monkeypatch
):
    """`max_transient_retries: 0` must read as "off", not as "exhausted": the
    operator needs the provider's error, not prose about a budget."""
    from loregarden.services import run_completion
    from loregarden.services.orchestration_profile import OrchestrationProfile

    off = OrchestrationProfile(slug="off")
    off.retry_budget.max_transient_retries = 0
    monkeypatch.setattr(run_completion, "resolve_orchestration_profile", lambda ws: off)

    ticket = _ticket_on_stage(db_session)
    _die(db_session, ticket, stderr=LEASE_STDERR)

    assert count_transient_retries(db_session, ticket.id, "testing") == 0
    assert ticket.workflow_stage_status is StageStatus.BLOCKED
    assert ticket.blocking_issues == LEASE_STDERR


def test_the_retry_reaches_the_ticket_payload_the_ui_reads(db_session: Session):
    """The marker is durable and logged, and neither of those is a surface.

    Without this the retry is invisible to the operator by construction: the
    ticket is deliberately unblocked and `blocking_issues` is empty, so nothing
    the detail view already renders says the stage was attempted twice.
    """
    from loregarden.api.tickets import _artifacts_grouped

    ticket = _ticket_on_stage(db_session)
    _die(db_session, ticket, stderr=LEASE_STDERR)
    _die(db_session, ticket, stderr=KEYCHAIN_STDERR)

    grouped = _artifacts_grouped(db_session, ticket)
    notices = grouped["transient_retries"]

    assert len(notices) == 2
    assert {notice["stage_key"] for notice in notices} == {"testing"}
    assert all(notice["reason"] == "infrastructure" for notice in notices)
    # Oldest first, so the view's "latest message" is actually the latest.
    assert notices[0]["at"] <= notices[1]["at"]
    assert "keychain" in notices[-1]["message"]


def test_a_ticket_with_no_retries_reports_an_empty_list_not_a_missing_key(
    db_session: Session,
):
    """An absent key and an empty list read differently in TypeScript, and the
    view distinguishes "nothing happened" from "retries happened"."""
    from loregarden.api.tickets import _artifacts_grouped

    ticket = _ticket_on_stage(db_session)

    assert _artifacts_grouped(db_session, ticket)["transient_retries"] == []
