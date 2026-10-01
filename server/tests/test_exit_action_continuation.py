"""Clearing a gate requirement continues the stage; only a passing report finishes it.

Driven through the real pipeline (the test_auto_mode_subtree harness, with the
suite's synchronous orchestration): the first pass raises a gate, a person
rechecks or grants, and the scheduled continuation dispatches a *new* run of
the stage. These pin AC-8/AC-9 end to end — the continuation runs exactly the
newly executable actions, the run that raised the gate keeps its own record,
and the stage reaches DONE only on the continuation's passing report.
"""

from __future__ import annotations

import json

import pytest
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    CliAdapter,
    ExitActionGateLedger,
    OrchestrationDriver,
    OrchestrationRun,
    RunStatus,
    RuntimeAvailability,
    RuntimeExitActionSnapshot,
    StageStatus,
    Ticket,
    TicketState,
)
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.orchestration import ApprovalService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from sqlmodel import Session, col, select
from tests.test_auto_mode_subtree import (
    _STAGES,
    _make_ticket,
    _make_workspace,
    _profile,
    _stage_status,
)

_READ_USAGE = {
    "key": "read-usage",
    "label": "Read provider usage",
    "requirement": {"kind": "credential", "credential_key": "github_token"},
}
_PUSH_BRANCH = {
    "key": "push-branch",
    "label": "Push the branch",
    "requirement": {"kind": "authority", "authority_scope": "repo:push"},
}
_ACCEPT_RISK = {
    "key": "accept-risk",
    "label": "Accept residual risk",
    "requirement": {"kind": "operator_judgment", "decision_prompt": "Acceptable?"},
}
_SIGNOFF = "signoff"


def _stages_with(stage_index: int, *actions: dict) -> list[dict]:
    stages = [dict(stage) for stage in _STAGES]
    stages[stage_index] = {
        **stages[stage_index],
        "exit_actions_enabled": True,
        "exit_actions": list(actions),
    }
    return stages


def _gates(db_session: Session, ticket: Ticket, stage_key: str = _SIGNOFF) -> list[Approval]:
    db_session.expire_all()
    return list(
        db_session.exec(
            select(Approval)
            .where(
                Approval.ticket_id == ticket.id,
                Approval.stage_key == stage_key,
                Approval.kind == ApprovalKind.WORKFLOW_GATE,
            )
            .order_by(col(Approval.created_at).asc())
        ).all()
    )


def _stage_runs(db_session: Session, ticket: Ticket, stage_key: str = _SIGNOFF) -> list[AgentRun]:
    db_session.expire_all()
    return list(
        db_session.exec(
            select(AgentRun)
            .where(AgentRun.ticket_id == ticket.id, AgentRun.stage_key == stage_key)
            .order_by(col(AgentRun.created_at).asc())
        ).all()
    )


def _keys(raw: str) -> list[str]:
    return json.loads(raw or "[]")


