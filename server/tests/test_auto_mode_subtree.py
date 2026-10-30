"""Parent auto-mode: run a ticket subtree end-to-end.

Exercises the real BuiltinOrchestrator/OrchestrationService/PermissionBridgeRunner
pipeline against a small hand-built workflow template (work -> signoff ->
review [agentless with no exit actions] -> done). Human-required exit actions
use a dedicated fixture: ``auto_approve`` must never resolve those gates.
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
from loregarden.services.acceptance_criteria import serialize_criteria
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.hold_line import CUT_MARKER, ERROR_MESSAGE_LIMIT
from loregarden.services.integration_review import backfill_integration_reviews
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_profile import OrchestrationProfile
from loregarden.services.oversized_ticket_split import (
    MAX_ACCEPTANCE_CRITERIA,
    TICKET_SPLIT_CLI_PROFILE,
)
from loregarden.services.ticket_dependencies import TicketDependencyService
from loregarden.services.workflow_state import parse_stage_map, set_stage_status
from sqlalchemy import text
from sqlmodel import Session, select
from tests.hold_line_helpers import (
    assert_plain_single_line,
    assert_reconciles,
    parse_hold_line,
)
from tests.repo_templates import from_template
from tests.worktree_helpers import seed_stage_report_contract

# Stage shapes the ticket names explicitly:
#   work    - ordinary agent stage, nothing special
#   signoff - agent stage; default has no exit actions (auto_mode e2e completes)
#   review  - agentless with no exit actions → completes, not a human gate (AC-5)
#   done    - terminal
# Gate-behavior tests use `_OPERATOR_GATE_STAGES` (operator_judgment on signoff).
_LEGACY_JUDGMENT = {
    "key": "legacy-stage-sign-off",
    "label": "Approve completion",
    "requirement": {
        "kind": "operator_judgment",
        "decision_prompt": "Approve completion of stage.",
    },
}
_STAGES = [
    {
        "key": "work",
        "name": "Work",
        "agent_id": "planner",
        "skill_name": "",
        "stage_type": "agent",
        "order": 1,
        "exit_actions_enabled": False,
        "exit_actions": [],
    },
    {
        "key": "signoff",
        "name": "Sign-off",
        # Not the planner: a planner's design-plan gate is signed off by default
        # (746). Operator-judgment gates use `_OPERATOR_GATE_STAGES`.
        "agent_id": "backend_implementer",
        "skill_name": "",
        "stage_type": "agent",
        "order": 2,
        "exit_actions_enabled": False,
        "exit_actions": [],
    },
    {
        "key": "review",
        "name": "Review",
        "agent_id": "",
        "skill_name": "",
        "stage_type": "agent",
        "order": 3,
        "exit_actions_enabled": False,
        "exit_actions": [],
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
_OPERATOR_GATE_STAGES = [
    {
        "key": "work",
        "name": "Work",
        "agent_id": "planner",
        "skill_name": "",
        "stage_type": "agent",
        "order": 1,
        "exit_actions_enabled": False,
        "exit_actions": [],
    },
    {
        "key": "signoff",
        "name": "Sign-off",
        "agent_id": "backend_implementer",
        "skill_name": "",
        "stage_type": "agent",
        "order": 2,
        "exit_actions_enabled": True,
        "exit_actions": [_LEGACY_JUDGMENT],
    },
    {
        "key": "review",
        "name": "Review",
        "agent_id": "",
        "skill_name": "",
        "stage_type": "agent",
        "order": 3,
        "exit_actions_enabled": False,
        "exit_actions": [],
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
_OPERATOR_GATE_STAGES = [
    _STAGES[0],
    {
        **_STAGES[1],
        "exit_actions_enabled": True,
        "exit_actions": [
            {
                "key": "accept-residual-risk",
                "label": "Accept residual risk",
                "requirement": {
                    "kind": "operator_judgment",
                    "decision_prompt": "Are the documented residual risks acceptable?",
                },
            }
        ],
    },
    _STAGES[2],
    _STAGES[3],
]
_TRANSITIONS = [
    {"from": "work", "to": "signoff", "when": "pass"},
    {"from": "signoff", "to": "review", "when": "pass"},
    {"from": "review", "to": "done", "when": "pass"},
]


def _git_repo(tmp_path: Path) -> Path:
    """A copy of a seeded repo built once per process (`tests/repo_templates.py`)."""
    root = tmp_path / f"repo-{len(list(tmp_path.iterdir()))}"
    return from_template(root, "auto_mode_subtree", _build_git_repo)


def _build_git_repo(root: Path) -> None:
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"], cwd=root, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=root, check=True, capture_output=True
    )
    (root / "README.md").write_text("seed\n", encoding="utf-8")
    # A workspace with no stage-report contract cannot dispatch: the preflight
    # refuses it, because agents there would be given no report format at all.
    seed_stage_report_contract(root)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=root, check=True, capture_output=True)


def _make_workspace(
    db_session: Session,
    tmp_path: Path,
    slug: str,
    *,
    stages: list[dict] = _STAGES,
    transitions: list[dict] = _TRANSITIONS,
) -> Workspace:
    template = WorkflowTemplate(
        slug=f"{slug}-tpl",
        name="Auto-mode subtree test template",
        stages_json=json.dumps(stages),
        transitions_json=json.dumps(transitions),
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
# AC-6/AC-12: auto_approve cannot resolve human-required exit actions
# ---------------------------------------------------------------------------


def test_auto_approve_does_not_resolve_operator_judgment(db_session: Session, tmp_path):
    ws = _make_workspace(db_session, tmp_path, "auto-mode-gate-on", stages=_OPERATOR_GATE_STAGES)
    ticket = _make_ticket(db_session, ws, external_id="gate-auto-1", title="Solo")

    BuiltinOrchestrator(db_session).execute(ticket, _profile(), auto_approve=True)
    db_session.refresh(ticket)

    approvals = db_session.exec(
        select(Approval).where(
            Approval.ticket_id == ticket.id,
            Approval.kind == ApprovalKind.WORKFLOW_GATE,
            Approval.stage_key == "signoff",
        )
    ).all()
    assert len(approvals) == 1
    approval = approvals[0]
    assert approval.status == ApprovalStatus.PENDING
    assert approval.resolved_by == ""
    assert approval.resolving_orchestration_run_id is None
    assert _stage_status(db_session, ticket, "signoff") == StageStatus.AWAITING


def test_operator_judgment_pauses_without_auto_approve(db_session: Session, tmp_path):
    ws = _make_workspace(db_session, tmp_path, "auto-mode-gate-off", stages=_OPERATOR_GATE_STAGES)
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
# AC-5: an agentless stage with no exit actions is not a human gate
# ---------------------------------------------------------------------------


def test_agentless_stage_without_actions_creates_no_gate_under_auto_approve(
    db_session: Session, tmp_path
):
    ws = _make_workspace(db_session, tmp_path, "auto-mode-agentless-on")
    ticket = _make_ticket(db_session, ws, external_id="agentless-auto-1", title="Solo")

    orch_run = BuiltinOrchestrator(db_session).execute(ticket, _profile(), auto_approve=True)
    db_session.refresh(ticket)

    assert ticket.state == TicketState.DONE
    assert orch_run.status == OrchestrationRunStatus.SUCCEEDED
    assert (
        db_session.exec(
            select(Approval).where(Approval.ticket_id == ticket.id, Approval.stage_key == "review")
        ).all()
        == []
    )


def test_agentless_stage_without_actions_creates_no_gate_in_attended_mode(
    db_session: Session, tmp_path
):
    ws = _make_workspace(db_session, tmp_path, "auto-mode-agentless-off")
    ticket = _make_ticket(db_session, ws, external_id="agentless-manual-1", title="Solo")

    BuiltinOrchestrator(db_session).execute(ticket, _profile(), auto_approve=False)
    db_session.refresh(ticket)
    assert ticket.state == TicketState.DONE
    assert (
        db_session.exec(
            select(Approval).where(Approval.ticket_id == ticket.id, Approval.stage_key == "review")
        ).all()
        == []
    )


# ---------------------------------------------------------------------------
# Regression: a no-action workflow remains unattended under auto mode
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
    assert parent.state == TicketState.DONE, (
        "the parent must complete by aggregation once its children are done"
    )
    assert orch_run.status == OrchestrationRunStatus.SUCCEEDED

    for ticket in (child_a, child_b):
        assert db_session.exec(select(Approval).where(Approval.ticket_id == ticket.id)).all() == []

    assert db_session.exec(select(AgentRun).where(AgentRun.ticket_id == parent.id)).all() == [], (
        "an aggregator parent must not run any of its own workflow stages"
    )
    assert db_session.exec(select(Approval).where(Approval.ticket_id == parent.id)).all() == [], (
        "an aggregator parent opens no gates of its own"
    )
    assert db_session.exec(select(AgentRun).where(AgentRun.ticket_id == child_a.id)).all()
    assert db_session.exec(select(AgentRun).where(AgentRun.ticket_id == child_b.id)).all()


def test_aggregator_parent_creates_no_git_branch_of_its_own(db_session: Session, tmp_path):
    """The point of skipping a parent's own stages: no unused ticket branch is
    ever created for it. ensure_ticket_branch fires per stage agent, so a parent
    that runs zero stages leaves no branch behind — while its child, which runs
    its stages for real, does get one.
    """
    from loregarden.services.branch_names import resolve_ticket_branch

    ws = _make_workspace(db_session, tmp_path, "auto-mode-no-parent-branch")
    parent = _make_ticket(
        db_session,
        ws,
        external_id="nobranch-parent",
        title="Parent",
        work_item_type=WorkItemType.FEATURE,
    )
    child = _make_ticket(
        db_session, ws, external_id="nobranch-child", title="Child", parent_ticket_id=parent.id
    )

    BuiltinOrchestrator(db_session).execute(parent, _profile(), auto_approve=True)
    db_session.refresh(parent)
    db_session.refresh(child)
    assert parent.state == TicketState.DONE
    assert child.state == TicketState.DONE

    branches = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=ws.repo_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert resolve_ticket_branch(parent) not in branches, (
        f"an aggregator parent must not create a branch; found {branches}"
    )
    assert resolve_ticket_branch(child) in branches, (
        f"the child ran its stages and must have a branch; found {branches}"
    )


def test_aggregator_parent_without_terminal_stage_completes_not_blocks(
    db_session: Session, tmp_path
):
    """A parent whose own workflow has no terminal ('done') stage — an older
    instance or template drift — must still complete once its children are done,
    not block with "Workflow has no done stage". Its children carry the work, so
    its own stage shape is moot.
    """
    from loregarden.services.studio_routing import is_terminal_stage

    no_terminal = [stage for stage in _STAGES if stage["key"] != "done"]
    ws = _make_workspace(db_session, tmp_path, "auto-mode-no-terminal", stages=no_terminal)
    parent = _make_ticket(
        db_session,
        ws,
        external_id="nt-parent",
        title="Parent",
        work_item_type=WorkItemType.FEATURE,
    )
    child = _make_ticket(
        db_session, ws, external_id="nt-child", title="Child", parent_ticket_id=parent.id
    )
    # Child already complete (isolate the parent-finalize path).
    child.state = TicketState.DONE
    db_session.add(child)
    db_session.commit()

    _, stages = OrchestrationService(db_session)._resolve_stages(parent)
    assert stages and not any(is_terminal_stage(s) for s in stages), (
        "test setup: the parent's workflow must have no terminal stage"
    )

    orch_run = BuiltinOrchestrator(db_session).execute(parent, _profile(), auto_approve=True)
    db_session.refresh(parent)
    assert parent.state == TicketState.DONE, (
        "an aggregator parent whose workflow lacks a terminal stage must complete, not block"
    )
    assert orch_run.status != OrchestrationRunStatus.BLOCKED
    assert "done stage" not in (orch_run.error_message or "")


def _make_review_child(db_session, ws, *, external_id, parent):
    child = _make_ticket(
        db_session,
        ws,
        external_id=external_id,
        title="Integration review",
        work_item_type=WorkItemType.CAPABILITY,
        parent_ticket_id=parent.id,
    )
    child.is_integration_review = True
    db_session.add(child)
    db_session.commit()
    db_session.refresh(child)
    return child


def test_integration_review_child_runs_last_via_dependency(db_session: Session, tmp_path):
    """A child that depends on its siblings runs after them, even though its
    external_id would sort it first without the dependency — proving run order
    comes from the dependency edges, not child_sort_key.
    """
    ws = _make_workspace(db_session, tmp_path, "auto-mode-review-last")
    parent = _make_ticket(
        db_session,
        ws,
        external_id="rev-parent",
        title="Parent",
        work_item_type=WorkItemType.FEATURE,
    )
    cap_b = _make_ticket(
        db_session,
        ws,
        external_id="cap-b",
        title="Cap B",
        work_item_type=WorkItemType.CAPABILITY,
        parent_ticket_id=parent.id,
    )
    cap_c = _make_ticket(
        db_session,
        ws,
        external_id="cap-c",
        title="Cap C",
        work_item_type=WorkItemType.CAPABILITY,
        parent_ticket_id=parent.id,
    )
    # external_id "aaa-*" sorts first — without the dependency it would run FIRST.
    review = _make_review_child(db_session, ws, external_id="aaa-review", parent=parent)
    deps = TicketDependencyService(db_session)
    deps.add_dependency(review.id, cap_b.id)
    deps.add_dependency(review.id, cap_c.id)

    BuiltinOrchestrator(db_session).execute(parent, _profile(), auto_approve=True)
    for t in (parent, cap_b, cap_c, review):
        db_session.refresh(t)
    assert review.state == TicketState.DONE
    assert parent.state == TicketState.DONE

    def _first_run_at(ticket):
        runs = db_session.exec(select(AgentRun).where(AgentRun.ticket_id == ticket.id)).all()
        assert runs, f"{ticket.external_id} must have run"
        return min(r.created_at for r in runs)

    review_start = _first_run_at(review)
    assert review_start > _first_run_at(cap_b), "review must start after sibling cap-b"
    assert review_start > _first_run_at(cap_c), "review must start after sibling cap-c"


def test_backfill_adds_integration_review_with_sibling_dependencies(db_session: Session, tmp_path):
    """Backfill gives an existing childful feature a review capability that depends
    on its siblings, and is idempotent on a second pass.
    """
    ws = _make_workspace(db_session, tmp_path, "auto-mode-backfill")
    parent = _make_ticket(
        db_session,
        ws,
        external_id="bf-parent",
        title="Parent",
        work_item_type=WorkItemType.FEATURE,
    )
    cap_a = _make_ticket(
        db_session,
        ws,
        external_id="bf-cap-a",
        title="Cap A",
        work_item_type=WorkItemType.CAPABILITY,
        parent_ticket_id=parent.id,
    )
    cap_b = _make_ticket(
        db_session,
        ws,
        external_id="bf-cap-b",
        title="Cap B",
        work_item_type=WorkItemType.CAPABILITY,
        parent_ticket_id=parent.id,
    )

    created = backfill_integration_reviews(db_session, workspace_slug=ws.slug)
    assert len(created) == 1
    review = db_session.get(Ticket, created[0])
    assert review.is_integration_review is True
    assert review.parent_ticket_id == parent.id
    assert review.work_item_type == WorkItemType.CAPABILITY
    assert set(TicketDependencyService(db_session).prerequisites(review.id)) == {cap_a.id, cap_b.id}

    # Idempotent: a second pass creates nothing new.
    again = backfill_integration_reviews(db_session, workspace_slug=ws.slug)
    assert again == []
    reviews = db_session.exec(
        select(Ticket).where(
            Ticket.parent_ticket_id == parent.id,
            Ticket.is_integration_review == True,  # noqa: E712
        )
    ).all()
    assert len(reviews) == 1


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
            select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
        ).first()
        run = AgentRun(
            run_code="run_auto_question_test",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="planner",
            stage_key="plan",
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


def test_a_child_waiting_on_another_workspace_is_held_and_named(
    db_session: Session, tmp_path
) -> None:
    """676: the orchestrator must not pick up work that cannot succeed yet.

    Dependency edges were order-only. `order_children_for_subtree` drops every
    prerequisite outside the sibling set, which is every cross-workspace edge —
    so nine such edges existed, rendered in the UI as prerequisites, and the
    orchestrator started the dependent anyway and reported the resulting failure
    as the child's own.

    Held rather than blocked, and named rather than counted: the reader cannot
    see the other workspace's board, so the message has to carry the workspace
    or it sends them somewhere they will not find it.
    """
    ws = _make_workspace(db_session, tmp_path, "auto-mode-cross-workspace")
    parent = _make_ticket(db_session, ws, external_id="waiting-p-1", title="Parent")
    child = _make_ticket(
        db_session, ws, external_id="waiting-c-1", title="Child", parent_ticket_id=parent.id
    )
    upstream_ws = _make_workspace(db_session, tmp_path, "auto-mode-upstream")
    upstream = _make_ticket(
        db_session, upstream_ws, external_id="lor-extract-lore-35", title="Shared library"
    )
    TicketDependencyService(db_session).add_dependency(child.id, upstream.id)
    db_session.commit()

    orch_run = BuiltinOrchestrator(db_session).execute(parent, _profile(), auto_approve=True)
    db_session.refresh(parent)
    db_session.refresh(child)

    message = orch_run.error_message or ""
    assert "waiting on a prerequisite" in message.lower(), message
    assert "lor-extract-lore-35" in message, message
    assert "auto-mode-upstream" in message, (
        "the prerequisite's workspace has to be named — an external id alone "
        f"points the reader at the wrong board: {message}"
    )
    assert parent.state != TicketState.DONE, (
        "a parent whose child never ran must not report itself complete"
    )


def test_a_child_runs_once_its_cross_workspace_prerequisite_is_settled(
    db_session: Session, tmp_path
) -> None:
    """The control. Without it the test above passes for an orchestrator that
    holds every child forever, which would stop the subtree rather than order it.
    """
    ws = _make_workspace(db_session, tmp_path, "auto-mode-cross-workspace-ok")
    parent = _make_ticket(db_session, ws, external_id="ready-p-1", title="Parent")
    child = _make_ticket(
        db_session, ws, external_id="ready-c-1", title="Child", parent_ticket_id=parent.id
    )
    upstream_ws = _make_workspace(db_session, tmp_path, "auto-mode-upstream-ok")
    upstream = _make_ticket(
        db_session, upstream_ws, external_id="lor-extract-lore-99", title="Shared library"
    )
    upstream.state = TicketState.DONE
    TicketDependencyService(db_session).add_dependency(child.id, upstream.id)
    db_session.commit()

    orch_run = BuiltinOrchestrator(db_session).execute(parent, _profile(), auto_approve=True)

    assert "waiting on a prerequisite" not in (orch_run.error_message or "").lower(), (
        orch_run.error_message
    )


def test_already_done_child_is_not_rerun_under_auto_mode(db_session: Session, tmp_path):
    """A child that already finished its workflow before the parent's auto run
    starts must be skipped (via _ticket_workflow_complete), not re-executed.
    """
    ws = _make_workspace(db_session, tmp_path, "auto-mode-already-done")
    parent = _make_ticket(db_session, ws, external_id="done-parent-1", title="Parent")
    child = _make_ticket(
        db_session, ws, external_id="done-child-1", title="Child", parent_ticket_id=parent.id
    )

    # No-action workflow completes in one attended execute call.
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
        "an already-done child must not be re-executed by the parent's auto run"
    )
    assert parent.state == TicketState.DONE
    assert child.state == TicketState.DONE


def test_ticket_workflow_complete_true_for_done_ticket_with_incomplete_stages():
    """A ticket whose state is terminal (DONE/WONT_DO) is workflow-complete even
    when its stage instance still has required stages PENDING. A ticket can reach
    DONE without every stage ticked (manual completion, review shortcut, imported
    or older tickets, a stage-map reset); the subtree loop must treat that as
    complete, not as an incomplete child to re-run.
    """
    from unittest.mock import MagicMock

    from loregarden.services.subtree_auto_run import ticket_workflow_complete

    stage = MagicMock(key="implement", optional=False)
    orch = MagicMock()
    orch._resolve_stages.return_value = (MagicMock(), [stage])

    ticket = MagicMock(state=TicketState.DONE)
    assert ticket_workflow_complete(orch, ticket) is True
    ticket.state = TicketState.WONT_DO
    assert ticket_workflow_complete(orch, ticket) is True
    # The terminal-state short-circuit means the stage instance is never consulted.
    orch._resolve_stages.assert_not_called()


def test_done_child_with_incomplete_stages_does_not_block_later_siblings(
    db_session: Session, tmp_path
):
    """The subtree bug: a first child left DONE with an incomplete stage instance
    (state=DONE but required stages still PENDING) must be skipped as complete so
    the parent auto run continues on to the *next* sibling — instead of re-entering
    execute() on the done child (which no-ops on its terminal state), still seeing
    it "incomplete", and pausing the whole subtree there. Reproduces the real 219→
    220→[221 DONE, 223, 225] tree where 221 was DONE with only `triage` ticked and
    223/225 never ran.
    """
    ws = _make_workspace(db_session, tmp_path, "auto-mode-done-incomplete")
    parent = _make_ticket(db_session, ws, external_id="di-parent-1", title="Parent")
    # First child: DONE at the ticket level, but its stage instance is untouched
    # (every required stage still PENDING) — exactly the divergent state the bug
    # tripped on. Written in raw SQL: `db.closed_workflow_guard` closes the
    # stages of any ticket closed through the ORM, so this state now exists only
    # in rows that predate it, which is what the skip still has to tolerate.
    done_child = _make_ticket(
        db_session,
        ws,
        external_id="di-child-done",
        title="Done child",
        parent_ticket_id=parent.id,
    )
    db_session.execute(
        text("UPDATE tickets SET state = :s WHERE id = :id"),
        {"s": TicketState.DONE.value, "id": done_child.id},
    )
    db_session.commit()
    db_session.refresh(done_child)
    assert _stage_status(db_session, done_child, "work") == StageStatus.PENDING, (
        "test setup: the done child must have an incomplete stage instance"
    )
    # Later sibling: a fresh backlog ticket that must get run once the loop
    # advances past the done child.
    sibling = _make_ticket(
        db_session,
        ws,
        external_id="di-child-later",
        title="Later sibling",
        parent_ticket_id=parent.id,
    )

    done_runs_before = len(
        db_session.exec(select(AgentRun).where(AgentRun.ticket_id == done_child.id)).all()
    )

    BuiltinOrchestrator(db_session).execute(parent, _profile(), auto_approve=True)
    db_session.refresh(done_child)
    db_session.refresh(sibling)

    done_runs_after = len(
        db_session.exec(select(AgentRun).where(AgentRun.ticket_id == done_child.id)).all()
    )
    assert done_runs_after == done_runs_before, (
        "a DONE child with incomplete stages must not be re-run"
    )
    sibling_runs = db_session.exec(select(AgentRun).where(AgentRun.ticket_id == sibling.id)).all()
    assert sibling_runs, (
        "the later sibling must run: the parent auto run has to advance past a "
        "done-but-incomplete first child, not pause the whole subtree on it"
    )
    assert sibling.state == TicketState.DONE, (
        "with auto_approve the later sibling should run all the way to DONE"
    )


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
    ws = _make_workspace(
        db_session, tmp_path, "auto-mode-human-label", stages=_OPERATOR_GATE_STAGES
    )
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


def test_auto_approve_does_not_resolve_a_preexisting_human_gate(db_session: Session, tmp_path):
    """AC-6: auto_approve never resolves human-required exit actions, even ones
    that were already awaiting before the auto run started.
    """
    ws = _make_workspace(db_session, tmp_path, "auto-mode-late-adopt", stages=_OPERATOR_GATE_STAGES)
    ticket = _make_ticket(db_session, ws, external_id="gate-late-adopt-1", title="Solo")

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

    BuiltinOrchestrator(db_session).execute(ticket, _profile(), auto_approve=True)
    db_session.refresh(ticket)
    db_session.refresh(pending)

    assert pending.status == ApprovalStatus.PENDING
    assert _stage_status(db_session, ticket, "signoff") == StageStatus.AWAITING
    assert ticket.state != TicketState.DONE


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

    # The child runs exactly 3 stages to DONE (work, signoff, review) under this
    # test template; the parent is an aggregator that runs none of its own, so a
    # 1-child subtree is 3 stages total, exactly.
    profile = _profile(max_subtree_stages_per_run=3)
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

    # The child needs 3 stages (work, signoff, review); the aggregator parent
    # runs none. A bound of 2 is one short of the child's required count.
    profile = _profile(max_subtree_stages_per_run=2)
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


# ---------------------------------------------------------------------------
# AC: a builtin_autopilot run renews its own lease, so the sweep cannot
# reclaim a run that is doing work
# ---------------------------------------------------------------------------


def test_builtin_run_records_a_lease_stamp(db_session: Session, tmp_path):
    """`touch_lease` was reachable only through the MCP callbacks and the
    external harness — paths this driver never crosses, because it advances its
    own stages in-process. Every builtin run therefore carried a null
    `last_seen_at` and was judged on `started_at`: permanently "expired" 30
    minutes in, and reclaimable in any gap between stages. That is how
    `orch_749069` was failed out from under a ticket whose stages all passed.
    """
    ws = _make_workspace(db_session, tmp_path, "auto-mode-lease")
    ticket = _make_ticket(db_session, ws, external_id="lease-1", title="Solo")

    orch_run = BuiltinOrchestrator(db_session).execute(ticket, _profile(), auto_approve=True)

    db_session.refresh(orch_run)
    assert orch_run.last_seen_at is not None, (
        "a builtin run must renew its own lease; without a stamp the sweep judges "
        "it on started_at and reclaims a healthy run mid-handoff"
    )


def test_a_child_whose_prerequisite_is_done_but_unlanded_is_held_and_says_so(
    db_session: Session, tmp_path
) -> None:
    """770: done is not landed. The hold names the branch the work is missing from."""
    from loregarden.services.target_branch import resolve_target_branch

    ws = _make_workspace(db_session, tmp_path, "auto-mode-unlanded")
    parent = _make_ticket(
        db_session,
        ws,
        external_id="unlanded-p",
        title="Parent",
        work_item_type=WorkItemType.FEATURE,
    )
    other_parent = _make_ticket(
        db_session, ws, external_id="unlanded-q", title="Other", work_item_type=WorkItemType.FEATURE
    )
    child = _make_ticket(
        db_session, ws, external_id="unlanded-c", title="Child", parent_ticket_id=parent.id
    )
    prerequisite = _make_ticket(
        db_session,
        ws,
        external_id="unlanded-a",
        title="Done elsewhere",
        parent_ticket_id=other_parent.id,
    )
    prerequisite.state = TicketState.DONE
    prerequisite.branch = "loregarden/unlanded-a"
    db_session.add(prerequisite)
    db_session.commit()
    repo = Path(ws.repo_path)
    other_target = resolve_target_branch(db_session, prerequisite, ws, repo_root=repo)
    subprocess.run(["git", "branch", prerequisite.branch, other_target], cwd=repo, check=True)
    tree = tmp_path / "prereq-tree"
    subprocess.run(
        ["git", "worktree", "add", "-q", str(tree), prerequisite.branch], cwd=repo, check=True
    )
    (tree / "a.txt").write_text("work nobody landed\n")
    subprocess.run(["git", "add", "-A"], cwd=tree, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "a"], cwd=tree, check=True)
    subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=repo, check=True)
    TicketDependencyService(db_session).add_dependency(child.id, prerequisite.id)
    db_session.commit()

    orch_run = BuiltinOrchestrator(db_session).execute(parent, _profile(), auto_approve=True)
    db_session.refresh(parent)

    message = orch_run.error_message or ""
    assert "unlanded-a" in message, message
    assert "not landed on integration/unlanded-p" in message, message
    assert parent.state != TicketState.DONE


def test_oversized_ticket_is_split_and_its_parts_run_to_completion(
    db_session: Session, tmp_path, monkeypatch
):
    """An oversized feature never runs its own stages: it is split before the
    first one, and the aggregator loop runs the parts."""
    criteria = [f"criterion {n}" for n in range(1, MAX_ACCEPTANCE_CRITERIA + 2)]
    parts = [criteria[:8], criteria[8:]]
    reply = json.dumps(
        {
            "children": [
                {
                    "external_id": f"part-{i}",
                    "title": f"Part {i}",
                    "work_item_type": "capability",
                    "description": f"Part {i}.",
                    "acceptance_criteria": group,
                    "priority": 2,
                    "children": [],
                }
                for i, group in enumerate(parts, start=1)
            ]
        }
    )
    monkeypatch.setenv(TICKET_SPLIT_CLI_PROFILE.stub_env, reply)
    ws = _make_workspace(db_session, tmp_path, "auto-mode-split")
    parent = _make_ticket(
        db_session,
        ws,
        external_id="split-e2e-parent",
        title="Oversized parent",
        work_item_type=WorkItemType.FEATURE,
    )
    parent.acceptance_criteria_json = serialize_criteria(criteria)
    db_session.add(parent)
    db_session.commit()

    orch_run = BuiltinOrchestrator(db_session).execute(parent, _profile(), auto_approve=True)
    db_session.refresh(parent)

    children = db_session.exec(select(Ticket).where(Ticket.parent_ticket_id == parent.id)).all()
    assert len([c for c in children if not c.is_integration_review]) == 2
    assert all(child.state == TicketState.DONE for child in children)
    assert parent.state == TicketState.DONE
    assert orch_run.status == OrchestrationRunStatus.SUCCEEDED
    assert db_session.exec(select(AgentRun).where(AgentRun.ticket_id == parent.id)).all() == []


def test_a_ticket_that_grows_oversized_after_a_stage_is_split_at_the_next_boundary(
    db_session: Session, tmp_path, monkeypatch
):
    """The criteria that make a ticket oversized are written by its early
    stages, so the split is asked again after one has run — here, with "work"
    done and nothing committed, the remaining stages are never run on the
    parent; its parts carry them."""
    criteria = [f"criterion {n}" for n in range(1, MAX_ACCEPTANCE_CRITERIA + 2)]
    reply = json.dumps(
        {
            "children": [
                {
                    "external_id": f"part-{i}",
                    "title": f"Part {i}",
                    "work_item_type": "capability",
                    "description": f"Part {i}.",
                    "acceptance_criteria": group,
                    "priority": 2,
                    "children": [],
                }
                for i, group in enumerate([criteria[:8], criteria[8:]], start=1)
            ]
        }
    )
    monkeypatch.setenv(TICKET_SPLIT_CLI_PROFILE.stub_env, reply)
    ws = _make_workspace(db_session, tmp_path, "auto-mode-late-split")
    parent = _make_ticket(
        db_session,
        ws,
        external_id="late-split-parent",
        title="Grew during spec",
        work_item_type=WorkItemType.FEATURE,
    )
    orch = OrchestrationService(db_session)
    instance, stages = orch._resolve_stages(parent)
    set_stage_status(parent, instance, stages, "work", StageStatus.DONE)
    db_session.add(instance)
    parent.acceptance_criteria_json = serialize_criteria(criteria)
    db_session.add(parent)
    db_session.commit()

    orch_run = BuiltinOrchestrator(db_session).execute(parent, _profile(), auto_approve=True)
    db_session.refresh(parent)

    children = db_session.exec(select(Ticket).where(Ticket.parent_ticket_id == parent.id)).all()
    assert len([c for c in children if not c.is_integration_review]) == 2
    assert all(child.state == TicketState.DONE for child in children)
    assert parent.state == TicketState.DONE
    assert orch_run.status == OrchestrationRunStatus.SUCCEEDED
    assert db_session.exec(select(AgentRun).where(AgentRun.ticket_id == parent.id)).all() == []


# ---------------------------------------------------------------------------
# lg-milestone-that-779: a sibling prerequisite that is done but not landed
#
# `_partition_children` dropped *every* unmet prerequisite that was a sibling,
# on the reasoning that `order_children_for_subtree` had already put it earlier
# in the list. True for NOT_DONE — ordering will run it. False for NOT_LANDED:
# it already ran, and the dependent's base still cannot see its work. Observed
# on lg-milestone-that-714, where 715 reached `done` unlanded and 716 was
# dispatched into a worktree containing none of 715's commits.
#
# AC10: `work_is_reachable` returns None when the prerequisite has no branch in
# the repository, and None is not unmet — so a fixture without a real branch
# carrying a real unlanded commit asserts nothing while reporting green. Both
# tests below assert the fixture is in the state it claims before they exercise
# the orchestrator.
# ---------------------------------------------------------------------------


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=repo,
            capture_output=True,
        ).returncode
        == 0
    )


def _unlanded_branch_with_a_commit(repo: Path, tmp_path: Path, branch: str, start: str) -> str:
    """`branch` cut from `start` with one commit on it, landed nowhere.

    The recipe the 770 test uses: a real branch, a real commit made in a
    throwaway worktree that is then removed, so the tip is reachable from the
    branch and from nothing else. Returns the tip.
    """
    subprocess.run(["git", "branch", branch, start], cwd=repo, check=True)
    tree = tmp_path / f"tree-{branch.replace('/', '-')}"
    subprocess.run(["git", "worktree", "add", "-q", str(tree), branch], cwd=repo, check=True)
    (tree / "work.txt").write_text("work nobody landed\n")
    subprocess.run(["git", "add", "-A"], cwd=tree, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "unlanded"], cwd=tree, check=True)
    tip = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tree, check=True, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=repo, check=True)
    return tip


def test_a_sibling_prerequisite_that_is_done_but_unlanded_holds_its_dependent(
    db_session: Session, tmp_path
) -> None:
    """AC1/AC9/AC10. The case that passed before 779: the prerequisite is a
    *sibling*, so the old predicate dropped it and the child ran against a base
    with none of its work. Ordering cannot help here — the sibling already ran.
    """
    from loregarden.services.target_branch import resolve_target_branch

    ws = _make_workspace(db_session, tmp_path, "auto-mode-sibling-unlanded")
    parent = _make_ticket(
        db_session,
        ws,
        external_id="sib-unlanded-p",
        title="Parent",
        work_item_type=WorkItemType.FEATURE,
    )
    child = _make_ticket(
        db_session,
        ws,
        external_id="sib-unlanded-c",
        title="Dependent child",
        parent_ticket_id=parent.id,
    )
    # The one change from the 770 fixture: the prerequisite sits under the SAME
    # parent, which is what makes it a sibling.
    prerequisite = _make_ticket(
        db_session,
        ws,
        external_id="sib-unlanded-a",
        title="Finished sibling",
        parent_ticket_id=parent.id,
    )
    prerequisite.state = TicketState.DONE
    prerequisite.branch = "loregarden/sib-unlanded-a"
    db_session.add(prerequisite)
    db_session.commit()

    repo = Path(ws.repo_path)
    target = resolve_target_branch(db_session, child, ws, repo_root=repo)
    tip = _unlanded_branch_with_a_commit(repo, tmp_path, prerequisite.branch, target)
    TicketDependencyService(db_session).add_dependency(child.id, prerequisite.id)
    db_session.commit()

    # AC10: the fixture claims "done, with work the dependent's base cannot
    # see". Without these three the test passes against any orchestrator,
    # because `work_is_reachable` would be answering None.
    assert tip, "the prerequisite's branch must have a real tip"
    assert not _is_ancestor(repo, tip, target), "the work must not be on the target branch"
    assert not _is_ancestor(repo, tip, "main"), "nor on the base branch"

    orch_run = BuiltinOrchestrator(db_session).execute(parent, _profile(), auto_approve=True)
    db_session.refresh(parent)
    db_session.refresh(child)

    message = orch_run.error_message or ""
    assert "waiting on a prerequisite" in message.lower(), message
    assert "sib-unlanded-a" in message, (
        f"the hold must name the prerequisite, not count it: {message}"
    )
    assert f"not landed on {target}" in message, (
        f"and the branch its work is missing from: {message}"
    )
    # AC6: the hold does not clear itself — landing runs only on a terminal-stage
    # transition — so the line carries the operator's next action, on the
    # prerequisite's id and not the held child's.
    assert "land it: loregarden_land_ticket sib-unlanded-a" in message, message
    assert "loregarden_land_ticket sib-unlanded-c" not in message, message
    # The whole point: the child never started against a base missing the work.
    assert db_session.exec(select(AgentRun).where(AgentRun.ticket_id == child.id)).all() == [], (
        "the held child must not be dispatched at all"
    )
    assert parent.state != TicketState.DONE


def test_a_sibling_prerequisite_that_is_merely_not_done_does_not_hold_its_dependent(
    db_session: Session, tmp_path
) -> None:
    """AC2. The regression guard for lg-workflow-integrity-449 / 676: an
    integration-review child depends on every sibling, so a blanket sibling
    hold would mean it never starts. Ordering owns this case and always did.
    """
    ws = _make_workspace(db_session, tmp_path, "auto-mode-sibling-not-done")
    parent = _make_ticket(
        db_session,
        ws,
        external_id="sib-notdone-p",
        title="Parent",
        work_item_type=WorkItemType.FEATURE,
    )
    child = _make_ticket(
        db_session,
        ws,
        external_id="sib-notdone-c",
        title="Dependent child",
        parent_ticket_id=parent.id,
    )
    prerequisite = _make_ticket(
        db_session,
        ws,
        external_id="sib-notdone-a",
        title="Unstarted sibling",
        parent_ticket_id=parent.id,
    )
    TicketDependencyService(db_session).add_dependency(child.id, prerequisite.id)
    db_session.commit()

    # AC10's other half: the fixture must really be in a non-satisfied state,
    # and must really have no branch — otherwise this asserts nothing either.
    db_session.refresh(prerequisite)
    assert prerequisite.state == TicketState.BACKLOG, prerequisite.state
    assert (
        subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", "refs/heads/loregarden/sib-notdone-a"],
            cwd=Path(ws.repo_path),
            capture_output=True,
        ).returncode
        != 0
    ), "a NOT_DONE prerequisite has no branch yet; that is what makes it NOT_DONE"

    orch_run = BuiltinOrchestrator(db_session).execute(parent, _profile(), auto_approve=True)
    db_session.refresh(child)
    db_session.refresh(prerequisite)

    assert "waiting on a prerequisite" not in (orch_run.error_message or "").lower(), (
        orch_run.error_message
    )
    child_runs = db_session.exec(select(AgentRun).where(AgentRun.ticket_id == child.id)).all()
    prereq_runs = db_session.exec(
        select(AgentRun).where(AgentRun.ticket_id == prerequisite.id)
    ).all()
    assert child_runs, "the dependent must still be dispatched"
    assert prereq_runs, "and so must the prerequisite"
    assert min(r.created_at for r in prereq_runs) < min(r.created_at for r in child_runs), (
        "ordering's job, and still ordering's job: the prerequisite runs first"
    )


# ---------------------------------------------------------------------------
# AC6: _why carries the remedy on the NOT_LANDED branch only
# ---------------------------------------------------------------------------


def test_why_names_the_landing_tool_for_an_unlanded_prerequisite() -> None:
    from loregarden.services.dependency_readiness import UnmetPrerequisite, UnmetReason
    from loregarden.services.hold_line import _why

    prerequisite = Ticket(
        external_id="lg-milestone-that-715",
        workspace_id="w",
        title="Finished, never landed",
        state=TicketState.DONE,
    )

    assert _why(
        UnmetPrerequisite(prerequisite, UnmetReason.NOT_LANDED, "integration/lg-milestone-that-491")
    ) == (
        "done, not landed on integration/lg-milestone-that-491; "
        "land it: loregarden_land_ticket lg-milestone-that-715"
    )


def test_why_is_unchanged_for_a_prerequisite_that_has_simply_not_run() -> None:
    """A NOT_DONE prerequisite needs no operator action — ordering will run it —
    so the bare state is still the whole answer."""
    from loregarden.services.dependency_readiness import UnmetPrerequisite, UnmetReason
    from loregarden.services.hold_line import _why

    prerequisite = Ticket(
        external_id="lg-milestone-that-716",
        workspace_id="w",
        title="Not started",
        state=TicketState.IN_PROGRESS,
    )

    assert _why(UnmetPrerequisite(prerequisite, UnmetReason.NOT_DONE)) == "in_progress"
    assert "loregarden_land_ticket" not in _why(
        UnmetPrerequisite(prerequisite, UnmetReason.NOT_DONE)
    )


# ---------------------------------------------------------------------------
# AC7: the parent's waiting line states its count before any names, and caps
# the names. `_finish_orchestration_run` cuts error_message at 2000 characters;
# one live parent has 93 children and 2 runs have already hit that cut, so a
# line that leads with names loses both the count and the first child.
# ---------------------------------------------------------------------------


def _parent_with_held_children(
    db_session: Session, tmp_path: Path, slug: str, count: int, *, title_pad: int = 0
):
    """A parent with `count` children, each held by a prerequisite in another
    workspace — the cheapest hold there is, and one no git fixture can make
    vacuous (a cross-workspace prerequisite is judged by state).
    """
    ws = _make_workspace(db_session, tmp_path, slug)
    upstream_ws = _make_workspace(db_session, tmp_path, f"{slug}-upstream")
    parent = _make_ticket(
        db_session, ws, external_id=f"{slug}-p", title="Parent", work_item_type=WorkItemType.FEATURE
    )
    dependencies = TicketDependencyService(db_session)
    titles = []
    for n in range(1, count + 1):
        title = f"Held child {n:02d}" + ("." * title_pad)
        titles.append(title)
        child = _make_ticket(
            db_session, ws, external_id=f"{slug}-c{n:02d}", title=title, parent_ticket_id=parent.id
        )
        upstream = _make_ticket(
            db_session, upstream_ws, external_id=f"{slug}-u{n:02d}", title=f"Upstream {n:02d}"
        )
        dependencies.add_dependency(child.id, upstream.id)
    db_session.commit()
    return parent, titles


@pytest.mark.parametrize("count", [1, 5])
def test_the_waiting_line_counts_its_children_and_names_them_all_up_to_five(
    db_session: Session, tmp_path, count: int
) -> None:
    parent, titles = _parent_with_held_children(
        db_session, tmp_path, f"auto-mode-waiting-{count}", count
    )

    orch_run = BuiltinOrchestrator(db_session).execute(parent, _profile(), auto_approve=True)

    message = orch_run.error_message or ""
    assert message.startswith(f"Child ticket waiting on a prerequisite ({count}): "), message
    assert all(title in message for title in titles), message
    assert "more" not in message.split(": ", 1)[1], (
        f"at {count} held children nothing is elided: {message}"
    )


def test_the_waiting_line_names_the_first_five_and_counts_the_rest(
    db_session: Session, tmp_path
) -> None:
    parent, titles = _parent_with_held_children(db_session, tmp_path, "auto-mode-waiting-7", 7)

    orch_run = BuiltinOrchestrator(db_session).execute(parent, _profile(), auto_approve=True)

    message = orch_run.error_message or ""
    assert message.startswith("Child ticket waiting on a prerequisite (7): "), message
    named = [title for title in titles if title in message]
    assert len(named) == 5, f"five clauses, no more: named {named} in {message}"
    assert message.endswith("; +2 more"), message


def test_the_count_and_the_first_child_survive_the_two_thousand_character_cut(
    db_session: Session, tmp_path
) -> None:
    """R5-AC5 (919). The reason the count leads — with the inherited assertion
    corrected rather than deleted.

    This test used to assert `len(message) == 2000`, which pinned the defect
    919 exists to fix: it made "the message reaches the cut" look like a
    guarantee. 779 AC7 guarantees three things — the count leads, at most five
    child names, then `+<n> more` — and reaching the cut is not one of them.
    The line is now composed against `ERROR_MESSAGE_LIMIT`, so at 500-character
    titles the budget binds before `WAITING_NAMES` does and fewer than five
    children are named. That is the cap behaving as a ceiling, not a
    regression; the short-title test above is where "five names, then +2 more"
    is proven, and it is untouched.
    """
    parent, titles = _parent_with_held_children(
        db_session, tmp_path, "auto-mode-waiting-long", 7, title_pad=500
    )

    orch_run = BuiltinOrchestrator(db_session).execute(parent, _profile(), auto_approve=True)

    message = orch_run.error_message or ""
    assert len(message) < 2000, (
        f"composed against the cut, not by it — and never reaching it: {len(message)}"
    )
    assert CUT_MARKER not in message, f"the budget, not the truncation, bounds it: {message[-80:]}"
    assert message.startswith("Child ticket waiting on a prerequisite (7): "), message
    assert titles[0] in message, f"the first child must be named whole: {message[:200]}"
    assert_plain_single_line(message)

    parsed = parse_hold_line(message)
    assert parsed.total == 7
    named = list(parsed.children)
    assert named, "at least the first child is named whole"
    assert [child.title for child in named] == titles[: len(named)], (
        f"the children named are the first ones, in order: {[c.title[:20] for c in named]}"
    )
    assert parsed.elided_children == 7 - len(named)
    assert message.endswith(f"; +{7 - len(named)} more"), message[-40:]


# ---------------------------------------------------------------------------
# lg-milestone-that-919: real volume. The live maximum is ONE dependent with 18
# done-but-unlanded prerequisites, measured read-only against the database
# 2026-10-13: fan-in per ticket peaks at 18 on exactly one ticket, then 13, 12,
# 11 (x2), 9 (x3); max external_id 29 characters, max workspace slug 10, max
# title 127.
#
# At that shape the pre-919 line measured 2000 characters: 13 prerequisites
# carried a readable remedy, the 14th ended `land it: loregarden_land_ticket
# lg-milestone-` and four were dropped with no indication at all.
#
# R6-AC2 / 779 AC10: the prerequisites must be `done` with REAL unlanded
# branches. A cross-workspace, state-only hold renders NOT_DONE clauses, which
# are ~130 characters shorter each and would not exercise the budget at all.
# ---------------------------------------------------------------------------

#: Live maxima the fixture pads to, rather than depending on the live database.
_LIVE_ID_WIDTH = 29
_LIVE_SLUG = "pq-live-ws"  # 10 characters
_LIVE_TITLE_WIDTH = 127
_LIVE_FAN_IN = 18


def _pad(stem: str, width: int, filler: str = "z") -> str:
    assert len(stem) <= width, stem
    return stem + filler * (width - len(stem))


def _unlanded_branches_with_commits(
    repo: Path, tmp_path: Path, branches: list[str], start: str
) -> dict[str, str]:
    """`branches` each cut from `start` with one commit, landed nowhere.

    The same recipe as `_unlanded_branch_with_a_commit`, through one throwaway
    worktree instead of eighteen: at this volume the add/remove pair is the
    whole cost of the test.
    """
    tree = tmp_path / "unlanded-branches"
    subprocess.run(
        ["git", "worktree", "add", "-q", "--detach", str(tree), start], cwd=repo, check=True
    )
    tips: dict[str, str] = {}
    for index, branch in enumerate(branches):
        subprocess.run(["git", "checkout", "-q", "-b", branch, start], cwd=tree, check=True)
        (tree / "work.txt").write_text(f"work nobody landed {index}\n")
        subprocess.run(["git", "add", "-A"], cwd=tree, check=True)
        subprocess.run(["git", "commit", "-q", "-m", f"unlanded {index}"], cwd=tree, check=True)
        tips[branch] = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=tree, check=True, capture_output=True, text=True
        ).stdout.strip()
    subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=repo, check=True)
    return tips


def _parent_with_one_child_held_by_unlanded_siblings(
    db_session: Session, tmp_path: Path, *, count: int
):
    """One dependent child at live widths, held by `count` `done` siblings whose
    branches carry a commit the dependent's resolved target cannot see."""
    from loregarden.services.target_branch import resolve_target_branch

    ws = _make_workspace(db_session, tmp_path, _LIVE_SLUG)
    parent = _make_ticket(
        db_session,
        ws,
        external_id="pq-live-parent",
        title="Parent",
        work_item_type=WorkItemType.FEATURE,
    )
    child = _make_ticket(
        db_session,
        ws,
        external_id=_pad("lg-milestone-that-919-dep", _LIVE_ID_WIDTH),
        title=_pad("Dependent child at the live title maximum", _LIVE_TITLE_WIDTH, "."),
        parent_ticket_id=parent.id,
    )
    repo = Path(ws.repo_path)
    target = resolve_target_branch(db_session, child, ws, repo_root=repo)

    dependencies = TicketDependencyService(db_session)
    prerequisite_ids = []
    for n in range(1, count + 1):
        external_id = _pad(f"lg-milestone-that-919-p{n:02d}", _LIVE_ID_WIDTH)
        prerequisite = _make_ticket(
            db_session,
            ws,
            external_id=external_id,
            title=_pad(f"Finished sibling {n:02d}", _LIVE_TITLE_WIDTH, "."),
            parent_ticket_id=parent.id,
        )
        prerequisite.state = TicketState.DONE
        prerequisite.branch = f"loregarden/{external_id}"
        db_session.add(prerequisite)
        dependencies.add_dependency(child.id, prerequisite.id)
        prerequisite_ids.append(external_id)
    db_session.commit()

    tips = _unlanded_branches_with_commits(
        repo, tmp_path, [f"loregarden/{pid}" for pid in prerequisite_ids], target
    )

    # 779 AC10: without these the fixture asserts nothing, because
    # `work_is_reachable` answers None for a prerequisite with no real branch
    # and None is not unmet.
    for branch, tip in tips.items():
        assert tip, branch
        assert not _is_ancestor(repo, tip, target), f"{branch} must not be on {target}"
        assert not _is_ancestor(repo, tip, "main"), f"{branch} must not be on main"

    return parent, child, prerequisite_ids, target


