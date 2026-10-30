"""Stage-end commit of ticket-owned dirt (lg-milestone-that-1616 / AC1–AC4, AC6, AC10).

The orchestrator commits the files land would refuse after a writing stage
passes, before the next stage is dispatched. A commit failure blocks this
stage. Ticket-triage chat is a second caller of the same helper and never
claims a next stage will commit.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from loregarden.models.domain import (
    AgentRun,
    InvariantReentry,
    OrchestrationRun,
    OrchestrationRunStatus,
    ParallelAgentSpec,
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
from loregarden.services.chat_publish import report_ticket_turn
from loregarden.services.git_automation_config import serialize_override
from loregarden.services.invariant_registry import LANDED_WORK_IS_COMMITTED
from loregarden.services.landing_uncommitted import uncommitted_at_landing
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.parallel_stage import member_result, reconcile_parallel_stage
from loregarden.services.ticket_worktree import resolve_execution_root
from loregarden.services.workflow_state import initial_stages_json, parse_stage_map
from sqlmodel import Session
from tests.worktree_helpers import blocking_text, git, make_repo

_EDIT = "VALUE = 2  # the implementation\n"
_TRACKED = "service.py"
_IMPLEMENT = "implement"
_VERIFY = "verify"
_PLAN = "plan"
_LANES = ("planner", "plan-risk")


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path: Path) -> Path:
    repo = make_repo(tmp_path)
    (repo / _TRACKED).write_text("VALUE = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "a tracked source file")
    return repo


def _workspace(session: Session, repo: Path) -> Workspace:
    ws = Workspace(
        slug=f"stage-end-{uuid4().hex[:10]}",
        name="stage-end commit",
        repo_path=str(repo),
    )
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _stages_single() -> list[WorkflowStageDef]:
    return [
        WorkflowStageDef(
            key=_IMPLEMENT,
            name="Implement",
            order=1,
            stage_type="agent",
            agent_id="backend_implementer",
        ),
        WorkflowStageDef(
            key=_VERIFY, name="Verify", order=2, stage_type="agent", agent_id="verifier"
        ),
        WorkflowStageDef(key="done", name="Done", order=3, terminal=True, stage_type="agent"),
    ]


def _stages_parallel() -> list[WorkflowStageDef]:
    return [
        WorkflowStageDef(
            key=_PLAN,
            name="Plan",
            order=1,
            stage_type="parallel",
            parallel_agents=[ParallelAgentSpec(agent_id=agent) for agent in _LANES],
        ),
        WorkflowStageDef(
            key=_IMPLEMENT,
            name="Implement",
            order=2,
            stage_type="agent",
            agent_id="backend_implementer",
        ),
        WorkflowStageDef(key="done", name="Done", order=3, terminal=True, stage_type="agent"),
    ]


def _bind_workflow(
    session: Session,
    ticket: Ticket,
    stages: list[WorkflowStageDef],
    *,
    current: str,
    transitions: list[dict[str, str]],
) -> None:
    template = WorkflowTemplate(
        slug=f"stage-end-tpl-{uuid4().hex[:8]}",
        name="stage-end",
        stages_json=json.dumps([s.model_dump(mode="json") for s in stages]),
        transitions_json=json.dumps(transitions),
    )
    session.add(template)
    session.commit()
    session.refresh(template)
    session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key=current,
            stages_json=initial_stages_json(stages),
        )
    )
    session.commit()


def _ticket(
    session: Session,
    workspace: Workspace,
    *,
    commit: bool,
    stage_key: str,
    stages: list[WorkflowStageDef],
    transitions: list[dict[str, str]],
) -> Ticket:
    ticket = Ticket(
        external_id=f"lg-se-{uuid4().hex[:8]}",
        workspace_id=workspace.id,
        title="Implement the thing",
        branch=f"loregarden/lg-se-{uuid4().hex[:8]}",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key=stage_key,
        workflow_stage_status=StageStatus.RUNNING,
        git_automation_json=serialize_override({"commit": commit}),
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    _bind_workflow(session, ticket, stages, current=stage_key, transitions=transitions)
    return ticket


def _orch(session: Session, ticket: Ticket) -> OrchestrationRun:
    parent = OrchestrationRun(
        run_code=f"orch-{uuid4().hex[:8]}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        status=OrchestrationRunStatus.RUNNING,
        current_stage_key=ticket.workflow_stage_key,
        # Off so a commit failure stays StageStatus.BLOCKED (AC1) instead of
        # being re-armed PENDING under the repair agent.
        auto_repair=False,
    )
    session.add(parent)
    session.commit()
    session.refresh(parent)
    return parent


def _cut_tree(
    session: Session,
    workspace: Workspace,
    ticket: Ticket,
    *,
    stage_key: str,
    agent_id: str = "backend_implementer",
    status: RunStatus = RunStatus.RUNNING,
) -> tuple[Path, AgentRun]:
    parent = _orch(session, ticket)
    run = AgentRun(
        run_code=f"r-{uuid4().hex[:8]}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id=agent_id,
        stage_key=stage_key,
        status=status,
        orchestration_run_id=parent.id,
        changed_paths_json="[]",
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    root = resolve_execution_root(session, run, ticket, workspace)
    session.refresh(run)
    return root, run


def _dirty(root: Path) -> None:
    (root / _TRACKED).write_text(_EDIT)


def _head_tracked(root: Path) -> str:
    return git(root, "show", "HEAD:" + _TRACKED).stdout


def _report(status: str) -> str:
    body = json.dumps({"status": status, "confidence": 0.9})
    return f"Narrative.\n<<<LOREGARDEN_STAGE_REPORT>>>\n{body}\n<<<END_STAGE_REPORT>>>\n"


def _complete(
    session: Session,
    run: AgentRun,
    *,
    stdout: str,
    advance_workflow: bool = True,
) -> AgentRun:
    return OrchestrationService(session).complete_run(
        run,
        status=RunStatus.SUCCEEDED,
        stdout=stdout,
        stderr="",
        advance_workflow=advance_workflow,
    )


def _refuse_commit(repo: Path) -> None:
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'refused by the workspace gate' >&2\nexit 1\n")
    hook.chmod(0o755)


def _stage_map(session: Session, ticket: Ticket) -> dict[str, StageStatus]:
    instance, stages = OrchestrationService(session)._resolve_stages(ticket)
    assert instance is not None and stages is not None
    return parse_stage_map(instance, stages)


def test_succeeded_writing_stage_is_on_head_before_next_stage(session, repo: Path):
    """AC3 / R2.1 — prevention pointer. Reverting the `_advance_clean_exit` call turns this red.

    Implement-shaped single-agent stage on a ticket worktree; tracked production
    file already committed then edited; changed_paths_json is []; the agent does
    not git commit. GitAutomationConfig(commit=True) is armed on the ticket, not
    inherited from a yaml slug.
    """
    workspace = _workspace(session, repo)
    ticket = _ticket(
        session,
        workspace,
        commit=True,
        stage_key=_IMPLEMENT,
        stages=_stages_single(),
        transitions=[
            {"from": _IMPLEMENT, "to": _VERIFY, "when": "pass"},
            {"from": _VERIFY, "to": "done", "when": "pass"},
        ],
    )
    tree, run = _cut_tree(session, workspace, ticket, stage_key=_IMPLEMENT)
    _dirty(tree)

    finished = _complete(session, run, stdout=_report("pass"))

    session.refresh(ticket)
    session.refresh(finished)
    assert finished.status is RunStatus.SUCCEEDED
    assert _head_tracked(tree) == _EDIT
    assert uncommitted_at_landing(session, ticket, workspace) == ()
    assert git(tree, "status", "--porcelain").stdout == ""
    # complete_run marks this stage DONE; the driver then starts the next one.
    # Cursor stays on implement until that dispatch (or is PENDING on a terminal).
    assert _stage_map(session, ticket)[_IMPLEMENT] is StageStatus.DONE
    assert ticket.workflow_stage_status is not StageStatus.BLOCKED
    assert ticket.workflow_stage_key in {_IMPLEMENT, _VERIFY, "done"}


def test_commit_failure_blocks_this_stage_not_the_next(session, repo: Path):
    """AC1 / R2.2 / AC10: git ValueError blocks this stage; run stays SUCCEEDED."""
    workspace = _workspace(session, repo)
    ticket = _ticket(
        session,
        workspace,
        commit=True,
        stage_key=_IMPLEMENT,
        stages=_stages_single(),
        transitions=[
            {"from": _IMPLEMENT, "to": _VERIFY, "when": "pass"},
            {"from": _VERIFY, "to": "done", "when": "pass"},
        ],
    )
    tree, run = _cut_tree(session, workspace, ticket, stage_key=_IMPLEMENT)
    _dirty(tree)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'refused by the workspace gate' >&2\nexit 1\n")
    hook.chmod(0o755)

    finished = _complete(session, run, stdout=_report("pass"))

    session.refresh(ticket)
    session.refresh(finished)
    assert finished.status is RunStatus.SUCCEEDED
    assert ticket.workflow_stage_key == _IMPLEMENT
    assert _stage_map(session, ticket)[_IMPLEMENT] is StageStatus.BLOCKED
    assert ticket.workflow_stage_status is StageStatus.BLOCKED
    issues = blocking_text(session, ticket)
    assert "refused by the workspace gate" in issues
    assert _head_tracked(tree) == "VALUE = 1\n"


def test_commit_off_leaves_the_file_dirty(session, repo: Path):
    """R2.3: GitAutomationConfig(commit=False) → file dirty, stage still advances."""
    workspace = _workspace(session, repo)
    ticket = _ticket(
        session,
        workspace,
        commit=False,
        stage_key=_IMPLEMENT,
        stages=_stages_single(),
        transitions=[
            {"from": _IMPLEMENT, "to": _VERIFY, "when": "pass"},
            {"from": _VERIFY, "to": "done", "when": "pass"},
        ],
    )
    tree, run = _cut_tree(session, workspace, ticket, stage_key=_IMPLEMENT)
    _dirty(tree)

    _complete(session, run, stdout=_report("pass"))

    session.refresh(ticket)
    assert (tree / _TRACKED).read_text() == _EDIT
    assert _head_tracked(tree) == "VALUE = 1\n"
    assert uncommitted_at_landing(session, ticket, workspace) == (_TRACKED,)
    assert _stage_map(session, ticket)[_IMPLEMENT] is StageStatus.DONE
    assert ticket.workflow_stage_status is not StageStatus.BLOCKED


def test_fail_report_does_not_commit(session, repo: Path):
    """R2.4: fail/needs_rework sentinel with status=SUCCEEDED → HEAD unchanged."""
    workspace = _workspace(session, repo)
    ticket = _ticket(
        session,
        workspace,
        commit=True,
        stage_key=_IMPLEMENT,
        stages=_stages_single(),
        transitions=[
            {"from": _IMPLEMENT, "to": _VERIFY, "when": "pass"},
            {"from": _VERIFY, "to": "done", "when": "pass"},
        ],
    )
    tree, run = _cut_tree(session, workspace, ticket, stage_key=_IMPLEMENT)
    _dirty(tree)
    fail_body = json.dumps(
        {
            "status": "fail",
            "confidence": 0.9,
            "unmet_criteria": ["AC1"],
            "reroute_to_stage": _IMPLEMENT,
            "reroute_context": "the implementation does not match the spec",
        }
    )

    finished = _complete(
        session,
        run,
        stdout=(
            f"Narrative.\n<<<LOREGARDEN_STAGE_REPORT>>>\n{fail_body}\n<<<END_STAGE_REPORT>>>\n"
        ),
    )

    session.refresh(ticket)
    session.refresh(finished)
    assert finished.status is RunStatus.SUCCEEDED
    assert _head_tracked(tree) == "VALUE = 1\n"
    assert (tree / _TRACKED).read_text() == _EDIT
    assert _stage_map(session, ticket)[_IMPLEMENT] is not StageStatus.DONE


def test_first_parallel_member_does_not_commit(session, repo: Path):
    """R3.1: complete_run(..., advance_workflow=False) while a sibling is RUNNING → HEAD unchanged."""
    workspace = _workspace(session, repo)
    ticket = _ticket(
        session,
        workspace,
        commit=True,
        stage_key=_PLAN,
        stages=_stages_parallel(),
        transitions=[
            {"from": _PLAN, "to": _IMPLEMENT, "when": "pass"},
            {"from": _PLAN, "to": _IMPLEMENT, "when": "reject"},
        ],
    )
    tree, first = _cut_tree(session, workspace, ticket, stage_key=_PLAN, agent_id=_LANES[0])
    sibling = AgentRun(
        run_code=f"r-sib-{uuid4().hex[:8]}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id=_LANES[1],
        stage_key=_PLAN,
        status=RunStatus.RUNNING,
        worktree_id=first.worktree_id,
        orchestration_run_id=first.orchestration_run_id,
        changed_paths_json="[]",
    )
    session.add(sibling)
    session.commit()
    _dirty(tree)

    _complete(session, first, stdout=_report("pass"), advance_workflow=False)

    session.refresh(ticket)
    assert _head_tracked(tree) == "VALUE = 1\n"
    assert (tree / _TRACKED).read_text() == _EDIT
    assert ticket.workflow_stage_key == _PLAN


def test_parallel_all_pass_commits_before_done(session, repo: Path):
    """R3.2 / AC10: reconcile_parallel_stage all-pass commits before finalize_stage(DONE)."""
    workspace = _workspace(session, repo)
    ticket = _ticket(
        session,
        workspace,
        commit=True,
        stage_key=_PLAN,
        stages=_stages_parallel(),
        transitions=[
            {"from": _PLAN, "to": _IMPLEMENT, "when": "pass"},
            {"from": _PLAN, "to": _IMPLEMENT, "when": "reject"},
        ],
    )
    tree, cutter = _cut_tree(session, workspace, ticket, stage_key=_PLAN, agent_id=_LANES[0])
    cutter.status = RunStatus.SUCCEEDED
    cutter.stdout = _report("pass")
    session.add(cutter)
    session.commit()
    parent = session.get(OrchestrationRun, cutter.orchestration_run_id)
    assert parent is not None
    for agent in _LANES:
        if agent == cutter.agent_id:
            continue
        member = AgentRun(
            run_code=f"r-{agent}-{uuid4().hex[:6]}",
            ticket_id=ticket.id,
            workspace_id=workspace.id,
            agent_id=agent,
            stage_key=_PLAN,
            status=RunStatus.SUCCEEDED,
            stdout=_report("pass"),
            worktree_id=cutter.worktree_id,
            orchestration_run_id=parent.id,
            changed_paths_json="[]",
        )
        session.add(member)
    session.commit()
    _dirty(tree)
    dirt_at_done: list[tuple[str, ...]] = []
    real_finalize = OrchestrationService.finalize_stage

    def _finalize(self, ticket_arg, stage_key, *, status, blocking_message=""):
        if status is StageStatus.DONE:
            dirt_at_done.append(uncommitted_at_landing(session, ticket_arg, workspace))
        return real_finalize(
            self, ticket_arg, stage_key, status=status, blocking_message=blocking_message
        )

    results = [
        member_result(agent, status=RunStatus.SUCCEEDED, stdout=_report("pass"), stderr="")
        for agent in _LANES
    ]
    with patch.object(OrchestrationService, "finalize_stage", _finalize):
        ok, message = reconcile_parallel_stage(session, ticket, parent, _PLAN, results)

    session.refresh(ticket)
    assert ok is True
    assert message == ""
    assert dirt_at_done == [()], "commit must land before finalize_stage(DONE)"
    assert _head_tracked(tree) == _EDIT
    assert uncommitted_at_landing(session, ticket, workspace) == ()
    assert _stage_map(session, ticket)[_PLAN] is StageStatus.DONE


def test_report_ticket_turn_never_says_next_stage_will_commit(session, repo: Path):
    """AC6 / R4.1: dirty ticket-triage never prints that a next stage will commit."""
    workspace = _workspace(session, repo)
    ticket = _ticket(
        session,
        workspace,
        commit=True,
        stage_key=_IMPLEMENT,
        stages=_stages_single(),
        transitions=[
            {"from": _IMPLEMENT, "to": _VERIFY, "when": "pass"},
            {"from": _VERIFY, "to": "done", "when": "pass"},
        ],
    )
    tree, cutter = _cut_tree(session, workspace, ticket, stage_key=_IMPLEMENT)
    cutter.status = RunStatus.SUCCEEDED
    session.add(cutter)
    session.commit()
    triage = AgentRun(
        run_code=f"r-triage-{uuid4().hex[:8]}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="ticket_scoper",
        stage_key="ticket-triage",
        status=RunStatus.RUNNING,
        worktree_id=cutter.worktree_id,
        changed_paths_json="[]",
    )
    session.add(triage)
    session.commit()
    session.refresh(triage)
    _dirty(tree)

    with patch("loregarden.services.chat_publish.publish_run") as publish:
        on_outcome = report_ticket_turn(session, triage.id, ticket, workspace)
    publish.assert_not_called()
    assert on_outcome is not None
    assert on_outcome.deferred_to == ""
    assert "next stage" not in on_outcome.summary()

    ticket.git_automation_json = serialize_override({"commit": False})
    session.add(ticket)
    session.commit()
    # First half committed `_EDIT`; rewrite so the tree is dirty again under commit-off.
    (tree / _TRACKED).write_text("VALUE = 3  # after commit-off\n")
    off_outcome = report_ticket_turn(session, triage.id, ticket, workspace)
    assert off_outcome is not None
    assert off_outcome.deferred_to == ""
    assert "next stage" not in off_outcome.summary()
    assert "This workspace's git automation has `commit` off" in off_outcome.summary()


def test_report_ticket_turn_commits_through_the_helper(session, repo: Path):
    """R4.2: dirty ticket worktree, commit on, no other RUNNING writer → HEAD has the file."""
    workspace = _workspace(session, repo)
    ticket = _ticket(
        session,
        workspace,
        commit=True,
        stage_key=_IMPLEMENT,
        stages=_stages_single(),
        transitions=[
            {"from": _IMPLEMENT, "to": _VERIFY, "when": "pass"},
            {"from": _VERIFY, "to": "done", "when": "pass"},
        ],
    )
    tree, cutter = _cut_tree(session, workspace, ticket, stage_key=_IMPLEMENT)
    cutter.status = RunStatus.SUCCEEDED
    session.add(cutter)
    session.commit()
    triage = AgentRun(
        run_code=f"r-triage-{uuid4().hex[:8]}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="ticket_scoper",
        stage_key="ticket-triage",
        status=RunStatus.RUNNING,
        worktree_id=cutter.worktree_id,
        changed_paths_json="[]",
    )
    session.add(triage)
    session.commit()
    session.refresh(triage)
    _dirty(tree)

    with patch("loregarden.services.chat_publish.publish_run") as publish:
        outcome = report_ticket_turn(session, triage.id, ticket, workspace)
    publish.assert_not_called()

    assert outcome is not None
    assert outcome.deferred_to == ""
    assert "**Committed to" in outcome.summary()
    assert "Not pushed." in outcome.summary()
    assert _head_tracked(tree) == _EDIT
    assert uncommitted_at_landing(session, ticket, workspace) == ()


def test_report_ticket_turn_live_writer_uses_stopped_at_commit_sentence(session, repo: Path):
    """R4.3: a live RUNNING implement on that worktree is not git-add'd by triage."""
    workspace = _workspace(session, repo)
    ticket = _ticket(
        session,
        workspace,
        commit=True,
        stage_key=_IMPLEMENT,
        stages=_stages_single(),
        transitions=[
            {"from": _IMPLEMENT, "to": _VERIFY, "when": "pass"},
            {"from": _VERIFY, "to": "done", "when": "pass"},
        ],
    )
    tree, implement = _cut_tree(session, workspace, ticket, stage_key=_IMPLEMENT)
    implement.status = RunStatus.RUNNING
    session.add(implement)
    session.commit()
    triage = AgentRun(
        run_code=f"r-triage-{uuid4().hex[:8]}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="ticket_scoper",
        stage_key="ticket-triage",
        status=RunStatus.RUNNING,
        worktree_id=implement.worktree_id,
        changed_paths_json="[]",
    )
    session.add(triage)
    session.commit()
    session.refresh(triage)
    _dirty(tree)

    outcome = report_ticket_turn(session, triage.id, ticket, workspace)

    assert outcome is not None
    assert outcome.deferred_to == ""
    summary = outcome.summary()
    assert "publishing stopped at `commit`" in summary
    assert "another agent run is still writing this worktree" in summary
    assert _head_tracked(tree) == "VALUE = 1\n"
    assert (tree / _TRACKED).read_text() == _EDIT


