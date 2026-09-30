"""RED behavioral contracts for runtime evaluation of workflow exit actions."""

import json
from unittest.mock import patch

import pytest
from loregarden.agents.stage_context import build_orchestration_context
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    Ticket,
    WorkflowStageDef,
)
from loregarden.services import exit_actions
from loregarden.services.gate_approvals import create_workflow_gate_approval
from loregarden.services.orchestration import ApprovalService
from sqlmodel import Session, select

_ACTIONS = [
    {
        "key": "run-smoke",
        "label": "Run the smoke test",
        "requirement": {
            "kind": "runtime_capability",
            "capability_id": "http_test_client",
        },
    },
    {
        "key": "read-usage",
        "label": "Read provider usage",
        "requirement": {
            "kind": "credential",
            "credential_key": "claude_profile",
        },
    },
    {
        "key": "publish-release",
        "label": "Publish the release",
        "requirement": {
            "kind": "authority",
            "authority_scope": "release:publish",
        },
    },
    {
        "key": "accept-risk",
        "label": "Accept residual risk",
        "requirement": {
            "kind": "operator_judgment",
            "decision_prompt": "Are the residual risks acceptable?",
        },
    },
]


def _stage(actions: list[dict] | None = None) -> WorkflowStageDef:
    return WorkflowStageDef.model_validate(
        {
            "key": "verify",
            "name": "Verify",
            "agent_id": "verifier",
            "exit_actions_enabled": True,
            "exit_actions": actions or _ACTIONS,
        }
    )


def _snapshot(*, driver: str = "builtin_autopilot", fresh: bool = True) -> dict:
    return {
        "run_id": "run_runtime",
        "agent_id": "verifier",
        "agent_version": 7,
        "adapter": "codex",
        "driver": driver,
        "capability_data_fresh": fresh,
        "capabilities": {"http_test_client": "available"},
        "credentials": {"claude_profile": "available"},
        "authority": {"release:publish": "granted"},
    }