@pytest.fixture(name="no_github_token")
def no_github_token_fixture(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    return monkeypatch


def _first_pass(
    db_session: Session, tmp_path, slug: str, stages: list[dict], *, auto_approve: bool = False
) -> tuple[Ticket, Approval]:
    """Run the pipeline until the signoff gate parks it; return the ticket and gate."""
    workspace = _make_workspace(db_session, tmp_path, slug, stages=stages)
    ticket = _make_ticket(db_session, workspace, external_id=f"{slug}-1", title="Solo")
    BuiltinOrchestrator(db_session).execute(ticket, _profile(), auto_approve=auto_approve)
    gates = _gates(db_session, ticket)
    assert [gate.status for gate in gates] == [ApprovalStatus.PENDING]
    assert _stage_status(db_session, ticket, _SIGNOFF) == StageStatus.AWAITING
    return ticket, gates[0]


@pytest.fixture(name="credential_gate")
def credential_gate_fixture(db_session: Session, tmp_path, no_github_token):
    """Signoff authored one credential action, and the server had no token."""
    return _first_pass(db_session, tmp_path, "cont-cred", _stages_with(1, _READ_USAGE))


def test_a_recheck_continuation_runs_only_the_newly_executable_action(
    db_session: Session, credential_gate, no_github_token
):
    ticket, gate = credential_gate
    (first_run,) = _stage_runs(db_session, ticket)
    first_record = (
        first_run.assigned_exit_action_keys_json,
        first_run.runtime_exit_action_snapshot_json,
    )
    no_github_token.setenv("GITHUB_TOKEN", "test-token-value")

    result = ApprovalService(db_session).recheck(gate.id)

    assert result.newly_assigned_action_keys == ["read-usage"]
    assert result.completed_action_keys == []
    first, continuation = _stage_runs(db_session, ticket)
    # The run that raised the gate keeps its own record of what it was given.
    assert (first.assigned_exit_action_keys_json, first.runtime_exit_action_snapshot_json) == (
        first_record
    )
    assert _keys(first.completed_exit_action_keys_json) == []
    # A new run was dispatched with exactly the newly executable action, and its
    # passing report is what attests it.
    assert _keys(continuation.assigned_exit_action_keys_json) == ["read-usage"]
    assert _keys(continuation.completed_exit_action_keys_json) == ["read-usage"]
    assert "test-token-value" not in continuation.runtime_exit_action_snapshot_json
    assert _stage_status(db_session, ticket, _SIGNOFF) == StageStatus.DONE
    (closed,) = _gates(db_session, ticket)
    assert closed.status == ApprovalStatus.APPROVED
    ledger = ExitActionGateLedger.model_validate_json(closed.response_json)
    assert ledger.continuation_run_id == continuation.id


def test_a_failed_continuation_attests_nothing_and_does_not_finish_the_stage(
    db_session: Session, credential_gate, no_github_token
):
    ticket, gate = credential_gate
    no_github_token.setenv("GITHUB_TOKEN", "test-token-value")
    no_github_token.setenv("LOREGARDEN_FORCE_AGENT_FAIL", "1")

    ApprovalService(db_session).recheck(gate.id)

    # The failure may earn a retry or a repair turn; none of them passed.
    _, *continuations = _stage_runs(db_session, ticket)
    assert continuations
    assert _keys(continuations[0].assigned_exit_action_keys_json) == ["read-usage"]
    assert [_keys(run.completed_exit_action_keys_json) for run in continuations] == [
        [] for _ in continuations
    ]
    assert _stage_status(db_session, ticket, _SIGNOFF) != StageStatus.DONE


def test_the_continuation_keeps_the_parent_runs_flags(
    db_session: Session, tmp_path, no_github_token
):
    ticket, gate = _first_pass(
        db_session, tmp_path, "cont-flags", _stages_with(1, _READ_USAGE), auto_approve=True
    )
    no_github_token.setenv("GITHUB_TOKEN", "test-token-value")

    ApprovalService(db_session).recheck(gate.id)

    db_session.expire_all()
    runs = db_session.exec(
        select(OrchestrationRun)
        .where(OrchestrationRun.ticket_id == ticket.id)
        .order_by(col(OrchestrationRun.created_at).asc())
    ).all()
    assert len(runs) == 2
    assert runs[1].auto_approve is True
    assert runs[1].approve_design_plans == runs[0].approve_design_plans


def test_a_grant_continuation_runs_the_action_and_does_not_reask_the_judgment(
    db_session: Session, tmp_path
):
    """AC-9: a grant is recorded, overlaid on the next dispatch, and attested by it."""
    ticket, gate = _first_pass(
        db_session, tmp_path, "cont-grant", _stages_with(1, _PUSH_BRANCH, _ACCEPT_RISK)
    )
    assert json.loads(gate.tool_input_json)["allowed_actions"] == ["approve", "reject"]

    result = ApprovalService(db_session).approve_exit_action_gate(gate.id)

    assert result is not None
    assert result.newly_assigned_action_keys == ["push-branch"]
    _, continuation = _stage_runs(db_session, ticket)
    snapshot = json.loads(continuation.runtime_exit_action_snapshot_json)
    assert snapshot["authority"]["repo:push"] == "granted"
    assert _keys(continuation.assigned_exit_action_keys_json) == ["push-branch"]
    assert _keys(continuation.completed_exit_action_keys_json) == ["push-branch"]
    # The judgment was answered by the approve click; nothing is asked twice.
    assert [g.status for g in _gates(db_session, ticket)] == [ApprovalStatus.APPROVED]
    assert _stage_status(db_session, ticket, _SIGNOFF) == StageStatus.DONE


def test_a_mixed_gate_offers_approve_only_after_its_recheck_clears(
    db_session: Session, tmp_path, no_github_token
):
    """AC-8: approve is refused while a recheck is outstanding; the remainder re-gates once."""
    ticket, gate = _first_pass(
        db_session, tmp_path, "cont-mixed", _stages_with(1, _READ_USAGE, _ACCEPT_RISK)
    )
    service = ApprovalService(db_session)
    with pytest.raises(ValueError, match="recheck"):
        service.resolve(gate.id, approved=True)
    no_github_token.setenv("GITHUB_TOKEN", "test-token-value")

    service.recheck(gate.id)

    first, second = _gates(db_session, ticket)
    assert (first.status, second.status) == (ApprovalStatus.APPROVED, ApprovalStatus.PENDING)
    remaining = json.loads(second.tool_input_json)
    # Only the judgment is left: the credential action was attested by the run.
    assert [a["action_key"] for a in remaining["human_required_actions"]] == ["accept-risk"]
    assert remaining["allowed_actions"] == ["approve", "reject"]
    assert _stage_status(db_session, ticket, _SIGNOFF) == StageStatus.AWAITING

    service.resolve(second.id, approved=True)

    assert _stage_status(db_session, ticket, _SIGNOFF) == StageStatus.DONE


def test_a_design_plan_sign_off_never_grants_authority(db_session: Session, tmp_path):
    """The unattended sign-off answers operator judgment only, and never raises."""
    workspace = _make_workspace(
        db_session, tmp_path, "cont-plan", stages=_stages_with(0, _PUSH_BRANCH)
    )
    ticket = _make_ticket(db_session, workspace, external_id="cont-plan-1", title="Plan")

    BuiltinOrchestrator(db_session).execute(ticket, _profile(), approve_design_plans=True)

    (gate,) = _gates(db_session, ticket, "work")
    assert gate.status == ApprovalStatus.PENDING
    assert gate.resolved_by == ""
    assert _stage_status(db_session, ticket, "work") == StageStatus.AWAITING
    db_session.refresh(ticket)
    assert ticket.state != TicketState.BLOCKED


@pytest.fixture(name="unconfirmed_signoff")
def unconfirmed_signoff_fixture(db_session: Session, tmp_path) -> Ticket:
    """A sign-off stage whose run exited cleanly but did not pass: its agent was
    assigned read-usage, and nothing attests it."""
    workspace = _make_workspace(
        db_session, tmp_path, "unconfirmed", stages=_stages_with(1, _READ_USAGE, _ACCEPT_RISK)
    )
    ticket = _make_ticket(db_session, workspace, external_id="unconfirmed-1", title="Unconfirmed")
    snapshot = RuntimeExitActionSnapshot(
        run_id="prior",
        agent_id="backend_implementer",
        adapter=CliAdapter.LOCAL,
        driver=OrchestrationDriver.MANUAL_STAGE,
        credentials={"github_token": RuntimeAvailability.AVAILABLE},
    )
    db_session.add(
        AgentRun(
            ticket_id=ticket.id,
            workspace_id=workspace.id,
            stage_key="signoff",
            agent_id="backend_implementer",
            status=RunStatus.SUCCEEDED,
            run_code="UNCONFIRMED-1",
            runtime_exit_action_snapshot_json=snapshot.model_dump_json(),
            assigned_exit_action_keys_json=json.dumps(["read-usage"]),
            completed_exit_action_keys_json="[]",
        )
    )
    db_session.commit()
    return ticket


def test_a_requested_gate_cannot_stand_in_for_unconfirmed_agent_work(
    db_session: Session, unconfirmed_signoff: Ticket
):
    """Approving the judgment must not finish a stage whose assigned action was
    never confirmed by a passing report (AC-5, AC-9)."""
    with pytest.raises(ValueError, match="read-usage"):
        OrchestrationCallbackService(db_session).request_approval(
            unconfirmed_signoff, stage_key="signoff"
        )

    gates = db_session.exec(
        select(Approval).where(
            Approval.ticket_id == unconfirmed_signoff.id,
            Approval.kind == ApprovalKind.WORKFLOW_GATE,
        )
    ).all()
    assert gates == []
    assert _stage_status(db_session, unconfirmed_signoff, "signoff") != StageStatus.DONE