def test_needs_rework_report_does_not_commit(session, repo: Path):
    """R2.4: needs_rework sentinel with status=SUCCEEDED never reaches the pass hook."""
    workspace = _workspace(session, repo)
    ticket = _ticket(
        session,
        workspace,
        commit=True,
        stage_key=_IMPLEMENT,
        stages=_stages_single(),
        transitions=[
            {"from": _IMPLEMENT, "to": _VERIFY, "when": "pass"},
            {"from": _VERIFY, "to": "done", "when": "pass"},
        ],
    )
    tree, run = _cut_tree(session, workspace, ticket, stage_key=_IMPLEMENT)
    _dirty(tree)
    body = json.dumps(
        {
            "status": "needs_rework",
            "confidence": 0.9,
            "unmet_criteria": ["AC1"],
            "reroute_to_stage": _IMPLEMENT,
            "reroute_context": "the implementation does not match the spec",
        }
    )

    finished = _complete(
        session,
        run,
        stdout=f"Narrative.\n<<<LOREGARDEN_STAGE_REPORT>>>\n{body}\n<<<END_STAGE_REPORT>>>\n",
    )

    session.refresh(ticket)
    session.refresh(finished)
    assert finished.status is RunStatus.SUCCEEDED
    assert _head_tracked(tree) == "VALUE = 1\n"
    assert (tree / _TRACKED).read_text() == _EDIT
    assert _stage_map(session, ticket)[_IMPLEMENT] is not StageStatus.DONE


