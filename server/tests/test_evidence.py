"""Evidence artifacts: proof of behaviour, bound to the commit it proves (S1)."""

import pytest
from loregarden.mcp.tools import execute_tool, tool_names
from loregarden.models.domain import Artifact, Ticket, WorkItemType, Workspace
from loregarden.services.evidence import EVIDENCE_KINDS, evidence_for_commit, has_evidence
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from sqlmodel import Session, SQLModel, create_engine, select


@pytest.fixture()
def session_and_ticket(tmp_path, git_repo):
    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    session.add(Workspace(id="ws", slug="ws", name="WS", repo_path=str(git_repo)))
    ticket = Ticket(
        id="t1",
        external_id="42-evidence",
        workspace_id="ws",
        title="Evidence",
        work_item_type=WorkItemType.TASK,
    )
    session.add(ticket)
    session.commit()
    return session, ticket


def test_evidence_tool_is_registered_and_auto_approved():
    from loregarden.agents.executors.permission_bridge import AUTO_APPROVED_MCP_TOOLS

    assert "loregarden_attach_evidence" in tool_names()
    # Writes only to the artifacts table, same trust level as attach_artifact.
    assert "loregarden_attach_evidence" in AUTO_APPROVED_MCP_TOOLS


def test_evidence_is_stamped_with_the_current_head(session_and_ticket, git_repo):
    session, ticket = session_and_ticket
    svc = OrchestrationCallbackService(session)
    from loregarden.services.evidence import resolve_head_sha

    head = resolve_head_sha(session, ticket)
    assert head  # a real sha, not empty

    svc.attach_artifact(
        ticket,
        kind="evidence",
        title="POST /tickets returns 201",
        content={"status": 201},
        evidence_kind="real_surface",
        commit_sha=head,
    )
    stored = session.exec(select(Artifact).where(Artifact.kind == "evidence")).first()
    assert stored.commit_sha == head
    assert stored.evidence_kind == "real_surface"


def test_evidence_is_scoped_to_the_commit_it_proves(session_and_ticket, git_repo):
    """Evidence captured before the last edit proves nothing about the new code."""
    session, ticket = session_and_ticket
    svc = OrchestrationCallbackService(session)
    svc.attach_artifact(
        ticket,
        kind="evidence",
        title="old proof",
        content={},
        evidence_kind="real_surface",
        commit_sha="oldsha",
    )

    assert has_evidence(session, ticket, commit_sha="oldsha") is True
    assert has_evidence(session, ticket, commit_sha="newsha") is False
    assert len(evidence_for_commit(session, ticket)) == 1


def test_evidence_can_be_narrowed_by_kind(session_and_ticket):
    session, ticket = session_and_ticket
    svc = OrchestrationCallbackService(session)
    svc.attach_artifact(
        ticket,
        kind="evidence",
        title="tests",
        content={},
        evidence_kind="test_red_green",
        commit_sha="sha1",
    )
    # The floor is present but the ceiling is not — what a two-artifact gate asks.
    assert has_evidence(session, ticket, commit_sha="sha1", evidence_kind="test_red_green")
    assert not has_evidence(session, ticket, commit_sha="sha1", evidence_kind="real_surface")


def test_unknown_evidence_kind_is_rejected(session_and_ticket):
    """A free-form kind would make a gate's question unanswerable."""
    session, ticket = session_and_ticket
    from loregarden.models.domain import OrchestrationRun

    session.add(
        OrchestrationRun(
            id="orun",
            run_code="orun_1",
            ticket_id=ticket.id,
            workspace_id="ws",
        )
    )
    session.commit()

    with pytest.raises(ValueError, match="Unknown evidence_kind"):
        execute_tool(
            session,
            "loregarden_attach_evidence",
            {
                "run_id": "orun",
                "evidence_kind": "vibes",
                "title": "trust me",
            },
        )


def test_evidence_kinds_cover_floor_ceiling_and_verdict():
    assert set(EVIDENCE_KINDS) == {"test_red_green", "real_surface", "verify_verdict"}


# --- Two-artifact gate: proof required before a stage advances (#2) ---------


def _gate_stage(required):
    from loregarden.models.domain import WorkflowStageDef

    return WorkflowStageDef(
        key="implement",
        name="Implement",
        agent_id="backend",
        order=1,
        required_evidence=required,
    )


def _orchestrator(session):
    from loregarden.services.builtin_orchestrator import BuiltinOrchestrator

    return BuiltinOrchestrator(session)


def test_stage_without_a_requirement_is_unaffected(session_and_ticket):
    """Templates that never asked for proof keep advancing as before."""
    session, ticket = session_and_ticket
    detail = _orchestrator(session)._missing_evidence_detail(ticket, _gate_stage([]))
    assert detail == ""


def test_missing_proof_names_what_is_missing(session_and_ticket):
    session, ticket = session_and_ticket
    stage = _gate_stage(["test_red_green", "real_surface"])
    detail = _orchestrator(session)._missing_evidence_detail(ticket, stage)
    assert "test_red_green" in detail and "real_surface" in detail
    # The message has to tell the agent how to satisfy it, since this reason is
    # what gets handed back for the retry.
    assert "loregarden_attach_evidence" in detail