def _payload(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _resolution(stage: WorkflowStageDef, snapshot: dict) -> dict:
    resolver = getattr(exit_actions, "resolve_exit_actions")
    return _payload(resolver(stage, snapshot))


@pytest.mark.parametrize("driver", ["builtin_autopilot", "manual_stage", "external_mcp"])
def test_all_run_drivers_use_the_same_secret_free_runtime_snapshot(driver: str):
    """AC-4: driver choice cannot change evaluation or persist a credential value."""
    run = AgentRun(
        run_code=f"run_{driver}",
        workspace_id="ws",
        agent_id="verifier",
        agent_version=7,
        stage_key="verify",
    )
    capture = getattr(exit_actions, "capture_runtime_snapshot")
    snapshot = _payload(
        capture(
            run=run,
            driver=driver,
            adapter="codex",
            capability_statuses={"http_test_client": "available"},
            credential_preflight={"claude_profile": "available"},
            authority_statuses={"release:publish": "granted"},
        )
    )
    persisted = json.loads(run.runtime_exit_action_snapshot_json)
    snapshot_json = json.dumps(persisted)

    assert "secret" not in snapshot_json.lower()
    assert "token" not in snapshot_json.lower()
    assert "password" not in snapshot_json.lower()
    assert persisted == snapshot
    assert persisted["run_id"] == run.id
    assert persisted["agent_version"] == 7
    assert persisted["adapter"] == "codex"
    assert _resolution(_stage(_ACTIONS[:3]), snapshot)["assigned_action_keys"] == [
        "run-smoke",
        "read-usage",
        "publish-release",
    ]


def test_resolver_partitions_satisfied_and_human_required_actions():
    """AC-5/AC-6: only the unresolved subset enters the single human gate."""
    snapshot = _snapshot()
    snapshot["credentials"]["claude_profile"] = "unavailable"

    resolved = _resolution(_stage(), snapshot)

    assert resolved["assigned_action_keys"] == ["run-smoke", "publish-release"]
    assert [item["action_key"] for item in resolved["human_required_actions"]] == [
        "read-usage",
        "accept-risk",
    ]
    assert resolved["allowed_actions"] == ["recheck", "reject"]


def test_clearing_recheck_only_conditions_exposes_operator_approve():
    """AC-8: mixed gates expose approve only after every recheck condition clears."""
    resolved = _resolution(_stage([_ACTIONS[1], _ACTIONS[3]]), _snapshot())

    assert [item["action_key"] for item in resolved["human_required_actions"]] == ["accept-risk"]
    assert resolved["allowed_actions"] == ["approve", "reject"]


@pytest.mark.parametrize(
    ("field", "identifier", "reason_code"),
    [
        ("capabilities", "http_test_client", "capability_status_unknown"),
        ("credentials", "claude_profile", "credential_status_unknown"),
        ("authority", "release:publish", "authority_status_unknown"),
    ],
)
def test_unknown_runtime_status_fails_closed(field: str, identifier: str, reason_code: str):
    """AC-4/AC-7: omitted data is unknown, not executable."""
    snapshot = _snapshot()
    snapshot[field].pop(identifier)

    resolved = _resolution(_stage(_ACTIONS[:3]), snapshot)
    unresolved = next(
        action for action in resolved["human_required_actions"] if identifier in action["reason"]
    )

    assert unresolved["reason_code"] == reason_code
    assert "status unavailable" in unresolved["reason"].lower()
    assert unresolved["resolution_mode"] == "recheck"


def test_stale_external_capability_data_is_unknown():
    """AC-4: an external report cannot remain executable after it becomes stale."""
    resolved = _resolution(_stage([_ACTIONS[0]]), _snapshot(driver="external_mcp", fresh=False))

    assert resolved["assigned_action_keys"] == []
    assert resolved["human_required_actions"][0]["reason_code"] == ("capability_status_unknown")


def test_assigned_actions_reach_stage_context_and_require_a_passing_attestation():
    """AC-5: assigned work is explicit, and only pass attests completion."""
    ticket = Ticket(external_id="runtime-actions", workspace_id="ws", title="Runtime actions")
    run = AgentRun(
        id="run_runtime",
        run_code="run_runtime",
        ticket_id="ticket",
        workspace_id="ws",
        agent_id="verifier",
        agent_version=7,
        stage_key="verify",
    )
    resolved = _resolution(_stage(_ACTIONS[:3]), _snapshot())

    context = build_orchestration_context(
        ticket=ticket,
        run=run,
        stage_def=_stage(_ACTIONS[:3]),
        assigned_exit_actions=resolved["assigned_actions"],
    )

    assert "Run the smoke test" in context
    assert "Read provider usage" in context
    assert "Publish the release" in context
    assert "passing stage report attests" in context.lower()


@pytest.mark.parametrize(
    ("report_status", "expected"),
    [("pass", ["run-smoke"]), ("needs_rework", []), ("fail", [])],
)
def test_only_a_passing_report_attests_assigned_actions(report_status: str, expected: list[str]):
    """AC-5/AC-9: requirement satisfaction alone never completes an action."""
    assert {
        "assigned_exit_action_keys_json",
        "completed_exit_action_keys_json",
    } <= AgentRun.model_fields.keys()
    run = AgentRun.model_validate(
        {
            "run_code": f"run_{report_status}",
            "workspace_id": "ws",
            "agent_id": "verifier",
            "stage_key": "verify",
            "assigned_exit_action_keys_json": json.dumps(["run-smoke"]),
        }
    )
    attest = getattr(exit_actions, "attest_assigned_actions")

    attest(run, report_status=report_status)

    assert json.loads(run.completed_exit_action_keys_json) == expected


def _recheck_only_approval(db_session: Session) -> Approval:
    ticket = db_session.exec(select(Ticket)).first()
    assert ticket is not None
    approval = Approval(
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        kind=ApprovalKind.WORKFLOW_GATE,
        title="Resolve runtime action",
        stage_key=ticket.workflow_stage_key,
        tool_input_json=json.dumps(
            {
                "human_required_actions": [
                    {
                        "action_key": "read-usage",
                        "action_label": "Read provider usage",
                        "action_description": "",
                        "requirement": {
                            "kind": "credential",
                            "credential_key": "claude_profile",
                        },
                        "reason_code": "credential_unavailable",
                        "reason": "Credential unavailable: claude_profile",
                        "resolution_mode": "recheck",
                    }
                ],
                "allowed_actions": ["recheck", "reject"],
            }
        ),
        status=ApprovalStatus.PENDING,
    )
    db_session.add(approval)
    db_session.commit()
    db_session.refresh(approval)
    return approval


def test_server_rejects_approve_for_a_recheck_only_gate(db_session: Session):
    """AC-8: payload validation in the client model is not the security boundary."""
    approval = _recheck_only_approval(db_session)

    with pytest.raises(ValueError, match="recheck"):
        ApprovalService(db_session).resolve(approval.id, approved=True)

    db_session.refresh(approval)
    assert approval.status == ApprovalStatus.PENDING


def test_executable_actions_open_no_gate(db_session: Session):
    """AC-6/AC-12: capability available, credential available, authority granted —
    every action goes to the agent, and no approval row is written."""
    ticket = db_session.exec(select(Ticket)).first()
    assert ticket is not None
    stage = _stage(_ACTIONS[:3])

    resolution = exit_actions.resolve_exit_actions(stage, _snapshot())
    approval = create_workflow_gate_approval(
        db_session, ticket, "verify", "Verify", stage_def=stage, snapshot=_snapshot()
    )

    assert resolution.assigned_action_keys == ["run-smoke", "read-usage", "publish-release"]
    assert resolution.human_required_actions == []
    assert approval is None
    assert (
        db_session.exec(
            select(Approval).where(Approval.ticket_id == ticket.id, Approval.stage_key == "verify")
        ).all()
        == []
    )


def test_successful_recheck_schedules_only_newly_satisfied_actions(db_session: Session):
    """AC-9: recheck resumes execution; it does not complete the underlying action."""
    approval = _recheck_only_approval(db_session)

    with (
        patch(
            "loregarden.services.exit_action_approvals.probe_credentials",
            return_value={"claude_profile": "available"},
        ),
        patch(
            "loregarden.services.exit_action_approvals.probe_capabilities",
            return_value={},
        ),
        patch(
            "loregarden.services.exit_action_approvals.probe_authority",
            return_value={},
        ),
        patch("loregarden.services.run_service.schedule_orchestration") as scheduled,
    ):
        result = ApprovalService(db_session).recheck(approval.id)

    assert result.newly_assigned_action_keys == ["read-usage"]
    assert result.completed_action_keys == []
    scheduled.assert_called_once()
    assert scheduled.call_args.kwargs["assigned_exit_action_keys"] == ["read-usage"]
    db_session.refresh(approval)
    assert approval.status == ApprovalStatus.PENDING


def test_authority_grant_schedules_execution_without_completing_the_action(
    db_session: Session,
):
    """AC-9: granting policy authority resumes the responsible stage."""
    approval = _recheck_only_approval(db_session)
    payload = json.loads(approval.tool_input_json)
    payload["human_required_actions"][0] = {
        "action_key": "publish-release",
        "action_label": "Publish the release",
        "action_description": "",
        "requirement": {
            "kind": "authority",
            "authority_scope": "release:publish",
        },
        "reason_code": "authority_grant_required",
        "reason": "Authority grant required: release:publish",
        "resolution_mode": "approve",
    }
    payload["allowed_actions"] = ["approve", "reject"]
    approval.tool_input_json = json.dumps(payload)
    db_session.add(approval)
    db_session.commit()

    with patch("loregarden.services.run_service.schedule_orchestration") as scheduled:
        result = ApprovalService(db_session).grant_authority(
            approval.id, authority_scope="release:publish"
        )

    assert result.newly_assigned_action_keys == ["publish-release"]
    assert result.completed_action_keys == []
    scheduled.assert_called_once()
    assert scheduled.call_args.kwargs["assigned_exit_action_keys"] == ["publish-release"]


def test_approve_exit_action_gate_grants_authority_instead_of_completing_stage(
    db_session: Session,
):
    """AC-9: inbox approve of grantable authority schedules continuation, not stage DONE."""
    approval = _recheck_only_approval(db_session)
    payload = json.loads(approval.tool_input_json)
    payload["human_required_actions"][0] = {
        "action_key": "publish-release",
        "action_label": "Publish the release",
        "action_description": "",
        "requirement": {
            "kind": "authority",
            "authority_scope": "release:publish",
        },
        "reason_code": "authority_grant_required",
        "reason": "Authority grant required: release:publish",
        "resolution_mode": "approve",
    }
    payload["allowed_actions"] = ["approve", "reject"]
    approval.tool_input_json = json.dumps(payload)
    db_session.add(approval)
    db_session.commit()

    ticket = db_session.get(Ticket, approval.ticket_id)
    assert ticket is not None
    stage_before = ticket.workflow_stage_status

    with patch("loregarden.services.run_service.schedule_orchestration") as scheduled:
        result = ApprovalService(db_session).approve_exit_action_gate(approval.id)

    assert result is not None
    assert result.newly_assigned_action_keys == ["publish-release"]
    assert result.completed_action_keys == []
    scheduled.assert_called_once()
    db_session.refresh(approval)
    db_session.refresh(ticket)
    assert approval.status == ApprovalStatus.APPROVED
    assert ticket.workflow_stage_status == stage_before