def test_blocked_report_does_not_commit(session, repo: Path):
    """R2: blocked never reaches `_advance_clean_exit`, so dirt stays uncommitted."""
    workspace = _workspace(session, repo)
    ticket = _ticket(
        session,
        workspace,
        commit=True,
        stage_key=_IMPLEMENT,
        stages=_stages_single(),
        transitions=[
            {"from": _IMPLEMENT, "to": _VERIFY, "when": "pass"},
            {"from": _VERIFY, "to": "done", "when": "pass"},
        ],
    )
    tree, run = _cut_tree(session, workspace, ticket, stage_key=_IMPLEMENT)
    _dirty(tree)
    body = json.dumps(
        {
            "status": "blocked",
            "confidence": 0.9,
            "blocked_kind": "work",
            "reroute_context": "cannot proceed",
        }
    )

    finished = _complete(
        session,
        run,
        stdout=f"Narrative.\n<<<LOREGARDEN_STAGE_REPORT>>>\n{body}\n<<<END_STAGE_REPORT>>>\n",
    )

    session.refresh(finished)
    assert finished.status is RunStatus.SUCCEEDED
    assert _head_tracked(tree) == "VALUE = 1\n"
    assert (tree / _TRACKED).read_text() == _EDIT


def test_missing_report_does_not_commit(session, repo: Path):
    """R2: hook is after the missing-report return. No sentinel → no commit."""
    workspace = _workspace(session, repo)
    ticket = _ticket(
        session,
        workspace,
        commit=True,
        stage_key=_IMPLEMENT,
        stages=_stages_single(),
        transitions=[
            {"from": _IMPLEMENT, "to": _VERIFY, "when": "pass"},
            {"from": _VERIFY, "to": "done", "when": "pass"},
        ],
    )
    tree, run = _cut_tree(session, workspace, ticket, stage_key=_IMPLEMENT)
    _dirty(tree)

    finished = _complete(session, run, stdout="Narrative without a stage report.\n")

    session.refresh(ticket)
    session.refresh(finished)
    assert finished.status is RunStatus.SUCCEEDED
    assert _head_tracked(tree) == "VALUE = 1\n"
    assert (tree / _TRACKED).read_text() == _EDIT
    assert _stage_map(session, ticket)[_IMPLEMENT] is StageStatus.BLOCKED


