"""Parent auto-mode: run a ticket subtree end-to-end with auto-approved
standard gates (ticket 164).

Exercises the real BuiltinOrchestrator/OrchestrationService/PermissionBridgeRunner
pipeline against a small hand-built workflow template (work -> signoff
[gate_required] -> review [agentless human gate] -> done) so every stage shape
the ticket calls out is covered without depending on the seeded workspace's own
(much longer) template. Only the CLI subprocess boundary is faked, matching the
pattern already used in test_permission_bridge.py; everything else — DB state,
stage routing, gate resolution — runs for real.

Gaps 1/2 (auto_approve child propagation, stage-gate auto-resolution) are not
yet implemented as of the test-design stage — several tests below are
intentionally red until the implement stage lands the fix; that is the point
of writing them here first.
"""

import json
import subprocess
from pathlib import Path

import pytest
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    OrchestrationRunStatus,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_profile import OrchestrationProfile
from loregarden.services.workflow_state import parse_stage_map
from sqlmodel import Session, select

# Stage shapes the ticket names explicitly:
#   work    - ordinary agent stage, nothing special
#   signoff - agent stage with gate_required=True (Gap 2a)
#   review  - agentless stage (no agent_id) => human approval gate (Gap 2b)
#   done    - terminal
_STAGES = [
    {
        "key": "work",
        "name": "Work",
        "agent_id": "planner",
        "skill_name": "",
        "stage_type": "agent",
        "order": 1,
        "gate_required": False,
    },
    {
        "key": "signoff",
        "name": "Sign-off",
        "agent_id": "planner",
        "skill_name": "",
        "stage_type": "agent",
        "order": 2,
        "gate_required": True,
    },
    {
        "key": "review",
        "name": "Review",
        "agent_id": "",
        "skill_name": "",
        "stage_type": "agent",
        "order": 3,
        "gate_required": False,
    },
    {
        "key": "done",
        "name": "Done",
        "agent_id": "",
        "skill_name": "",
        "stage_type": "agent",
        "order": 4,
        "terminal": True,
    },
]
_TRANSITIONS = [
    {"from": "work", "to": "signoff", "when": "pass"},
    {"from": "signoff", "to": "review", "when": "pass"},
    {"from": "review", "to": "done", "when": "pass"},
]


def _git_repo(tmp_path: Path) -> Path:
    root = tmp_path / f"repo-{len(list(tmp_path.iterdir()))}"
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"], cwd=root, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=root, check=True, capture_output=True
    )
    (root / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=root, check=True, capture_output=True)
    return root


def _make_workspace(db_session: Session, tmp_path: Path, slug: str) -> Workspace:
    template = WorkflowTemplate(
        slug=f"{slug}-tpl",
        name="Auto-mode subtree test template",
        stages_json=json.dumps(_STAGES),
        transitions_json=json.dumps(_TRANSITIONS),
        version=1,
    )
    db_session.add(template)
    db_session.commit()
    db_session.refresh(template)

    ws = Workspace(
        slug=slug,
        name=slug,
        repo_path=str(_git_repo(tmp_path)),
        workflow_template_id=template.id,
    )
    db_session.add(ws)
    db_session.commit()
    db_session.refresh(ws)
    return ws


