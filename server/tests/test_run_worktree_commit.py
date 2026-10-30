"""Seam tests for `commit_succeeded_run_worktree` (lg-milestone-that-1616 / R1, AC4, AC10).

A real git repo and a session; no `OrchestrationService`. The helper commits
the files land would refuse, or explains why it did not.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from loregarden.models.domain import (
    AgentRun,
    RunStatus,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services.git_automation_config import serialize_override
from loregarden.services.landing_uncommitted import uncommitted_at_landing
from loregarden.services.run_worktree_commit import (
    WorktreeCommitSkip,
    commit_succeeded_run_worktree,
)
from loregarden.services.ticket_worktree import resolve_execution_root
from sqlmodel import Session
from tests.worktree_helpers import git, make_repo

_EDIT = "VALUE = 2  # the implementation\n"
_TRACKED = "service.py"


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
    # A slug that is not loregarden/blobert: yaml inheritance must not arm commit.
    ws = Workspace(
        slug=f"wt-commit-{uuid4().hex[:10]}",
        name="stage-end commit",
        repo_path=str(repo),
    )
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _ticket(session: Session, workspace: Workspace, *, commit: bool) -> Ticket:
    ticket = Ticket(
        external_id=f"lg-wt-{uuid4().hex[:8]}",
        workspace_id=workspace.id,
        title="Implement the thing",
        branch=f"loregarden/lg-wt-{uuid4().hex[:8]}",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="implement",
        git_automation_json=serialize_override({"commit": commit}),
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _cut_tree(
    session: Session,
    workspace: Workspace,
    ticket: Ticket,
    *,
    status: RunStatus = RunStatus.SUCCEEDED,
    stage_key: str = "implement",
) -> tuple[Path, AgentRun]:
    run = AgentRun(
        run_code=f"r-{uuid4().hex[:8]}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        stage_key=stage_key,
        status=status,
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


def _subject(root: Path) -> str:
    return git(root, "log", "-1", "--format=%s").stdout.strip()


def test_commit_on_and_dirty_tracked_file_lands_on_head(session, repo: Path):
    """R1.2 / AC4: commit on + dirty tracked file → HEAD has it, skipped=none."""
    workspace = _workspace(session, repo)
    ticket = _ticket(session, workspace, commit=True)
    tree, run = _cut_tree(session, workspace, ticket)
    run.status = RunStatus.SUCCEEDED
    session.add(run)
    session.commit()
    _dirty(tree)

    result = commit_succeeded_run_worktree(session, ticket, run)

    assert result.attempted is True
    assert result.committed is True
    assert result.error == ""
    assert result.skipped is WorktreeCommitSkip.NONE
    assert _TRACKED in result.paths
    assert _head_tracked(tree) == _EDIT
    assert uncommitted_at_landing(session, ticket, workspace) == ()
    assert _subject(tree) == f"{ticket.external_id}: {run.stage_key}"


def test_commit_off_leaves_dirty_and_skips(session, repo: Path):
    """R1.2 / AC4: commit off → dirty remains, skipped=commit_off, error empty."""
    workspace = _workspace(session, repo)
    ticket = _ticket(session, workspace, commit=False)
    tree, run = _cut_tree(session, workspace, ticket)
    run.status = RunStatus.SUCCEEDED
    session.add(run)
    session.commit()
    _dirty(tree)

    result = commit_succeeded_run_worktree(session, ticket, run)

    assert result.committed is False
    assert result.attempted is False
    assert result.error == ""
    assert result.skipped is WorktreeCommitSkip.COMMIT_OFF
    assert result.paths == ()
    assert _head_tracked(tree) == "VALUE = 1\n"
    assert (tree / _TRACKED).read_text() == _EDIT
    assert uncommitted_at_landing(session, ticket, workspace) == (_TRACKED,)


def test_shared_checkout_skips_not_ticket_worktree(session, repo: Path):
    """R1.2 / AC4 / AC10: shared checkout → not_ticket_worktree.

    Commit is armed so this skip is not commit_off. Spec evaluation order
    splits missing-workspace from root-equality around commit_off; AC10
    groups both as not_ticket_worktree before commit_off. Arming commit
    makes the two orders agree.
    """
    workspace = _workspace(session, repo)
    ticket = _ticket(session, workspace, commit=True)
    (repo / _TRACKED).write_text(_EDIT)
    run = AgentRun(
        run_code=f"r-shared-{uuid4().hex[:8]}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        stage_key="implement",
        status=RunStatus.SUCCEEDED,
        changed_paths_json="[]",
    )
    session.add(run)
    session.commit()

    result = commit_succeeded_run_worktree(session, ticket, run)

    assert result.skipped is WorktreeCommitSkip.NOT_TICKET_WORKTREE
    assert result.committed is False
    assert result.error == ""
    assert result.paths == ()
    assert _head_tracked(repo) == "VALUE = 1\n"
    assert (repo / _TRACKED).read_text() == _EDIT


def test_failed_run_status_does_not_commit(session, repo: Path):
    """R1.2 / AC10: failed_run is first — no commit even when dirty and commit on."""
    workspace = _workspace(session, repo)
    ticket = _ticket(session, workspace, commit=True)
    tree, run = _cut_tree(session, workspace, ticket, status=RunStatus.SUCCEEDED)
    _dirty(tree)
    run.status = RunStatus.FAILED
    session.add(run)
    session.commit()

    result = commit_succeeded_run_worktree(session, ticket, run)

    assert result.skipped is WorktreeCommitSkip.FAILED_RUN
    assert result.committed is False
    assert result.error == ""
    assert _head_tracked(tree) == "VALUE = 1\n"
    assert (tree / _TRACKED).read_text() == _EDIT


def test_git_valueerror_sets_error_and_is_not_raised(session, repo: Path):
    """R1.2 / AC4: git ValueError → error set, not raised."""
    workspace = _workspace(session, repo)
    ticket = _ticket(session, workspace, commit=True)
    tree, run = _cut_tree(session, workspace, ticket)
    run.status = RunStatus.SUCCEEDED
    session.add(run)
    session.commit()
    _dirty(tree)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'refused by the workspace gate' >&2\nexit 1\n")
    hook.chmod(0o755)

    try:
        result = commit_succeeded_run_worktree(session, ticket, run)
    except ValueError as exc:
        pytest.fail(f"helper raised ValueError instead of returning error: {exc}")

    assert result.committed is False
    assert result.skipped is WorktreeCommitSkip.NONE
    assert "refused by the workspace gate" in result.error
    assert _head_tracked(tree) == "VALUE = 1\n"


def test_leftover_dirt_after_false_sets_error(session, repo: Path):
    """R1.2 / AC4 / R1-risk-b: commit_paths_in False with leftover dirt is error."""
    workspace = _workspace(session, repo)
    ticket = _ticket(session, workspace, commit=True)
    tree, run = _cut_tree(session, workspace, ticket)
    run.status = RunStatus.SUCCEEDED
    session.add(run)
    session.commit()
    _dirty(tree)

    with patch(
        "loregarden.services.run_worktree_commit.commit_paths_in",
        return_value=False,
    ):
        result = commit_succeeded_run_worktree(session, ticket, run)

    assert result.committed is False
    assert result.skipped is WorktreeCommitSkip.NONE
    assert result.error
    assert _TRACKED in result.error
    assert (tree / _TRACKED).read_text() == _EDIT
    assert _head_tracked(tree) == "VALUE = 1\n"


def test_other_running_run_on_the_worktree_skips_live_writer(session, repo: Path):
    """R1.2 / AC4 / AC10: another RUNNING AgentRun on the same worktree → live_writer."""
    workspace = _workspace(session, repo)
    ticket = _ticket(session, workspace, commit=True)
    tree, run = _cut_tree(session, workspace, ticket)
    run.status = RunStatus.SUCCEEDED
    session.add(run)
    session.commit()
    assert run.worktree_id
    writer = AgentRun(
        run_code=f"r-live-{uuid4().hex[:8]}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        stage_key="implement",
        status=RunStatus.RUNNING,
        worktree_id=run.worktree_id,
        changed_paths_json="[]",
    )
    session.add(writer)
    session.commit()
    _dirty(tree)

    result = commit_succeeded_run_worktree(session, ticket, run)

    assert result.skipped is WorktreeCommitSkip.LIVE_WRITER
    assert result.committed is False
    assert result.error == ""
    assert _head_tracked(tree) == "VALUE = 1\n"


def test_empty_path_set_is_clean_noop(session, repo: Path):
    """R1.3: empty uncommitted_at_landing on a ticket worktree is clean, not False-as-nothing."""
    workspace = _workspace(session, repo)
    ticket = _ticket(session, workspace, commit=True)
    tree, run = _cut_tree(session, workspace, ticket)
    run.status = RunStatus.SUCCEEDED
    session.add(run)
    session.commit()

    result = commit_succeeded_run_worktree(session, ticket, run)

    assert result.skipped is WorktreeCommitSkip.CLEAN
    assert result.committed is False
    assert result.attempted is False
    assert result.error == ""
    assert result.paths == ()
    assert git(tree, "status", "--porcelain").stdout == ""


def test_uncommitted_at_landing_runtimeerror_sets_error(session, repo: Path):
    """AC10: RuntimeError from uncommitted_at_landing → error set, skipped=none."""
    workspace = _workspace(session, repo)
    ticket = _ticket(session, workspace, commit=True)
    _tree, run = _cut_tree(session, workspace, ticket)
    run.status = RunStatus.SUCCEEDED
    session.add(run)
    session.commit()

    with patch(
        "loregarden.services.run_worktree_commit.uncommitted_at_landing",
        side_effect=RuntimeError("git ls-files failed in the ticket worktree"),
    ):
        result = commit_succeeded_run_worktree(session, ticket, run)

    assert result.skipped is WorktreeCommitSkip.NONE
    assert result.committed is False
    assert "git ls-files failed in the ticket worktree" in result.error


def test_run_none_uses_ticket_workflow_stage_key_in_the_message(session, repo: Path):
    """R3 assumption: parallel caller passes run=None; message uses workflow_stage_key."""
    workspace = _workspace(session, repo)
    ticket = _ticket(session, workspace, commit=True)
    ticket.workflow_stage_key = "plan"
    session.add(ticket)
    session.commit()
    tree, cutter = _cut_tree(session, workspace, ticket, stage_key="plan")
    cutter.status = RunStatus.SUCCEEDED
    session.add(cutter)
    session.commit()
    _dirty(tree)

    result = commit_succeeded_run_worktree(session, ticket, None)

    assert result.committed is True
    assert result.skipped is WorktreeCommitSkip.NONE
    assert _subject(tree) == f"{ticket.external_id}: plan"


def test_helper_does_not_import_run_completion_or_orchestration():
    """R1.1 / AC11: helper must not import run_completion or orchestration."""
    from loregarden.services import run_worktree_commit

    source = Path(run_worktree_commit.__file__).read_text(encoding="utf-8")
    assert "loregarden.services.run_completion" not in source
    assert "loregarden.services.orchestration" not in source
    assert "OrchestrationService" not in source


def test_cancelled_run_skips_failed_run(session, repo: Path):
    """AC10 mutation: CANCELLED is not SUCCEEDED → failed_run, no commit."""
    workspace = _workspace(session, repo)
    ticket = _ticket(session, workspace, commit=True)
    tree, run = _cut_tree(session, workspace, ticket, status=RunStatus.SUCCEEDED)
    _dirty(tree)
    run.status = RunStatus.CANCELLED
    session.add(run)
    session.commit()

    result = commit_succeeded_run_worktree(session, ticket, run)

    assert result.skipped is WorktreeCommitSkip.FAILED_RUN
    assert result.committed is False
    assert result.error == ""
    assert _head_tracked(tree) == "VALUE = 1\n"


def test_queued_run_skips_failed_run(session, repo: Path):
    """AC10: QUEUED is not SUCCEEDED — do not commit under a run that has not started."""
    workspace = _workspace(session, repo)
    ticket = _ticket(session, workspace, commit=True)
    tree, run = _cut_tree(session, workspace, ticket, status=RunStatus.SUCCEEDED)
    _dirty(tree)
    run.status = RunStatus.QUEUED
    session.add(run)
    session.commit()

    result = commit_succeeded_run_worktree(session, ticket, run)

    assert result.skipped is WorktreeCommitSkip.FAILED_RUN
    assert result.committed is False
    assert _head_tracked(tree) == "VALUE = 1\n"


def test_missing_workspace_skips_not_ticket_worktree(session, repo: Path):
    """AC10 / spec R1 step 2: workspace_for_ticket None → not_ticket_worktree, no git."""
    workspace = _workspace(session, repo)
    ticket = _ticket(session, workspace, commit=True)
    tree, run = _cut_tree(session, workspace, ticket)
    run.status = RunStatus.SUCCEEDED
    session.add(run)
    session.commit()
    _dirty(tree)

    with patch(
        "loregarden.services.run_worktree_commit.workspace_for_ticket",
        return_value=None,
    ):
        result = commit_succeeded_run_worktree(session, ticket, run)

    assert result.skipped is WorktreeCommitSkip.NOT_TICKET_WORKTREE
    assert result.committed is False
    assert result.error == ""
    assert _head_tracked(tree) == "VALUE = 1\n"
    assert (tree / _TRACKED).read_text() == _EDIT


def test_commit_off_beats_live_writer(session, repo: Path):
    """AC10 order: commit_off is evaluated before live_writer."""
    workspace = _workspace(session, repo)
    ticket = _ticket(session, workspace, commit=False)
    tree, run = _cut_tree(session, workspace, ticket)
    run.status = RunStatus.SUCCEEDED
    session.add(run)
    session.commit()
    writer = AgentRun(
        run_code=f"r-live-{uuid4().hex[:8]}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        stage_key="implement",
        status=RunStatus.RUNNING,
        worktree_id=run.worktree_id,
        changed_paths_json="[]",
    )
    session.add(writer)
    session.commit()
    _dirty(tree)

    result = commit_succeeded_run_worktree(session, ticket, run)

    assert result.skipped is WorktreeCommitSkip.COMMIT_OFF
    assert result.error == ""
    assert result.committed is False
    assert _head_tracked(tree) == "VALUE = 1\n"


def test_live_writer_beats_uncommitted_runtimeerror(session, repo: Path):
    """AC10 order: live_writer stops before uncommitted_at_landing is called."""
    workspace = _workspace(session, repo)
    ticket = _ticket(session, workspace, commit=True)
    tree, run = _cut_tree(session, workspace, ticket)
    run.status = RunStatus.SUCCEEDED
    session.add(run)
    session.commit()
    writer = AgentRun(
        run_code=f"r-live-{uuid4().hex[:8]}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        stage_key="implement",
        status=RunStatus.RUNNING,
        worktree_id=run.worktree_id,
        changed_paths_json="[]",
    )
    session.add(writer)
    session.commit()
    _dirty(tree)

    with patch(
        "loregarden.services.run_worktree_commit.uncommitted_at_landing",
        side_effect=RuntimeError("git ls-files failed in the ticket worktree"),
    ):
        result = commit_succeeded_run_worktree(session, ticket, run)

    assert result.skipped is WorktreeCommitSkip.LIVE_WRITER
    assert result.error == ""
    assert result.committed is False
    assert _head_tracked(tree) == "VALUE = 1\n"


def test_run_none_skips_live_writer_on_the_ticket_worktree(session, repo: Path):
    """Spec R1 step 5: with run=None, worktree id is the ticket's ACTIVE non-chat tree."""
    workspace = _workspace(session, repo)
    ticket = _ticket(session, workspace, commit=True)
    tree, cutter = _cut_tree(session, workspace, ticket)
    cutter.status = RunStatus.SUCCEEDED
    session.add(cutter)
    session.commit()
    writer = AgentRun(
        run_code=f"r-live-{uuid4().hex[:8]}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="planner",
        stage_key="plan",
        status=RunStatus.RUNNING,
        worktree_id=cutter.worktree_id,
        changed_paths_json="[]",
    )
    session.add(writer)
    session.commit()
    _dirty(tree)

    result = commit_succeeded_run_worktree(session, ticket, None)

    assert result.skipped is WorktreeCommitSkip.LIVE_WRITER
    assert result.committed is False
    assert result.error == ""
    assert _head_tracked(tree) == "VALUE = 1\n"
    assert (tree / _TRACKED).read_text() == _EDIT