def test_failed_complete_run_does_not_commit(session, repo: Path):
    """FAILED never reaches `_advance_clean_exit` even when the tree is dirty and commit is on."""
    workspace = _workspace(session, repo)
    ticket = _ticket(
        session,
        workspace,
        commit=True,
        stage_key=_IMPLEMENT,
        stages=_stages_single(),
        transitions=[
            {"from": _IMPLEMENT, "to": _VERIFY, "when": "pass"},
            {"from": _VERIFY, "to": "done", "when": "pass"},
        ],
    )
    tree, run = _cut_tree(session, workspace, ticket, stage_key=_IMPLEMENT)
    _dirty(tree)

    finished = OrchestrationService(session).complete_run(
        run,
        status=RunStatus.FAILED,
        stdout="",
        stderr="the harness crashed",
        advance_workflow=True,
    )

    session.refresh(finished)
    assert finished.status is RunStatus.FAILED
    assert _head_tracked(tree) == "VALUE = 1\n"
    assert (tree / _TRACKED).read_text() == _EDIT


def test_parallel_commit_failure_blocks_this_stage_returns_ok(session, repo: Path):
    """AC10: parallel git error finalizes BLOCKED, settle_block, returns (True, '')."""
    workspace = _workspace(session, repo)
    ticket = _ticket(
        session,
        workspace,
        commit=True,
        stage_key=_PLAN,
        stages=_stages_parallel(),
        transitions=[
            {"from": _PLAN, "to": _IMPLEMENT, "when": "pass"},
            {"from": _PLAN, "to": _IMPLEMENT, "when": "reject"},
        ],
    )
    tree, cutter = _cut_tree(session, workspace, ticket, stage_key=_PLAN, agent_id=_LANES[0])
    cutter.status = RunStatus.SUCCEEDED
    cutter.stdout = _report("pass")
    session.add(cutter)
    session.commit()
    parent = session.get(OrchestrationRun, cutter.orchestration_run_id)
    assert parent is not None
    for agent in _LANES:
        if agent == cutter.agent_id:
            continue
        session.add(
            AgentRun(
                run_code=f"r-{agent}-{uuid4().hex[:6]}",
                ticket_id=ticket.id,
                workspace_id=workspace.id,
                agent_id=agent,
                stage_key=_PLAN,
                status=RunStatus.SUCCEEDED,
                stdout=_report("pass"),
                worktree_id=cutter.worktree_id,
                orchestration_run_id=parent.id,
                changed_paths_json="[]",
            )
        )
    session.commit()
    _dirty(tree)
    _refuse_commit(repo)
    finalized: list[StageStatus] = []
    real_finalize = OrchestrationService.finalize_stage

    def _finalize(self, ticket_arg, stage_key, *, status, blocking_message=""):
        finalized.append(status)
        return real_finalize(
            self, ticket_arg, stage_key, status=status, blocking_message=blocking_message
        )

    results = [
        member_result(agent, status=RunStatus.SUCCEEDED, stdout=_report("pass"), stderr="")
        for agent in _LANES
    ]
    with patch.object(OrchestrationService, "finalize_stage", _finalize):
        ok, message = reconcile_parallel_stage(session, ticket, parent, _PLAN, results)

    session.refresh(ticket)
    assert ok is True
    assert message == ""
    assert StageStatus.BLOCKED in finalized
    assert StageStatus.DONE not in finalized
    assert _stage_map(session, ticket)[_PLAN] is StageStatus.BLOCKED
    assert "refused by the workspace gate" in blocking_text(session, ticket)
    assert _head_tracked(tree) == "VALUE = 1\n"