def test_green_tests_alone_do_not_satisfy_a_two_artifact_stage(session_and_ticket):
    """The floor without the ceiling is the case this gate exists to catch."""
    session, ticket = session_and_ticket
    from loregarden.services.evidence import resolve_head_sha

    OrchestrationCallbackService(session).attach_artifact(
        ticket,
        kind="evidence",
        title="tests go green",
        content={},
        evidence_kind="test_red_green",
        commit_sha=resolve_head_sha(session, ticket),
    )
    stage = _gate_stage(["test_red_green", "real_surface"])
    detail = _orchestrator(session)._missing_evidence_detail(ticket, stage)
    assert "real_surface" in detail
    assert "test_red_green" not in detail


def test_both_artifacts_for_this_commit_let_the_stage_pass(session_and_ticket):
    session, ticket = session_and_ticket
    from loregarden.services.evidence import resolve_head_sha

    svc = OrchestrationCallbackService(session)
    head = resolve_head_sha(session, ticket)
    for kind in ("test_red_green", "real_surface"):
        svc.attach_artifact(
            ticket,
            kind="evidence",
            title=kind,
            content={},
            evidence_kind=kind,
            commit_sha=head,
        )
    stage = _gate_stage(["test_red_green", "real_surface"])
    assert _orchestrator(session)._missing_evidence_detail(ticket, stage) == ""


def test_proof_from_an_earlier_commit_does_not_count(session_and_ticket):
    """Evidence carried over from a previous commit says nothing about this one."""
    session, ticket = session_and_ticket
    OrchestrationCallbackService(session).attach_artifact(
        ticket,
        kind="evidence",
        title="stale proof",
        content={},
        evidence_kind="real_surface",
        commit_sha="a-commit-from-before",
    )
    stage = _gate_stage(["real_surface"])
    detail = _orchestrator(session)._missing_evidence_detail(ticket, stage)
    assert "real_surface" in detail


def test_missing_proof_blocks_even_when_transition_gates_are_off(session_and_ticket):
    """A stage opts in by declaring required_evidence, so the requirement holds
    regardless of whether the profile runs gate commands."""
    from loregarden.models.domain import (
        OrchestrationRun,
        StageStatus,
        WorkflowInstance,
        WorkflowStageDef,
    )
    from loregarden.services.builtin_orchestrator import _GateDecision
    from loregarden.services.orchestration_profile import resolve_orchestration_profile
    from loregarden.services.workflow_state import initial_stages_json

    session, ticket = session_and_ticket
    stages = [
        WorkflowStageDef(
            key="implement",
            name="Implement",
            agent_id="backend",
            order=1,
            required_evidence=["real_surface"],
        ),
        WorkflowStageDef(key="review", name="Review", agent_id="reviewer", order=2),
    ]
    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id="tpl",
        current_stage_key="implement",
        stages_json=initial_stages_json(stages),
    )
    orch_run = OrchestrationRun(run_code="orun_1", ticket_id=ticket.id, workspace_id="ws")
    session.add(instance)
    session.add(orch_run)
    session.commit()

    ticket.workflow_stage_key = "implement"
    ticket.workflow_stage_status = StageStatus.RUNNING
    workspace = session.get(Workspace, "ws")
    profile = resolve_orchestration_profile(workspace)
    profile.gates.enabled = False

    decision = _orchestrator(session)._run_gates_with_autofix(
        ticket,
        profile,
        stages[0],
        instance,
        stages,
        orch_run,
        from_stage="implement",
        to_stage="review",
    )
    assert decision is not _GateDecision.PASS
    # The agent is told why, so it can attach the proof and retry.
    assert "real_surface" in (ticket.blocking_issues or "")


def test_verify_blocks_until_it_records_a_verdict(session_and_ticket):
    """The whole point of turning this on: a verify that advances without
    recording what it found is the unverified pass it was added to prevent."""
    from loregarden.models.domain import WorkflowStageDef

    session, ticket = session_and_ticket
    stage = WorkflowStageDef(
        key="verify",
        name="Verify",
        agent_id="verifier",
        order=8,
        stage_type="verify",
        required_evidence=["verify_verdict"],
    )
    orchestrator = _orchestrator(session)

    detail = orchestrator._missing_evidence_detail(ticket, stage)
    assert "verify_verdict" in detail

    from loregarden.services.evidence import resolve_head_sha

    OrchestrationCallbackService(session).attach_artifact(
        ticket,
        kind="evidence",
        title="confirmed against the running app",
        content={"checked": "POST /api/tickets"},
        evidence_kind="verify_verdict",
        commit_sha=resolve_head_sha(session, ticket),
    )
    assert orchestrator._missing_evidence_detail(ticket, stage) == ""


def test_a_verdict_from_an_earlier_commit_does_not_carry_over(session_and_ticket):
    """Re-running verify after new edits must not pass on the old verdict."""
    from loregarden.models.domain import WorkflowStageDef

    session, ticket = session_and_ticket
    OrchestrationCallbackService(session).attach_artifact(
        ticket,
        kind="evidence",
        title="verdict from before the fix",
        content={},
        evidence_kind="verify_verdict",
        commit_sha="an-earlier-commit",
    )
    stage = WorkflowStageDef(
        key="verify",
        name="Verify",
        agent_id="verifier",
        order=8,
        stage_type="verify",
        required_evidence=["verify_verdict"],
    )
    assert "verify_verdict" in _orchestrator(session)._missing_evidence_detail(ticket, stage)