def test_eighteen_unlanded_prerequisites_name_at_least_ten_whole_remedies(
    db_session: Session, tmp_path
) -> None:
    """R6-AC1/R6-AC2/R6-AC3/R6-AC4, R5-AC1/R5-AC2.

    The live worst case, driven through the real `BuiltinOrchestrator.execute`.
    The line must fit the column *and* stay useful: a line that fits by naming
    two prerequisites satisfies the length bound and fails this test.
    """
    parent, child, prerequisite_ids, target = _parent_with_one_child_held_by_unlanded_siblings(
        db_session, tmp_path, count=_LIVE_FAN_IN
    )

    orch_run = BuiltinOrchestrator(db_session).execute(parent, _profile(), auto_approve=True)
    db_session.refresh(child)

    message = orch_run.error_message or ""
    # R5-AC1: bounded by the budget, and never by the cut.
    assert len(message) < ERROR_MESSAGE_LIMIT, f"{len(message)}: {message[-120:]}"
    assert CUT_MARKER not in message, message[-120:]
    assert_plain_single_line(message)
    # The held child never started against a base missing the work.
    assert db_session.exec(select(AgentRun).where(AgentRun.ticket_id == child.id)).all() == []

    parsed = parse_hold_line(message)
    assert parsed.total == 1, parsed
    assert parsed.elided_children == 0, "one held child is not elided"
    (held,) = parsed.children
    assert held.external_id == child.external_id

    # R6-AC2: at least ten complete, copyable remedies, each naming a whole id.
    named = held.prerequisites
    assert len(named) >= 10, (
        f"the live worst case must name at least ten prerequisites, named {len(named)}"
    )
    assert len({p.external_id for p in named}) == len(named), "no prerequisite named twice"
    for prerequisite in named:
        assert prerequisite.external_id in prerequisite_ids, prerequisite
        assert prerequisite.slug == _LIVE_SLUG, prerequisite
        # R6-AC4, non-vacuity: a state-only hold would render the bare state.
        assert f", not landed on {target}; " in prerequisite.why, prerequisite
        assert prerequisite.why.endswith(
            f"land it: loregarden_land_ticket {prerequisite.external_id}"
        ), prerequisite
    assert message.count("loregarden_land_ticket") == len(named), (
        "every named prerequisite carries exactly one remedy"
    )

    # R6-AC3 / R5-AC4: the elision is present, exact, and points at the
    # unelided view (R7-AC1's frozen `blocked_by_summaries`).
    elided = _LIVE_FAN_IN - len(named)
    assert elided >= 1, "18 live-width clauses cannot all fit; the elision must fire"
    assert (
        f", +{elided} more prerequisites \u2014 loregarden_get_ticket {child.external_id})"
    ) in message, message[-200:]
    assert_reconciles(parsed, total=1, prerequisite_counts=[_LIVE_FAN_IN])