def test_terminal_writing_stage_commits_then_stays_pending(session, repo: Path):
    """R2: skip/success includes terminal PENDING-for-land. Commit still happens."""
    stages = [
        WorkflowStageDef(
            key="done",
            name="Done",
            order=1,
            terminal=True,
            stage_type="agent",
            agent_id="backend_implementer",
        ),
    ]
    workspace = _workspace(session, repo)
    ticket = _ticket(
        session,
        workspace,
        commit=True,
        stage_key="done",
        stages=stages,
        transitions=[],
    )
    tree, run = _cut_tree(session, workspace, ticket, stage_key="done")
    _dirty(tree)

    finished = _complete(session, run, stdout=_report("pass"))

    session.refresh(ticket)
    session.refresh(finished)
    assert finished.status is RunStatus.SUCCEEDED
    assert _head_tracked(tree) == _EDIT
    assert uncommitted_at_landing(session, ticket, workspace) == ()
    assert _stage_map(session, ticket)["done"] is StageStatus.PENDING
    assert ticket.workflow_stage_status is not StageStatus.BLOCKED


def test_report_ticket_turn_commit_off_does_not_revert(session, repo: Path):
    """AC6: commit off leaves dirty files; do not roll them back."""
    workspace = _workspace(session, repo)
    ticket = _ticket(
        session,
        workspace,
        commit=False,
        stage_key=_IMPLEMENT,
        stages=_stages_single(),
        transitions=[
            {"from": _IMPLEMENT, "to": _VERIFY, "when": "pass"},
            {"from": _VERIFY, "to": "done", "when": "pass"},
        ],
    )
    tree, cutter = _cut_tree(session, workspace, ticket, stage_key=_IMPLEMENT)
    cutter.status = RunStatus.SUCCEEDED
    session.add(cutter)
    session.commit()
    triage = AgentRun(
        run_code=f"r-triage-{uuid4().hex[:8]}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="ticket_scoper",
        stage_key="ticket-triage",
        status=RunStatus.RUNNING,
        worktree_id=cutter.worktree_id,
        changed_paths_json="[]",
    )
    session.add(triage)
    session.commit()
    session.refresh(triage)
    _dirty(tree)

    outcome = report_ticket_turn(session, triage.id, ticket, workspace)

    assert outcome is not None
    assert outcome.deferred_to == ""
    assert "This workspace's git automation has `commit` off" in outcome.summary()
    assert (tree / _TRACKED).read_text() == _EDIT
    assert _head_tracked(tree) == "VALUE = 1\n"
    assert uncommitted_at_landing(session, ticket, workspace) == (_TRACKED,)