def test_passed_in_running_run_commits(session, repo: Path):
    """CHECKPOINT: R4 passes a still-RUNNING triage run. That run is not failed_run.

    Spec R1 step 1 says `status is not SUCCEEDED` → failed_run. Taken literally,
    every ticket-triage turn would skip and AC6 would never commit. Conservative
    assumption: the passed-in run is excluded from live_writer and may commit
    while it is still RUNNING.
    """
    workspace = _workspace(session, repo)
    ticket = _ticket(session, workspace, commit=True)
    tree, run = _cut_tree(session, workspace, ticket, status=RunStatus.RUNNING)
    _dirty(tree)

    result = commit_succeeded_run_worktree(session, ticket, run)

    assert result.skipped is WorktreeCommitSkip.NONE
    assert result.committed is True
    assert result.error == ""
    assert _head_tracked(tree) == _EDIT
    assert uncommitted_at_landing(session, ticket, workspace) == ()


def test_untracked_unrecorded_file_is_not_added(session, repo: Path):
    """R6 give-up / AC2: land's path set, not `git add -A`. Unrecorded untracked stays dirty."""
    workspace = _workspace(session, repo)
    ticket = _ticket(session, workspace, commit=True)
    tree, run = _cut_tree(session, workspace, ticket)
    run.status = RunStatus.SUCCEEDED
    session.add(run)
    session.commit()
    _dirty(tree)
    (tree / "scratch.tmp").write_text("generated\n")

    result = commit_succeeded_run_worktree(session, ticket, run)

    assert result.committed is True
    assert _TRACKED in result.paths
    assert "scratch.tmp" not in result.paths
    assert _head_tracked(tree) == _EDIT
    porcelain = git(tree, "status", "--porcelain").stdout
    assert "scratch.tmp" in porcelain