def test_two_runs_over_the_same_held_subtree_produce_the_same_line(
    db_session: Session, tmp_path
) -> None:
    """R4-AC3, the orchestrator's half: two full `execute` runs over the same
    fixture produce byte-identical messages. Nothing about the line may depend
    on the order `unmet_prerequisites_for_start` happened to return entries in
    (measured on the real fixture: p03, p17, ..., p18, p16)."""
    parent, _ = _parent_with_held_children(db_session, tmp_path, "auto-mode-waiting-twice", 5)

    first = BuiltinOrchestrator(db_session).execute(parent, _profile(), auto_approve=True)
    first_message = first.error_message or ""
    second = BuiltinOrchestrator(db_session).execute(parent, _profile(), auto_approve=True)

    assert first_message
    assert first_message == (second.error_message or "")
    assert_plain_single_line(first_message)


# ---------------------------------------------------------------------------
# lg-milestone-that-779 (A), adversarial: the hold is keyed on *reachability*,
# per entry, and nothing wider.
#
# Three implementations pass both tests above and are wrong: "hold any sibling
# whose state is done", "hold any sibling at all once one of its prerequisites
# is unlanded", and a filter applied to the unmet *list* rather than to each
# entry. The two below separate them.
# ---------------------------------------------------------------------------


def _land_onto(repo: Path, tmp_path: Path, branch: str, target: str) -> None:
    """Merge `branch` into `target`, as landing does, in a throwaway worktree."""
    tree = tmp_path / f"land-{target.replace('/', '-')}"
    subprocess.run(["git", "worktree", "add", "-q", str(tree), target], cwd=repo, check=True)
    subprocess.run(["git", "merge", "--no-edit", "-q", branch], cwd=tree, check=True)
    subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=repo, check=True)