def test_report_ticket_turn_commit_error_uses_stopped_at_commit_sentence(session, repo: Path):
    """AC6: helper error maps to the existing stopped-at-commit sentence, not deferred_to."""
    workspace = _workspace(session, repo)
    ticket = _ticket(
        session,
        workspace,
        commit=True,
        stage_key=_IMPLEMENT,
        stages=_stages_single(),
        transitions=[
            {"from": _IMPLEMENT, "to": _VERIFY, "when": "pass"},
            {"from": _VERIFY, "to": "done", "when": "pass"},
        ],
    )
    tree, cutter = _cut_tree(session, workspace, ticket, stage_key=_IMPLEMENT)
    cutter.status = RunStatus.SUCCEEDED
    session.add(cutter)
    session.commit()
    triage = AgentRun(
        run_code=f"r-triage-{uuid4().hex[:8]}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="ticket_scoper",
        stage_key="ticket-triage",
        status=RunStatus.RUNNING,
        worktree_id=cutter.worktree_id,
        changed_paths_json="[]",
    )
    session.add(triage)
    session.commit()
    session.refresh(triage)
    _dirty(tree)
    _refuse_commit(repo)

    with patch("loregarden.services.chat_publish.publish_run") as publish:
        outcome = report_ticket_turn(session, triage.id, ticket, workspace)
    publish.assert_not_called()

    assert outcome is not None
    assert outcome.deferred_to == ""
    summary = outcome.summary()
    assert "publishing stopped at `commit`" in summary
    assert "refused by the workspace gate" in summary
    assert "next stage" not in summary
    assert _head_tracked(tree) == "VALUE = 1\n"
    assert (tree / _TRACKED).read_text() == _EDIT


