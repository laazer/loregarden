"""AC-4: every driver persists a secret-free runtime snapshot before dispatch.

Exercised through the real paths — the builtin driver's dispatch and an
external harness checking a stage out over `begin_external_stage` — rather than
a hand-built snapshot. The two must differ in exactly one way: this process can
observe its own environment, and cannot observe an external harness's, so the
harness's capabilities and credentials are unknown (fail closed) even when the
server holds the very credential the action needs.
"""

from __future__ import annotations

import json

import pytest
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    ExternalHarness,
)
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.external_harness import (
    begin_external_stage,
    finish_external_stage,
    start_external_orchestration,
)
from sqlmodel import Session, select
from tests.test_auto_mode_subtree import _STAGES, _make_ticket, _make_workspace, _profile

_SECRET = "ghp-test-secret-value"
_READ_USAGE = {
    "key": "read-usage",
    "label": "Read provider usage",
    "requirement": {"kind": "credential", "credential_key": "github_token"},
}
_PASSING_REPORT = """
Did the work.

<<<LOREGARDEN_STAGE_REPORT>>>
{"status": "pass", "confidence": 0.9}
<<<END_STAGE_REPORT>>>
"""


@pytest.fixture(name="ticket")
def ticket_fixture(db_session: Session, tmp_path, monkeypatch):
    """The first stage authors a credential action the *server* can satisfy."""
    monkeypatch.setenv("GITHUB_TOKEN", _SECRET)
    stages = [
        {**_STAGES[0], "exit_actions_enabled": True, "exit_actions": [_READ_USAGE]},
        *_STAGES[1:],
    ]
    workspace = _make_workspace(db_session, tmp_path, "snapshot-drivers", stages=stages)
    return _make_ticket(db_session, workspace, external_id="snapshot-drivers-1", title="Solo")


def _work_run(db_session: Session, ticket) -> AgentRun:
    db_session.expire_all()
    return db_session.exec(
        select(AgentRun).where(AgentRun.ticket_id == ticket.id, AgentRun.stage_key == "work")
    ).one()


def test_an_external_harness_runtime_is_unobserved_and_fails_closed(db_session: Session, ticket):
    orch_run = start_external_orchestration(db_session, ticket, harness=ExternalHarness.CODEX)

    stage = begin_external_stage(db_session, orch_run)

    # Persisted before the prompt left the control plane.
    run = _work_run(db_session, ticket)
    assert stage.runs[0].agent_run_id == run.id
    snapshot = json.loads(run.runtime_exit_action_snapshot_json)
    assert snapshot["run_id"] == run.id
    assert snapshot["agent_id"] == run.agent_id
    assert snapshot["agent_version"] == run.agent_version
    assert snapshot["driver"] == "external_mcp"
    assert snapshot["capabilities"] == {}
    assert snapshot["credentials"] == {}
    assert snapshot["capability_data_fresh"] is False
    # Authority is server policy, so it is still observed.
    assert snapshot["authority"]["repo:push"] == "grantable"
    assert _SECRET not in run.runtime_exit_action_snapshot_json
    # The server's own token does not make the harness's action executable.
    assert json.loads(run.assigned_exit_action_keys_json) == []

    finish_external_stage(db_session, run, transcript=_PASSING_REPORT)

    db_session.expire_all()
    (gate,) = db_session.exec(
        select(Approval).where(
            Approval.ticket_id == ticket.id,
            Approval.stage_key == "work",
            Approval.kind == ApprovalKind.WORKFLOW_GATE,
        )
    ).all()
    assert gate.status == ApprovalStatus.PENDING
    (action,) = json.loads(gate.tool_input_json)["human_required_actions"]
    assert action["reason_code"] == "credential_status_unknown"
    assert action["resolution_mode"] == "recheck"


def test_a_builtin_runtime_is_observed_without_recording_the_secret(db_session: Session, ticket):
    BuiltinOrchestrator(db_session).execute(ticket, _profile(), max_stages=1)

    run = _work_run(db_session, ticket)
    snapshot = json.loads(run.runtime_exit_action_snapshot_json)
    assert snapshot["driver"] == "builtin_autopilot"
    assert snapshot["credentials"]["github_token"] == "available"
    assert snapshot["capability_data_fresh"] is True
    assert _SECRET not in run.runtime_exit_action_snapshot_json
    assert json.loads(run.assigned_exit_action_keys_json) == ["read-usage"]