def test_a_sibling_prerequisite_whose_work_is_landed_does_not_hold_its_dependent(
    db_session: Session, tmp_path
) -> None:
    """The control AC1 needs. `done` is not what holds the child — unreachable
    work is (770) — so a sibling that finished *and* landed must hold nothing.
    Without this, "hold every sibling in `done`" satisfies AC1 and AC2 both,
    and stalls every tree whose siblings land normally.
    """
    from loregarden.services.target_branch import resolve_target_branch

    ws = _make_workspace(db_session, tmp_path, "auto-mode-sibling-landed")
    parent = _make_ticket(
        db_session,
        ws,
        external_id="sib-landed-p",
        title="Parent",
        work_item_type=WorkItemType.FEATURE,
    )
    child = _make_ticket(
        db_session, ws, external_id="sib-landed-c", title="Dependent", parent_ticket_id=parent.id
    )
    prerequisite = _make_ticket(
        db_session,
        ws,
        external_id="sib-landed-a",
        title="Landed sibling",
        parent_ticket_id=parent.id,
    )
    prerequisite.state = TicketState.DONE
    prerequisite.branch = "loregarden/sib-landed-a"
    db_session.add(prerequisite)
    db_session.commit()

    repo = Path(ws.repo_path)
    target = resolve_target_branch(db_session, child, ws, repo_root=repo)
    tip = _unlanded_branch_with_a_commit(repo, tmp_path, prerequisite.branch, target)
    _land_onto(repo, tmp_path, prerequisite.branch, target)
    TicketDependencyService(db_session).add_dependency(child.id, prerequisite.id)
    db_session.commit()

    # The mirror of AC10's trap: a prerequisite with no branch is judged by
    # state and also does not hold, so a fixture that never landed anything
    # would prove nothing here either.
    assert _is_ancestor(repo, tip, target), "fixture: the work must really be on the target"

    orch_run = BuiltinOrchestrator(db_session).execute(parent, _profile(), auto_approve=True)
    db_session.refresh(child)

    assert "waiting on a prerequisite" not in (orch_run.error_message or "").lower(), (
        orch_run.error_message
    )
    assert db_session.exec(select(AgentRun).where(AgentRun.ticket_id == child.id)).all(), (
        "a dependent whose prerequisite landed must run"
    )