def _make_ticket(
    db_session: Session,
    ws: Workspace,
    *,
    external_id: str,
    title: str,
    work_item_type: WorkItemType = WorkItemType.TASK,
    parent_ticket_id: str | None = None,
) -> Ticket:
    ticket = Ticket(
        external_id=external_id,
        workspace_id=ws.id,
        title=title,
        work_item_type=work_item_type,
        parent_ticket_id=parent_ticket_id,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    OrchestrationService(db_session).ensure_workflow_instance(ticket, commit=True)
    db_session.refresh(ticket)
    return ticket


def _profile(**overrides) -> OrchestrationProfile:
    return OrchestrationProfile(slug="auto-mode-subtree-test", **overrides)


def _stage_status(db_session: Session, ticket: Ticket, stage_key: str) -> StageStatus:
    orch = OrchestrationService(db_session)
    instance, stages = orch._resolve_stages(ticket)
    return parse_stage_map(instance, stages)[stage_key]


# ---------------------------------------------------------------------------
# AC: child orchestration runs inherit the parent run's auto_approve
# ---------------------------------------------------------------------------


def test_child_run_inherits_parent_auto_approve(db_session: Session, tmp_path):
    ws = _make_workspace(db_session, tmp_path, "auto-mode-inherit")
    parent = _make_ticket(db_session, ws, external_id="auto-parent-1", title="Parent")
    child = _make_ticket(
        db_session, ws, external_id="auto-child-1", title="Child", parent_ticket_id=parent.id
    )

    BuiltinOrchestrator(db_session).execute(parent, _profile(), auto_approve=True)

    child_runs = db_session.exec(select(AgentRun).where(AgentRun.ticket_id == child.id)).all()
    assert child_runs, "expected the child ticket to have produced at least one stage run"
    assert all(run.auto_approve for run in child_runs), (
        "child stage runs must inherit the parent orchestration run's auto_approve; "
        f"got {[(r.stage_key, r.auto_approve) for r in child_runs]}"
    )


def test_grandchild_run_inherits_auto_approve_recursively(db_session: Session, tmp_path):
    ws = _make_workspace(db_session, tmp_path, "auto-mode-inherit-deep")
    parent = _make_ticket(db_session, ws, external_id="auto-parent-2", title="Parent")
    child = _make_ticket(
        db_session, ws, external_id="auto-child-2", title="Child", parent_ticket_id=parent.id
    )
    grandchild = _make_ticket(
        db_session,
        ws,
        external_id="auto-grandchild-2",
        title="Grandchild",
        parent_ticket_id=child.id,
    )

    BuiltinOrchestrator(db_session).execute(parent, _profile(), auto_approve=True)

    grandchild_runs = db_session.exec(
        select(AgentRun).where(AgentRun.ticket_id == grandchild.id)
    ).all()
    assert grandchild_runs, "expected the grandchild ticket to have produced at least one stage run"
    assert all(run.auto_approve for run in grandchild_runs), (
        "auto_approve must propagate through every level of nested execute(), not just "
        f"the immediate child; got {[(r.stage_key, r.auto_approve) for r in grandchild_runs]}"
    )


def test_child_run_does_not_get_auto_approve_without_it(db_session: Session, tmp_path):
    """Regression guard: today's default behavior (no auto_approve) must not change."""
    ws = _make_workspace(db_session, tmp_path, "auto-mode-inherit-off")
    parent = _make_ticket(db_session, ws, external_id="noauto-parent-1", title="Parent")
    child = _make_ticket(
        db_session, ws, external_id="noauto-child-1", title="Child", parent_ticket_id=parent.id
    )

    BuiltinOrchestrator(db_session).execute(parent, _profile(), auto_approve=False)

    child_runs = db_session.exec(select(AgentRun).where(AgentRun.ticket_id == child.id)).all()
    assert child_runs, "expected the child ticket to have produced at least one stage run"
    assert all(not run.auto_approve for run in child_runs)


# ---------------------------------------------------------------------------
# AC: gate_required stage sign-off auto-resolves under auto_approve, with an
# audit trail distinguishing it from a human approval
# ---------------------------------------------------------------------------


def test_gate_required_stage_auto_resolves_under_auto_approve(db_session: Session, tmp_path):
    ws = _make_workspace(db_session, tmp_path, "auto-mode-gate-on")
    ticket = _make_ticket(db_session, ws, external_id="gate-auto-1", title="Solo")

    orch_run = BuiltinOrchestrator(db_session).execute(ticket, _profile(), auto_approve=True)
    db_session.refresh(ticket)

    # Scoped to the signoff stage: the agentless `review` gate later in the
    # same auto run writes its own (spec-required) WORKFLOW_GATE audit row,
    # which the agentless test covers — an unscoped count would see both.
    approvals = db_session.exec(
        select(Approval).where(
            Approval.ticket_id == ticket.id,
            Approval.kind == ApprovalKind.WORKFLOW_GATE,
            Approval.stage_key == "signoff",
        )
    ).all()
    assert len(approvals) == 1, "the WORKFLOW_GATE approval row must still be created, not skipped"
    approval = approvals[0]
    assert approval.status == ApprovalStatus.APPROVED
    assert approval.resolved_by == "automation", (
        "auto-resolved gates must be distinguishable from a human sign-off in the "
        "approvals table (AC: absence of a row must never be the record of an "
        "auto-approval, and a resolved row must say who/what resolved it)"
    )
    assert approval.resolving_orchestration_run_id == orch_run.id

    assert _stage_status(db_session, ticket, "signoff") == StageStatus.DONE


def test_gate_required_stage_pauses_without_auto_approve(db_session: Session, tmp_path):
    """Regression guard: today's default behavior (no auto_approve) must not change."""
    ws = _make_workspace(db_session, tmp_path, "auto-mode-gate-off")
    ticket = _make_ticket(db_session, ws, external_id="gate-manual-1", title="Solo")

    BuiltinOrchestrator(db_session).execute(ticket, _profile(), auto_approve=False)
    db_session.refresh(ticket)

    approvals = db_session.exec(
        select(Approval).where(
            Approval.ticket_id == ticket.id, Approval.kind == ApprovalKind.WORKFLOW_GATE
        )
    ).all()
    assert len(approvals) == 1
    assert approvals[0].status == ApprovalStatus.PENDING
    assert _stage_status(db_session, ticket, "signoff") == StageStatus.AWAITING


# ---------------------------------------------------------------------------
# AC: agentless human-gate stages resolve the same way under auto_approve
# ---------------------------------------------------------------------------


def test_agentless_human_gate_auto_resolves_under_auto_approve(db_session: Session, tmp_path):
    ws = _make_workspace(db_session, tmp_path, "auto-mode-agentless-on")
    ticket = _make_ticket(db_session, ws, external_id="agentless-auto-1", title="Solo")

    orch_run = BuiltinOrchestrator(db_session).execute(ticket, _profile(), auto_approve=True)
    db_session.refresh(ticket)

    # With both stage-level gates auto-resolving, the whole workflow completes
    # in one execute() call.
    assert ticket.state == TicketState.DONE
    assert orch_run.status == OrchestrationRunStatus.SUCCEEDED

    review_approvals = [
        a
        for a in db_session.exec(select(Approval).where(Approval.ticket_id == ticket.id)).all()
        if a.stage_key == "review"
    ]
    assert len(review_approvals) == 1, "the agentless gate must still create an audit row"
    approval = review_approvals[0]
    assert approval.status == ApprovalStatus.APPROVED
    assert approval.resolved_by == "automation"
    assert approval.resolving_orchestration_run_id == orch_run.id


def test_agentless_human_gate_pauses_without_auto_approve(
    db_session: Session, tmp_path, monkeypatch
):
    """Regression guard: today's default behavior (no auto_approve) must not change."""
    # Resolving a gate normally auto-resumes the ticket on a background thread/
    # session (OrchestrationService._resume_orchestration -> schedule_orchestration).
    # Suppress that here so this test drives the second stage with its own
    # explicit execute() call on a single, controlled session instead of racing
    # a second one.
    monkeypatch.setattr(
        "loregarden.services.run_service.schedule_orchestration", lambda *a, **k: None
    )

    ws = _make_workspace(db_session, tmp_path, "auto-mode-agentless-off")
    ticket = _make_ticket(db_session, ws, external_id="agentless-manual-1", title="Solo")

    # Resolve the first gate manually so we reach the agentless "review" stage.
    BuiltinOrchestrator(db_session).execute(ticket, _profile(), auto_approve=False)
    db_session.refresh(ticket)
    signoff_approval = db_session.exec(
        select(Approval).where(Approval.ticket_id == ticket.id, Approval.stage_key == "signoff")
    ).first()
    from loregarden.services.orchestration import ApprovalService

    ApprovalService(db_session).resolve(signoff_approval.id, approved=True)
    db_session.refresh(ticket)

    BuiltinOrchestrator(db_session).execute(ticket, _profile(), auto_approve=False)
    db_session.refresh(ticket)

    assert ticket.state != TicketState.DONE
    assert _stage_status(db_session, ticket, "review") == StageStatus.AWAITING
    review_approval = db_session.exec(
        select(Approval).where(Approval.ticket_id == ticket.id, Approval.stage_key == "review")
    ).first()
    assert review_approval is not None
    assert review_approval.status == ApprovalStatus.PENDING


# ---------------------------------------------------------------------------
# AC: subtree end-to-end — parent with 2+ children crossing a gate_required
# stage and an agentless gate, entirely under one auto-mode orchestration call
# ---------------------------------------------------------------------------


def test_subtree_with_two_children_completes_end_to_end_under_auto_mode(
    db_session: Session, tmp_path
):
    ws = _make_workspace(db_session, tmp_path, "auto-mode-e2e")
    parent = _make_ticket(
        db_session,
        ws,
        external_id="e2e-parent-1",
        title="Parent",
        work_item_type=WorkItemType.FEATURE,
    )
    child_a = _make_ticket(
        db_session,
        ws,
        external_id="e2e-child-a",
        title="Child A",
        parent_ticket_id=parent.id,
    )
    child_b = _make_ticket(
        db_session,
        ws,
        external_id="e2e-child-b",
        title="Child B",
        parent_ticket_id=parent.id,
    )

    orch_run = BuiltinOrchestrator(db_session).execute(parent, _profile(), auto_approve=True)
    db_session.refresh(parent)
    db_session.refresh(child_a)
    db_session.refresh(child_b)

    assert child_a.state == TicketState.DONE, "child A must run its full workflow unattended"
    assert child_b.state == TicketState.DONE, "child B must run its full workflow unattended"
    assert parent.state == TicketState.DONE, "the parent's own workflow must also complete"
    assert orch_run.status == OrchestrationRunStatus.SUCCEEDED

    for ticket in (child_a, child_b, parent):
        gate_approval = db_session.exec(
            select(Approval).where(Approval.ticket_id == ticket.id, Approval.stage_key == "signoff")
        ).first()
        review_approval = db_session.exec(
            select(Approval).where(Approval.ticket_id == ticket.id, Approval.stage_key == "review")
        ).first()
        assert gate_approval.status == ApprovalStatus.APPROVED
        assert gate_approval.resolved_by == "automation"
        assert review_approval.status == ApprovalStatus.APPROVED
        assert review_approval.resolved_by == "automation"

    # Children run before the parent's own stages advance, and in
    # deterministic (_child_sort_key) order — not parallel.
    child_a_first_run = min(
        db_session.exec(select(AgentRun).where(AgentRun.ticket_id == child_a.id)).all(),
        key=lambda r: r.created_at,
    )
    parent_first_run = min(
        db_session.exec(select(AgentRun).where(AgentRun.ticket_id == parent.id)).all(),
        key=lambda r: r.created_at,
    )
    assert child_a_first_run.created_at <= parent_first_run.created_at


# ---------------------------------------------------------------------------
# AC: BLOCKED tickets/stages still stop the subtree run — auto-mode never
# converts a block into a pass
# ---------------------------------------------------------------------------


def test_blocked_child_stops_subtree_auto_run(db_session: Session, tmp_path, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_FORCE_AGENT_FAIL", "1")

    ws = _make_workspace(db_session, tmp_path, "auto-mode-blocked")
    parent = _make_ticket(db_session, ws, external_id="blocked-parent-1", title="Parent")
    child = _make_ticket(
        db_session, ws, external_id="blocked-child-1", title="Child", parent_ticket_id=parent.id
    )

    orch_run = BuiltinOrchestrator(db_session).execute(parent, _profile(), auto_approve=True)
    db_session.refresh(parent)
    db_session.refresh(child)

    assert child.state == TicketState.BLOCKED, "the child's first stage was forced to fail"
    assert parent.state != TicketState.DONE, (
        "auto-mode must never convert a blocked child into a passing subtree run"
    )
    assert orch_run.status in (OrchestrationRunStatus.SUCCEEDED, OrchestrationRunStatus.BLOCKED)
    assert "blocked" in (orch_run.error_message or "").lower()


# ---------------------------------------------------------------------------
# AC: AskUserQuestion-rail approvals still pause the run even in auto-mode
# ---------------------------------------------------------------------------


class _FakeStdout:
    def __init__(self, lines):
        self.lines = list(lines)
        self._closed = False

    def readline(self):
        if self.lines:
            return self.lines.pop(0) + "\n"
        self._closed = True
        return ""


class _FakeStdin:
    def __init__(self):
        self.writes: list[str] = []

    def write(self, data):
        self.writes.append(data.decode("utf-8") if isinstance(data, bytes) else data)

    def flush(self):
        return None


class _FakeProc:
    returncode = 0

    def __init__(self, lines):
        self.stdout = _FakeStdout(lines)
        self.stdin = _FakeStdin()
        self.stderr = None

    def poll(self):
        return 0 if self.stdout._closed else None

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.returncode = 1


def test_ask_user_question_still_pauses_for_an_auto_approved_run(tmp_path):
    """A stage run carrying auto_approve=True (as it would after Gap 1 propagation)
    must still stop and create a pending CLI_QUESTION approval when the agent asks
    a clarifying question — auto-mode answers nothing on the agent's behalf.
    """
    from loregarden.agents.cli_adapters import build_interactive_invocation
    from loregarden.agents.executors.permission_bridge import PermissionBridgeRunner
    from loregarden.services.seed import seed_database
    from sqlmodel import SQLModel, create_engine
    from sqlmodel.pool import StaticPool

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_database(session)
        ticket = session.exec(
            select(Ticket).where(Ticket.external_id == "03-wire-cli-agent-runner")
        ).first()
        run = AgentRun(
            run_code="run_auto_question_test",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="planner",
            stage_key="planning",
            status=RunStatus.RUNNING,
            auto_approve=True,
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        workspace_dir = tmp_path / "repo"
        workspace_dir.mkdir()
        prompt_file = tmp_path / "prompt.md"
        prompt_file.write_text("do work", encoding="utf-8")
        invocation = build_interactive_invocation(
            adapter="claude", prompt_file=prompt_file, workspace_root=workspace_dir
        )

        question_line = json.dumps(
            {
                "type": "control_request",
                "request_id": "q_auto_1",
                "request": {
                    "subtype": "can_use_tool",
                    "tool_name": "AskUserQuestion",
                    "tool_input": {
                        "questions": [
                            {
                                "question": "Which environment?",
                                "options": [{"label": "staging"}, {"label": "prod"}],
                            }
                        ]
                    },
                },
            }
        )
        result_line = json.dumps(
            {"type": "result", "session_id": "sess_auto_q", "subtype": "success"}
        )

        def fake_spawn(*args, **kwargs):
            return _FakeProc([question_line, result_line])

        captured: dict = {}

        def fake_wait(approval_id, **kwargs):
            # Capture the approval's state at the moment the bridge asks us to
            # wait for it — this is the assertion that matters: auto_approve
            # must not have already resolved or bypassed it.
            approval = session.get(Approval, approval_id)
            captured["kind"] = approval.kind
            captured["status"] = approval.status
            from loregarden.agents.executors.permission_bridge import ApprovalResolution
            from loregarden.services.orchestration import ApprovalService

            ApprovalService(session).resolve(
                approval_id, approved=True, answers={"Which environment?": "staging"}
            )
            approval = session.get(Approval, approval_id)
            return ApprovalResolution(
                approved=True,
                updated_input=json.loads(approval.response_json)["updated_input"],
            )

        bridge = PermissionBridgeRunner(session)
        bridge.run(
            run_id=run.id,
            ticket=ticket,
            invocation=invocation,
            prompt="do work",
            timeout_seconds=30,
            spawn_process=fake_spawn,
            wait_for_approval=fake_wait,
        )

        assert captured["kind"] == ApprovalKind.CLI_QUESTION
        assert captured["status"] == ApprovalStatus.PENDING, (
            "an auto_approve=True run must still leave the question approval PENDING "
            "for a human to answer, not auto-resolved or skipped"
        )


# ---------------------------------------------------------------------------
# AC: a subtree-wide bound exists for auto runs; exhausting it pauses visibly
#
# Spec gap (recorded via loregarden_append_checkpoint): no field name for this
# bound is settled yet. This test assumes a new `max_subtree_stages_per_run`
# on OrchestrationProfile (0 = unlimited, mirroring the existing
# `max_stages_per_run` convention) shared across the whole recursive auto run.
# If implement lands a different shape, update this test's setup accordingly
# rather than the assertions below, which encode the actual required behavior:
# bounded, visible, never silently unbounded or silently passing.
# ---------------------------------------------------------------------------


def test_subtree_wide_stage_bound_pauses_before_finishing_everything(db_session: Session, tmp_path):
    ws = _make_workspace(db_session, tmp_path, "auto-mode-subtree-bound")
    parent = _make_ticket(
        db_session,
        ws,
        external_id="bound-parent-1",
        title="Parent",
        work_item_type=WorkItemType.FEATURE,
    )
    child_a = _make_ticket(
        db_session, ws, external_id="bound-child-a", title="Child A", parent_ticket_id=parent.id
    )
    child_b = _make_ticket(
        db_session, ws, external_id="bound-child-b", title="Child B", parent_ticket_id=parent.id
    )

    # Each ticket's template has 3 runnable stages (work, signoff, review) before
    # done; 2 children + the parent's own stages is at least 9 stages total.
    # A bound of 2 must stop the subtree run well short of completion.
    profile = _profile(max_subtree_stages_per_run=2)

    orch_run = BuiltinOrchestrator(db_session).execute(parent, profile, auto_approve=True)
    db_session.refresh(parent)
    db_session.refresh(child_a)
    db_session.refresh(child_b)

    assert not (
        child_a.state == TicketState.DONE
        and child_b.state == TicketState.DONE
        and parent.state == TicketState.DONE
    ), "the subtree-wide bound must stop the run before the whole subtree completes"
    assert orch_run.status != OrchestrationRunStatus.FAILED, (
        "exhausting the bound is a visible pause, not a crash"
    )
    assert (orch_run.error_message or "").strip(), (
        "exhausting the subtree-wide bound must leave a visible reason, not silently stop"
    )


# ---------------------------------------------------------------------------
# Test Breaker additions: gaps the test-design pass left uncovered.
# ---------------------------------------------------------------------------


def test_grandchild_blocked_stops_subtree_auto_run(db_session: Session, tmp_path, monkeypatch):
    """A block two levels down must still halt the whole subtree, not just the
    immediate parent of the blocked ticket. The existing blocked-child test only
    covers one level of nesting; _orchestrate_incomplete_children's BLOCKED check
    happens per recursive call, so a bug that only checks *direct* children (not
    the return value bubbling up through nested execute()) would pass that test
    while silently completing a grandparent above a blocked grandchild.
    """
    monkeypatch.setenv("LOREGARDEN_FORCE_AGENT_FAIL", "1")

    ws = _make_workspace(db_session, tmp_path, "auto-mode-blocked-deep")
    grandparent = _make_ticket(db_session, ws, external_id="blocked-gp-1", title="Grandparent")
    parent = _make_ticket(
        db_session, ws, external_id="blocked-p-1", title="Parent", parent_ticket_id=grandparent.id
    )
    child = _make_ticket(
        db_session, ws, external_id="blocked-c-1", title="Child", parent_ticket_id=parent.id
    )

    orch_run = BuiltinOrchestrator(db_session).execute(grandparent, _profile(), auto_approve=True)
    db_session.refresh(grandparent)
    db_session.refresh(parent)
    db_session.refresh(child)

    assert child.state == TicketState.BLOCKED
    assert parent.state != TicketState.DONE, (
        "a grandchild block must stop the intermediate parent from completing"
    )
    assert grandparent.state != TicketState.DONE, (
        "a block two levels down must bubble all the way up to the top-level auto run"
    )
    assert orch_run.status in (OrchestrationRunStatus.SUCCEEDED, OrchestrationRunStatus.BLOCKED)
    assert "blocked" in (orch_run.error_message or "").lower()


def test_already_done_child_is_not_rerun_under_auto_mode(
    db_session: Session, tmp_path, monkeypatch
):
    """A child that already finished its workflow before the parent's auto run
    starts must be skipped (via _ticket_workflow_complete), not re-executed.
    Re-running a finished child would waste subtree-wide stage budget on work
    that's already done and could re-trigger side effects (commits, approvals)
    for a ticket nothing asked to touch again.
    """
    # This test drives the child to DONE manually first, so suppress the
    # background auto-resume a gate resolution would otherwise schedule.
    monkeypatch.setattr(
        "loregarden.services.run_service.schedule_orchestration", lambda *a, **k: None
    )

    ws = _make_workspace(db_session, tmp_path, "auto-mode-already-done")
    parent = _make_ticket(db_session, ws, external_id="done-parent-1", title="Parent")
    child = _make_ticket(
        db_session, ws, external_id="done-child-1", title="Child", parent_ticket_id=parent.id
    )

    from loregarden.services.orchestration import ApprovalService

    # Walk the child fully to DONE by hand: work -> signoff (gate) -> review
    # (agentless gate) -> done, resolving each gate manually.
    BuiltinOrchestrator(db_session).execute(child, _profile(), auto_approve=False)
    db_session.refresh(child)
    signoff = db_session.exec(
        select(Approval).where(Approval.ticket_id == child.id, Approval.stage_key == "signoff")
    ).first()
    ApprovalService(db_session).resolve(signoff.id, approved=True)
    db_session.refresh(child)

    BuiltinOrchestrator(db_session).execute(child, _profile(), auto_approve=False)
    db_session.refresh(child)
    review = db_session.exec(
        select(Approval).where(Approval.ticket_id == child.id, Approval.stage_key == "review")
    ).first()
    ApprovalService(db_session).resolve(review.id, approved=True)
    db_session.refresh(child)

    BuiltinOrchestrator(db_session).execute(child, _profile(), auto_approve=False)
    db_session.refresh(child)
    assert child.state == TicketState.DONE, (
        "test setup: child must be fully done before the parent auto run"
    )

    runs_before = len(db_session.exec(select(AgentRun).where(AgentRun.ticket_id == child.id)).all())

    BuiltinOrchestrator(db_session).execute(parent, _profile(), auto_approve=True)
    db_session.refresh(child)
    db_session.refresh(parent)

    runs_after = len(db_session.exec(select(AgentRun).where(AgentRun.ticket_id == child.id)).all())
    assert runs_after == runs_before, (
        "an already-DONE child must not get new agent runs when the parent "
        f"auto-runs; had {runs_before} runs before, {runs_after} after"
    )
    assert child.state == TicketState.DONE


def test_children_run_in_type_then_priority_order_not_alphabetical(db_session: Session, tmp_path):
    """_child_sort_key orders by work_item_type, then priority, then external_id.
    Pick external_ids that sort the OPPOSITE way alphabetically from the
    expected type order, so a regression that accidentally sorts by
    external_id (or creation order) instead of type/priority would be caught
    instead of coincidentally passing.
    """
    ws = _make_workspace(db_session, tmp_path, "auto-mode-order")
    parent = _make_ticket(
        db_session,
        ws,
        external_id="order-parent-1",
        title="Parent",
        work_item_type=WorkItemType.FEATURE,
    )
    # type_order: FEATURE=1, TASK=3, BUG=4 — expected run order is feature, task, bug.
    # external_id order is the reverse (zzz < mmm < aaa is false; deliberately
    # alphabetically descending vs. the expected type order) to rule out a
    # sort-by-external_id bug from passing by coincidence.
    bug_child = _make_ticket(
        db_session,
        ws,
        external_id="order-aaa-bug",
        title="Bug child",
        work_item_type=WorkItemType.BUG,
        parent_ticket_id=parent.id,
    )
    task_child = _make_ticket(
        db_session,
        ws,
        external_id="order-mmm-task",
        title="Task child",
        work_item_type=WorkItemType.TASK,
        parent_ticket_id=parent.id,
    )
    feature_child = _make_ticket(
        db_session,
        ws,
        external_id="order-zzz-feature",
        title="Feature child",
        work_item_type=WorkItemType.FEATURE,
        parent_ticket_id=parent.id,
    )

    BuiltinOrchestrator(db_session).execute(parent, _profile(), auto_approve=True)

    def _first_run_time(ticket: Ticket):
        runs = db_session.exec(select(AgentRun).where(AgentRun.ticket_id == ticket.id)).all()
        assert runs, f"expected at least one run for {ticket.external_id}"
        return min(r.created_at for r in runs)

    feature_t = _first_run_time(feature_child)
    task_t = _first_run_time(task_child)
    bug_t = _first_run_time(bug_child)

    assert feature_t <= task_t <= bug_t, (
        "children must run in work_item_type order (feature, then task, then bug), "
        f"not external_id/creation order; got feature={feature_t}, task={task_t}, bug={bug_t}"
    )


def test_subtree_wide_bound_caps_total_completed_stages_across_all_tickets(
    db_session: Session, tmp_path
):
    """The bound is documented as subtree-wide: it must count stages across the
    parent AND every descendant combined, not reset per nested execute() call
    (that's exactly what max_stages_per_run already does today, and exactly the
    gap this ticket exists to close). A bound of 1 must leave at most one
    successfully-completed AgentRun across the parent + both children combined —
    a bound that merely limited each ticket's *own* stages to 1 would let 3
    tickets each complete 1 stage (3 total), which this test would catch.
    """
    ws = _make_workspace(db_session, tmp_path, "auto-mode-bound-strict")
    parent = _make_ticket(
        db_session,
        ws,
        external_id="strict-bound-parent",
        title="Parent",
        work_item_type=WorkItemType.FEATURE,
    )
    child_a = _make_ticket(
        db_session,
        ws,
        external_id="strict-bound-child-a",
        title="Child A",
        parent_ticket_id=parent.id,
    )
    child_b = _make_ticket(
        db_session,
        ws,
        external_id="strict-bound-child-b",
        title="Child B",
        parent_ticket_id=parent.id,
    )

    profile = _profile(max_subtree_stages_per_run=1)
    orch_run = BuiltinOrchestrator(db_session).execute(parent, profile, auto_approve=True)

    all_tickets = [parent, child_a, child_b]
    for t in all_tickets:
        db_session.refresh(t)

    completed_runs = [
        r
        for t in all_tickets
        for r in db_session.exec(select(AgentRun).where(AgentRun.ticket_id == t.id)).all()
        if r.status == RunStatus.SUCCEEDED
    ]
    assert len(completed_runs) <= 1, (
        "a subtree-wide bound of 1 must cap completed stages across the WHOLE "
        f"subtree at 1, not per ticket; found {len(completed_runs)} completed "
        f"runs across parent+children: {[(r.ticket_id, r.stage_key) for r in completed_runs]}"
    )
    assert orch_run.status != OrchestrationRunStatus.FAILED
    assert (orch_run.error_message or "").strip()


# ---------------------------------------------------------------------------
# Test Breaker pass 2: the AC says auto-resolved approvals must be
# *distinguishable* from human ones. Every existing test only checks the
# auto-resolved side of that; nothing proves a genuinely human-resolved gate
# does NOT also read as "automation" once the resolved_by column exists (e.g.
# a naive migration that backfills/defaults resolved_by to "automation" for
# every resolution path, or a resolve() implementation that hardcodes the
# string in ApprovalService instead of only in the auto-mode code path, would
# pass every test above while silently breaking the audit guarantee).
# ---------------------------------------------------------------------------


def test_human_resolved_gate_is_not_labeled_automation(db_session: Session, tmp_path):
    ws = _make_workspace(db_session, tmp_path, "auto-mode-human-label")
    ticket = _make_ticket(db_session, ws, external_id="gate-human-1", title="Solo")

    BuiltinOrchestrator(db_session).execute(ticket, _profile(), auto_approve=False)
    db_session.refresh(ticket)
    signoff = db_session.exec(
        select(Approval).where(Approval.ticket_id == ticket.id, Approval.stage_key == "signoff")
    ).first()
    assert signoff.status == ApprovalStatus.PENDING

    from loregarden.services.orchestration import ApprovalService

    ApprovalService(db_session).resolve(signoff.id, approved=True)
    db_session.refresh(signoff)

    assert signoff.status == ApprovalStatus.APPROVED
    assert signoff.resolved_by != "automation", (
        "a human clicking approve in the inbox must not be recorded as "
        "'automation' — the audit trail's whole purpose is to tell the two "
        "apart, and a hardcoded/defaulted resolved_by would make every row "
        "look auto-approved regardless of who actually approved it"
    )
    assert signoff.resolving_orchestration_run_id is None, (
        "a manually-resolved approval was not resolved by any orchestration "
        "run's auto-mode sweep, so this column must stay empty for it"
    )


# ---------------------------------------------------------------------------
# Test Breaker pass 2: a ticket that paused for a human BEFORE auto_approve
# was ever turned on, then gets swept up by a later top-level auto run (e.g.
# a parent kicked off in auto mode with a child that already has a stale
# PENDING gate from a previous manual run). The ticket spec only describes
# gates encountered *during* an auto_approve run; it says nothing about
# resuming into a stage that is already AWAITING. An implementation that only
# auto-resolves gates at the moment orchestration.py:790 creates the Approval
# row (i.e. only on first entry to the stage) would leave this ticket parked
# at AWAITING forever even under auto_approve=True on every later call —
# defeating unattended subtree runs the first time any ticket in the tree was
# ever touched manually.
# ---------------------------------------------------------------------------


def test_auto_run_resolves_a_stage_that_was_already_awaiting_before_auto_mode(
    db_session: Session, tmp_path
):
    ws = _make_workspace(db_session, tmp_path, "auto-mode-late-adopt")
    ticket = _make_ticket(db_session, ws, external_id="gate-late-adopt-1", title="Solo")

    # Reach AWAITING the old-fashioned way: no auto_approve at all.
    BuiltinOrchestrator(db_session).execute(ticket, _profile(), auto_approve=False)
    db_session.refresh(ticket)
    assert _stage_status(db_session, ticket, "signoff") == StageStatus.AWAITING
    pending = db_session.exec(
        select(Approval).where(
            Approval.ticket_id == ticket.id,
            Approval.stage_key == "signoff",
            Approval.status == ApprovalStatus.PENDING,
        )
    ).one()

    # Now someone (or a parent's subtree auto run) re-invokes execute() with
    # auto_approve=True on the very same, already-paused ticket.
    orch_run = BuiltinOrchestrator(db_session).execute(ticket, _profile(), auto_approve=True)
    db_session.refresh(ticket)
    db_session.refresh(pending)

    assert pending.status == ApprovalStatus.APPROVED, (
        "re-entering an already-AWAITING gate under auto_approve=True must "
        "resolve the existing pending approval, not leave the ticket stuck "
        "at AWAITING forever because the row predates this run"
    )
    assert pending.resolved_by == "automation"
    assert pending.resolving_orchestration_run_id == orch_run.id
    assert ticket.state == TicketState.DONE


# ---------------------------------------------------------------------------
# Test Breaker pass 2: boundary-exact behavior for the subtree-wide bound.
# The existing bound tests only prove "some limit less than the total stops
# the run short." Neither proves the bound is inclusive/correct at the exact
# edge — an off-by-one (`>` vs `>=`, or counting before vs. after increment)
# would pass both existing tests while still being wrong by one stage in
# either direction.
# ---------------------------------------------------------------------------


def test_subtree_bound_exact_stage_count_completes_fully(db_session: Session, tmp_path):
    ws = _make_workspace(db_session, tmp_path, "auto-mode-bound-exact")
    parent = _make_ticket(
        db_session,
        ws,
        external_id="exact-bound-parent",
        title="Parent",
        work_item_type=WorkItemType.FEATURE,
    )
    child = _make_ticket(
        db_session, ws, external_id="exact-bound-child", title="Child", parent_ticket_id=parent.id
    )

    # Each ticket runs exactly 3 stages to DONE (work, signoff, review) under
    # this test template; parent + 1 child = 6 stages total, exactly.
    profile = _profile(max_subtree_stages_per_run=6)
    orch_run = BuiltinOrchestrator(db_session).execute(parent, profile, auto_approve=True)
    db_session.refresh(parent)
    db_session.refresh(child)

    assert child.state == TicketState.DONE, (
        "a bound exactly equal to the required stage count must not stop the run one stage short"
    )
    assert parent.state == TicketState.DONE
    assert orch_run.status == OrchestrationRunStatus.SUCCEEDED


def test_subtree_bound_one_less_than_exact_stops_short(db_session: Session, tmp_path):
    ws = _make_workspace(db_session, tmp_path, "auto-mode-bound-short")
    parent = _make_ticket(
        db_session,
        ws,
        external_id="short-bound-parent",
        title="Parent",
        work_item_type=WorkItemType.FEATURE,
    )
    child = _make_ticket(
        db_session, ws, external_id="short-bound-child", title="Child", parent_ticket_id=parent.id
    )

    profile = _profile(max_subtree_stages_per_run=5)
    orch_run = BuiltinOrchestrator(db_session).execute(parent, profile, auto_approve=True)
    db_session.refresh(parent)
    db_session.refresh(child)

    assert not (child.state == TicketState.DONE and parent.state == TicketState.DONE), (
        "one stage short of the exact total must not silently complete the subtree"
    )
    assert orch_run.status != OrchestrationRunStatus.FAILED
    assert (orch_run.error_message or "").strip()


# ---------------------------------------------------------------------------
# Test Breaker pass 2: `max_subtree_stages_per_run` is documented (in the
# ticket's own spec-gap checkpoint) to mirror `max_stages_per_run`'s existing
# 0-means-unlimited convention. Nothing currently pins that default/zero
# value to "unlimited" explicitly — every other e2e test uses the default
# profile incidentally, so a regression that reinterpreted 0 as "stop
# immediately" would only be caught by cascading failures across unrelated
# tests, not a direct assertion of the contract.
# ---------------------------------------------------------------------------


def test_subtree_bound_zero_means_unlimited(db_session: Session, tmp_path):
    ws = _make_workspace(db_session, tmp_path, "auto-mode-bound-unlimited")
    parent = _make_ticket(
        db_session,
        ws,
        external_id="unlimited-bound-parent",
        title="Parent",
        work_item_type=WorkItemType.FEATURE,
    )
    child_a = _make_ticket(
        db_session,
        ws,
        external_id="unlimited-bound-child-a",
        title="Child A",
        parent_ticket_id=parent.id,
    )
    child_b = _make_ticket(
        db_session,
        ws,
        external_id="unlimited-bound-child-b",
        title="Child B",
        parent_ticket_id=parent.id,
    )

    profile = _profile(max_subtree_stages_per_run=0)
    orch_run = BuiltinOrchestrator(db_session).execute(parent, profile, auto_approve=True)
    db_session.refresh(parent)
    db_session.refresh(child_a)
    db_session.refresh(child_b)

    assert child_a.state == TicketState.DONE
    assert child_b.state == TicketState.DONE
    assert parent.state == TicketState.DONE
    assert orch_run.status == OrchestrationRunStatus.SUCCEEDED


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