def test_untracked_recorded_file_is_committed(session, repo: Path):
    """Land's path set: untracked counts when a run recorded it."""
    workspace = _workspace(session, repo)
    ticket = _ticket(session, workspace, commit=True)
    tree, run = _cut_tree(session, workspace, ticket)
    run.status = RunStatus.SUCCEEDED
    run.changed_paths_json = json.dumps(["new_mod.py"])
    session.add(run)
    session.commit()
    (tree / "new_mod.py").write_text("x = 1\n")

    result = commit_succeeded_run_worktree(session, ticket, run)

    assert result.committed is True
    assert "new_mod.py" in result.paths
    assert git(tree, "show", "HEAD:new_mod.py").stdout == "x = 1\n"
    assert uncommitted_at_landing(session, ticket, workspace) == ()


def test_forbidden_callers_do_not_import_the_helper():
    """AC11: landing, git_automation, finish_external_stage, builtin_orchestrator stay out."""
    from loregarden.services import (
        builtin_orchestrator,
        external_harness,
        git_automation,
        landing_uncommitted,
        orchestration,
    )

    for module in (
        builtin_orchestrator,
        external_harness,
        git_automation,
        landing_uncommitted,
        orchestration,
    ):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "run_worktree_commit" not in source, module.__name__
        assert "commit_succeeded_run_worktree" not in source, module.__name__