def test_an_unlanded_sibling_holds_only_the_child_that_depends_on_it(
    db_session: Session, tmp_path
) -> None:
    """Two separations at once.

    Per entry: the held child also depends on a sibling that has merely not
    run, and that entry is still ordering's job — the clause must name the
    unlanded prerequisite and not the unstarted one, which a filter applied to
    the unmet list rather than to each entry gets wrong in both directions.

    Per child: the hold is one child's, and the rest of the subtree keeps
    moving (lg-workflow-integrity-449) — including the prerequisite that has
    not run, which is exactly the sibling the held child is waiting for.
    """
    from loregarden.services.target_branch import resolve_target_branch

    ws = _make_workspace(db_session, tmp_path, "auto-mode-sibling-mixed")
    parent = _make_ticket(
        db_session,
        ws,
        external_id="sib-mixed-p",
        title="Parent",
        work_item_type=WorkItemType.FEATURE,
    )
    child = _make_ticket(
        db_session, ws, external_id="sib-mixed-c", title="Dependent", parent_ticket_id=parent.id
    )
    unlanded = _make_ticket(
        db_session,
        ws,
        external_id="sib-mixed-unlanded",
        title="Finished sibling",
        parent_ticket_id=parent.id,
    )
    unstarted = _make_ticket(
        db_session,
        ws,
        external_id="sib-mixed-unstarted",
        title="Unstarted sibling",
        parent_ticket_id=parent.id,
    )
    unlanded.state = TicketState.DONE
    unlanded.branch = "loregarden/sib-mixed-unlanded"
    db_session.add(unlanded)
    db_session.commit()

    repo = Path(ws.repo_path)
    target = resolve_target_branch(db_session, child, ws, repo_root=repo)
    tip = _unlanded_branch_with_a_commit(repo, tmp_path, unlanded.branch, target)
    dependencies = TicketDependencyService(db_session)
    dependencies.add_dependency(child.id, unlanded.id)
    dependencies.add_dependency(child.id, unstarted.id)
    db_session.commit()

    assert not _is_ancestor(repo, tip, target), "fixture: the finished sibling never landed"
    assert not _is_ancestor(repo, tip, "main"), "nor reached the base"
    db_session.refresh(unstarted)
    assert unstarted.state == TicketState.BACKLOG, unstarted.state

    orch_run = BuiltinOrchestrator(db_session).execute(parent, _profile(), auto_approve=True)
    db_session.refresh(child)
    db_session.refresh(unstarted)

    message = orch_run.error_message or ""
    assert "waiting on a prerequisite" in message.lower(), message
    assert "sib-mixed-unlanded" in message, message
    assert "sib-mixed-unstarted" not in message, (
        f"a sibling that has not run is ordering's job and is not a hold: {message}"
    )
    assert db_session.exec(select(AgentRun).where(AgentRun.ticket_id == child.id)).all() == [], (
        "the child is held by the unlanded prerequisite"
    )
    assert db_session.exec(select(AgentRun).where(AgentRun.ticket_id == unstarted.id)).all(), (
        "and one child's hold must not stop the rest of the subtree"
    )


def test_the_waiting_line_elides_one_child_at_six_and_reads_the_same_twice(
    db_session: Session, tmp_path
) -> None:
    """The slope of the cap, and that it does not wander.

    N=7 pins "+2" at one point; a `+{N - 4}` or a `[:4]` is still arithmetic
    that fits it. N=6 is the first N that elides anything, so it separates
    them. Running the same unchanged parent twice pins that the five names are
    chosen deterministically — the error_message is what an operator diffs
    between ticks, and a set-ordered five would churn.
    """
    parent, titles = _parent_with_held_children(db_session, tmp_path, "auto-mode-waiting-6", 6)
    orchestrator = BuiltinOrchestrator(db_session)

    first = orchestrator.execute(parent, _profile(), auto_approve=True).error_message or ""
    second = orchestrator.execute(parent, _profile(), auto_approve=True).error_message or ""

    assert first.startswith("Child ticket waiting on a prerequisite (6): "), first
    assert first.endswith("; +1 more"), first
    assert len([title for title in titles if title in first]) == 5, first
    assert second == first, f"the same six held children must read the same:\n{first}\n{second}"