def test_report_ticket_turn_shared_checkout_uses_commit_off_sentence(session, repo: Path):
    """AC6 mapping: not_ticket_worktree → existing commit-off sentence (spec give-up)."""
    workspace = _workspace(session, repo)
    ticket = _ticket(
        session,
        workspace,
        commit=True,
        stage_key=_IMPLEMENT,
        stages=_stages_single(),
        transitions=[
            {"from": _IMPLEMENT, "to": _VERIFY, "when": "pass"},
            {"from": _VERIFY, "to": "done", "when": "pass"},
        ],
    )
    triage = AgentRun(
        run_code=f"r-triage-{uuid4().hex[:8]}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="ticket_scoper",
        stage_key="ticket-triage",
        status=RunStatus.RUNNING,
        changed_paths_json="[]",
    )
    session.add(triage)
    session.commit()
    session.refresh(triage)
    (repo / _TRACKED).write_text(_EDIT)

    outcome = report_ticket_turn(session, triage.id, ticket, workspace)

    assert outcome is not None
    assert outcome.deferred_to == ""
    assert "This workspace's git automation has `commit` off" in outcome.summary()
    assert "next stage" not in outcome.summary()
    assert _head_tracked(repo) == "VALUE = 1\n"
    assert (repo / _TRACKED).read_text() == _EDIT


def test_landed_work_is_committed_prevented_by_is_the_stage_end_hook():
    """R5.2 / R5.3 / AC8: retarget prevented_by; stay report-only; do not widen it."""
    assert LANDED_WORK_IS_COMMITTED.prevented_by == (
        "server/tests/test_stage_end_commit.py"
        "::test_succeeded_writing_stage_is_on_head_before_next_stage"
    )
    assert LANDED_WORK_IS_COMMITTED.repair is None
    assert LANDED_WORK_IS_COMMITTED.reentry is InvariantReentry.DEFECT
    assert "commit them" in LANDED_WORK_IS_COMMITTED.unrepaired_action.lower()
    assert "land" in LANDED_WORK_IS_COMMITTED.unrepaired_action.lower()
