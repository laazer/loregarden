"""Running one stage N times and keeping one result.

The pipeline's other parallelism runs *different* work at once — different
tickets in lanes, different reviewers over one diff. This runs the *same* stage
several times independently and throws all but one away. It is worth the N×
tokens in exactly two places: a hard implement stage, and a stage that has
already burned a rework cycle, where a second independent attempt is cheaper
than another trip through review → implement.

It is not a default execution mode, and nothing here schedules itself.

Isolation is the whole design. Each attempt gets its own worktree cut from the
ticket's branch and its own branch to commit on, because the orchestrator
commits whole trees and two attempts sharing one would sweep each other's work.
Settling — promote one or decline them all — always ends with every losing
worktree removed and its branch deleted; leaking those is how the queue used to
lose slots.

`stage_fanout_groups` owns the rows. This owns the git and the dispatch.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.db.session import engine
from loregarden.models.domain import (
    AgentRun,
    RunStatus,
    StageFanoutAttempt,
    StageFanoutAttemptStatus,
    StageFanoutGroup,
    StageFanoutGroupStatus,
    StageFanoutOutcome,
    StageStatus,
    Ticket,
    WorkflowInstance,
    Workspace,
    Worktree,
)
from loregarden.services import stage_fanout_groups as groups
from loregarden.services.git_branch import resolve_ticket_branch
from loregarden.services.git_commit_push_service import commit_paths_in, working_tree_paths
from loregarden.services.git_subprocess import run_git
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_profile import resolve_orchestration_profile
from loregarden.services.stage_report import parse_stage_report
from loregarden.services.ticket_worktree import resolve_ticket_root
from loregarden.services.workspace_paths import resolve_workspace_root
from loregarden.services.worktree_service import WorktreeService
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

#: More than a handful of attempts is a token bonfire, not a comparison.
MAX_ATTEMPTS = 5

#: Diffs are for reading side by side in a browser, not for archiving.
MAX_DIFF_CHARS = 200_000


class FanoutError(ValueError):
    """A fan-out could not be started or settled. The message is user-facing."""


@dataclass(frozen=True)
class AttemptDiff:
    attempt_id: str
    branch: str
    stat: str
    patch: str
    files_changed: int
    truncated: bool

    def as_dict(self) -> dict:
        return {
            "attempt_id": self.attempt_id,
            "branch": self.branch,
            "stat": self.stat,
            "patch": self.patch,
            "files_changed": self.files_changed,
            "truncated": self.truncated,
        }


def launch_fanout(
    session: Session,
    ticket: Ticket,
    stage_key: str,
    attempt_count: int,
    *,
    agent_id: str = "",
    skill_name: str = "",
    auto_approve: bool = False,
) -> dict:
    """Run `stage_key` `attempt_count` times, each in its own worktree.

    Blocks until every attempt finishes: the point is to compare them, and
    there is nothing to compare until they are all done. Returns the group,
    already carrying each attempt's status.
    """
    if not 2 <= attempt_count <= MAX_ATTEMPTS:
        raise FanoutError(f"attempt_count must be between 2 and {MAX_ATTEMPTS}")

    workspace = session.get(Workspace, ticket.workspace_id)
    if not workspace:
        raise FanoutError("Workspace not found")

    open_group = _open_group_for(session, ticket.id)
    if open_group is not None:
        raise FanoutError(
            f"Ticket already has an unsettled fan-out on stage {open_group.stage_key!r}. "
            "Promote or decline it first."
        )

    group = groups.create_group(session, ticket.id, stage_key, attempt_count)
    # The stage is not "running" in the ordinary sense — no single run owns it —
    # but leaving it pending would let the orchestrator dispatch it underneath
    # the fan-out.
    orchestration = OrchestrationService(session)
    orchestration.finalize_stage(ticket, stage_key, status=StageStatus.RUNNING)
    session.refresh(ticket)

    parent_branch = _parent_branch(session, ticket, workspace)
    service = WorktreeService(session, repo_path=str(resolve_workspace_root(workspace)))

    prepared: list[tuple[str, str]] = []  # (attempt_id, run_id)
    for index in range(attempt_count):
        attempt = groups.create_attempt(session, group.id, attempt_index=index)
        run = orchestration.start_run(
            ticket,
            stage_key=stage_key,
            agent_id=agent_id or None,
            skill_name=skill_name or None,
            auto_approve=auto_approve,
        )
        worktree = service.create_worktree(
            workspace_id=ticket.workspace_id,
            agent_run_id=run.id,
            parent_branch=parent_branch,
            branch=f"{resolve_ticket_branch(ticket)}-attempt-{index + 1}",
        )
        if worktree is None:
            _abort_launch(session, group, prepared, index)
            raise FanoutError(
                f"Could not create a worktree for attempt {index + 1}; nothing was dispatched"
            )
        run.worktree_id = worktree.id
        session.add(run)
        session.commit()
        groups.link_attempt_run(session, attempt.id, run.id)
        groups.link_attempt_worktree(session, attempt.id, worktree.id)
        groups.update_attempt_status(session, attempt.id, StageFanoutAttemptStatus.QUEUED)
        prepared.append((attempt.id, run.id))

    # Read everything this session needs before the threads start, then let go
    # of its transaction. A session held open here blocks the attempts' own
    # writes — and touching it from their threads at all is unsafe.
    ticket_id = ticket.id
    group_id = group.id
    session.commit()

    with ThreadPoolExecutor(max_workers=len(prepared)) as pool:
        list(pool.map(lambda pair: _run_attempt(ticket_id, *pair), prepared))

    session.expire_all()
    return groups.serialize_group(session, group_id)


def _run_attempt(ticket_id: str, attempt_id: str, run_id: str) -> None:
    """One attempt, in its own session because it runs in its own thread."""
    with Session(engine) as session:
        groups.update_attempt_status(session, attempt_id, StageFanoutAttemptStatus.RUNNING)
        run = session.get(AgentRun, run_id)
        ticket = session.get(Ticket, ticket_id)
        if not run or not ticket:
            groups.update_attempt_status(
                session,
                attempt_id,
                StageFanoutAttemptStatus.FAILED,
                failure_details="run or ticket vanished before dispatch",
            )
            return

        # advance_workflow=False: the stage advances when an attempt is
        # promoted, not when one of N finishes. skip_git_branch because the
        # attempt's worktree is already on its own branch.
        completed = CliAgentExecutor(session).execute(
            run, ticket, advance_workflow=False, skip_git_branch=True
        )
        session.refresh(run)
        _commit_attempt_work(session, run, attempt_id)

        report = parse_stage_report(completed.stdout or "")
        failed = (
            completed.status != RunStatus.SUCCEEDED or report is None or report.status != "pass"
        )
        if failed:
            detail = (completed.stderr or "").strip() or (
                "agent exited cleanly with no stage report"
                if report is None
                else f"stage report status: {report.status}"
            )
            groups.update_attempt_status(
                session,
                attempt_id,
                StageFanoutAttemptStatus.FAILED,
                failure_details=detail[:2000],
            )
            return
        groups.update_attempt_status(session, attempt_id, StageFanoutAttemptStatus.SUCCEEDED)


def _commit_attempt_work(session: Session, run: AgentRun, attempt_id: str) -> None:
    """Commit what the attempt wrote, so there is something to diff and merge.

    Uncommitted work would be invisible to the comparison and lost when the
    losing worktrees are removed.
    """
    worktree = session.get(Worktree, run.worktree_id) if run.worktree_id else None
    if not worktree or not worktree.worktree_path:
        return
    root = Path(worktree.worktree_path)
    if not root.is_dir():
        return
    paths = working_tree_paths(root)
    if not paths:
        return
    try:
        commit_paths_in(root, f"{run.agent_id}: fan-out attempt {attempt_id[:8]}", paths)
    except ValueError as exc:
        logger.warning("Could not commit fan-out attempt %s: %s", attempt_id, exc)


def attempt_diffs(session: Session, group_id: str) -> list[dict]:
    """Each attempt's work as a patch against the branch they were all cut from.

    The same base for every attempt is what makes them comparable — diffing
    each against its own parent would answer a different question per column.
    """
    group = _require_group(session, group_id)
    attempts = _attempts_for(session, group.id)
    diffs: list[AttemptDiff] = []
    for attempt in attempts:
        worktree = session.get(Worktree, attempt.worktree_id) if attempt.worktree_id else None
        if not worktree or not Path(worktree.worktree_path).is_dir():
            diffs.append(AttemptDiff(attempt.id, attempt.branch, "", "", 0, False))
            continue
        base = worktree.parent_branch or "main"
        cwd = worktree.worktree_path
        stat = run_git(
            ["diff", "--stat", f"{base}...HEAD"],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
        )
        patch = run_git(
            ["diff", f"{base}...HEAD"], cwd=cwd, check=False, capture_output=True, text=True
        )
        body = patch.stdout if patch.returncode == 0 else ""
        names = run_git(
            ["diff", "--name-only", f"{base}...HEAD"],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
        )
        diffs.append(
            AttemptDiff(
                attempt_id=attempt.id,
                branch=attempt.branch,
                stat=stat.stdout if stat.returncode == 0 else "",
                patch=body[:MAX_DIFF_CHARS],
                files_changed=len([line for line in names.stdout.splitlines() if line.strip()]),
                truncated=len(body) > MAX_DIFF_CHARS,
            )
        )
    return [diff.as_dict() for diff in diffs]


def promote_attempt(session: Session, group_id: str, attempt_id: str) -> dict:
    """Merge one attempt into the ticket's branch and discard the rest."""
    group = _require_group(session, group_id)
    _require_open(group)
    attempt = session.get(StageFanoutAttempt, attempt_id)
    if attempt is None or attempt.group_id != group.id:
        raise FanoutError("attempt_id does not belong to this fan-out")
    if not attempt.branch:
        raise FanoutError("That attempt never produced a branch to merge")

    ticket = session.get(Ticket, group.ticket_id)
    workspace = session.get(Workspace, group.workspace_id) if ticket else None
    if not ticket or not workspace:
        raise FanoutError("Ticket or workspace not found")

    ticket_root = resolve_ticket_root(session, ticket, workspace)
    merge = run_git(
        ["merge", "--no-edit", attempt.branch],
        cwd=str(ticket_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if merge.returncode != 0:
        # Leave the fan-out open: the operator can pick a different attempt or
        # decline, and an aborted merge leaves the ticket tree as it was.
        run_git(["merge", "--abort"], cwd=str(ticket_root), check=False, capture_output=True)
        detail = (merge.stderr or merge.stdout or "merge failed").strip()
        raise FanoutError(f"Could not merge {attempt.branch} into the ticket branch: {detail}")

    groups.update_attempt_status(session, attempt.id, StageFanoutAttemptStatus.PROMOTED)
    discarded = _discard_attempts(session, group, keep_attempt_id=attempt.id)
    groups.settle_group(
        session,
        group.id,
        outcome=StageFanoutOutcome.PROMOTED,
        winner_attempt_id=attempt.id,
        status=StageFanoutGroupStatus.SETTLED,
    )
    OrchestrationService(session).finalize_stage(ticket, group.stage_key, status=StageStatus.DONE)
    session.refresh(ticket)
    result = groups.serialize_group(session, group.id)
    result["discarded_attempts"] = discarded
    return result


def decline_fanout(session: Session, group_id: str, reason: str = "") -> dict:
    """Keep none of them, and put the stage back exactly as it was."""
    group = _require_group(session, group_id)
    _require_open(group)
    ticket = session.get(Ticket, group.ticket_id)
    if not ticket:
        raise FanoutError("Ticket not found")

    discarded = _discard_attempts(session, group, keep_attempt_id=None)
    _restore_pre_fanout_state(session, group, ticket)
    groups.settle_group(
        session,
        group.id,
        outcome=StageFanoutOutcome.DECLINED,
        declined_reason=reason,
        status=StageFanoutGroupStatus.SETTLED,
    )
    result = groups.serialize_group(session, group.id)
    result["discarded_attempts"] = discarded
    return result


def _restore_pre_fanout_state(session: Session, group: StageFanoutGroup, ticket: Ticket) -> None:
    """Put back the cursor, the stage map and the agent hint the launch moved.

    The group recorded all four at launch precisely so declining is a restore
    rather than a guess.
    """
    ticket.workflow_stage_key = group.pre_fanout_workflow_stage_key
    ticket.workflow_stage_status = StageStatus(group.pre_fanout_workflow_stage_status)
    ticket.next_agent = group.pre_fanout_next_agent
    session.add(ticket)

    instance = session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).first()
    if instance and group.pre_fanout_stage_map_json:
        instance.stages_json = group.pre_fanout_stage_map_json
        instance.current_stage_key = group.pre_fanout_workflow_stage_key
        session.add(instance)
    session.commit()


def _discard_attempts(
    session: Session, group: StageFanoutGroup, *, keep_attempt_id: str | None
) -> list[str]:
    """Remove every losing attempt's worktree and branch. Returns their ids.

    Forced: a losing attempt's commits are exactly what nobody chose, and a
    branch left behind turns up in Branch Triage as work someone abandoned.
    """
    workspace = session.get(Workspace, group.workspace_id)
    if not workspace:
        return []
    repo_root = str(resolve_workspace_root(workspace))
    service = WorktreeService(session, repo_path=repo_root)

    discarded: list[str] = []
    for attempt in _attempts_for(session, group.id):
        if attempt.id == keep_attempt_id:
            continue
        worktree = session.get(Worktree, attempt.worktree_id) if attempt.worktree_id else None
        if worktree:
            service.cleanup_worktree(worktree)
        if attempt.branch:
            run_git(
                ["branch", "-D", attempt.branch],
                cwd=repo_root,
                check=False,
                capture_output=True,
            )
        if attempt.status != StageFanoutAttemptStatus.PROMOTED:
            groups.update_attempt_status(session, attempt.id, StageFanoutAttemptStatus.DECLINED)
        discarded.append(attempt.id)
    return discarded


def _abort_launch(
    session: Session,
    group: StageFanoutGroup,
    prepared: list[tuple[str, str]],
    failed_index: int,
) -> None:
    """Undo a half-built fan-out rather than dispatch a partial one."""
    _discard_attempts(session, group, keep_attempt_id=None)
    ticket = session.get(Ticket, group.ticket_id)
    if ticket:
        _restore_pre_fanout_state(session, group, ticket)
    groups.settle_group(
        session,
        group.id,
        outcome=StageFanoutOutcome.FAILED,
        failure_summary=f"worktree creation failed for attempt {failed_index + 1}",
        status=StageFanoutGroupStatus.FAILED,
    )
    logger.warning("Aborted fan-out %s after preparing %d attempt(s)", group.id, len(prepared))


def _parent_branch(session: Session, ticket: Ticket, workspace: Workspace) -> str:
    """The branch every attempt is cut from: the ticket's, or the base branch.

    A ticket that has not run yet has no branch in the repository, and cutting
    the attempts from a ref that does not exist fails at `git worktree add`.
    """
    branch = resolve_ticket_branch(ticket)
    exists = run_git(
        ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=str(resolve_workspace_root(workspace)),
        check=False,
        capture_output=True,
    )
    if exists.returncode == 0:
        return branch
    return resolve_orchestration_profile(workspace).git.base_branch


def _open_group_for(session: Session, ticket_id: str) -> StageFanoutGroup | None:
    return session.exec(
        select(StageFanoutGroup)
        .where(StageFanoutGroup.ticket_id == ticket_id)
        .where(StageFanoutGroup.outcome == StageFanoutOutcome.PENDING)
    ).first()


def _attempts_for(session: Session, group_id: str) -> list[StageFanoutAttempt]:
    return list(
        session.exec(
            select(StageFanoutAttempt)
            .where(StageFanoutAttempt.group_id == group_id)
            .order_by(StageFanoutAttempt.attempt_index)
        ).all()
    )


def _require_group(session: Session, group_id: str) -> StageFanoutGroup:
    group = session.get(StageFanoutGroup, group_id)
    if group is None:
        raise FanoutError(f"No such fan-out group: {group_id}")
    return group


def _require_open(group: StageFanoutGroup) -> None:
    if group.outcome != StageFanoutOutcome.PENDING:
        raise FanoutError(f"This fan-out is already settled ({group.outcome.value})")
