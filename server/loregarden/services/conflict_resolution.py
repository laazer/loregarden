"""Hand a merge conflict to an agent instead of to a human.

The only resolution that existed was ``git checkout --ours`` on every
conflicted file, which is not resolution — it is discarding the other branch's
changes and calling the result merged. That is silently wrong in exactly the
case conflicts matter.

This drives the same loop the rest of the control plane uses for "the work
came back wrong": leave the merge in progress in the worktree so the agent can
see the conflict markers, record the conflict in the rework-feedback ledger so
it reaches the agent's prompt, and re-dispatch the ticket's implementer.
Attempts are bounded and recorded as ConflictReport rows; when the budget runs
out the ticket blocks, which is what would have happened immediately without
this.
"""

from __future__ import annotations

import logging
from pathlib import Path

from loregarden.models.domain import AgentRun, ConflictReport, Ticket, Worktree
from loregarden.services.git_subprocess import run_git
from loregarden.services.rework_feedback import record_rework_feedback
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

#: Kind recorded on the ledger entry, so the agent's prompt frames it as a
#: merge conflict rather than a review rejection.
CONFLICT_STAGE_CONTEXT = "merge conflict"


def attempts_so_far(session: Session, worktree: Worktree) -> int:
    return len(
        session.exec(select(ConflictReport).where(ConflictReport.worktree_id == worktree.id)).all()
    )


def conflicted_files(repo_root: Path) -> list[str]:
    """Files git has left with conflict markers, from the in-progress merge."""
    result = run_git(
        ["diff", "--name-only", "--diff-filter=U"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _conflict_excerpt(repo_root: Path, files: list[str], limit: int = 4000) -> str:
    """Enough of the conflict for the agent to act on without the whole diff."""
    chunks: list[str] = []
    budget = limit
    for name in files:
        if budget <= 0:
            break
        result = run_git(
            ["diff", "--", name],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            continue
        text = result.stdout[:budget]
        budget -= len(text)
        chunks.append(f"### {name}\n\n```diff\n{text}\n```")
    return "\n\n".join(chunks)


def _feedback(worktree: Worktree, files: list[str], excerpt: str, attempt: int) -> str:
    return "\n".join(
        [
            f"Merging `{worktree.branch or worktree.worktree_path}` into "
            f"`{worktree.parent_branch}` hit conflicts (attempt {attempt}).",
            "",
            "The merge is left in progress in your working tree, so the conflict "
            "markers are in the files listed below. Resolve each one by keeping "
            "the intent of BOTH sides — do not blanket-take one side, and do not "
            "revert the other branch's work to make the conflict go away. When "
            "every file is resolved, stage them and commit the merge.",
            "",
            "Conflicted files:",
            *[f"- `{name}`" for name in files],
            "",
            excerpt,
        ]
    )


def request_agent_resolution(
    session: Session,
    run: AgentRun,
    ticket: Ticket,
    worktree: Worktree,
    repo_root: Path,
    max_attempts: int,
) -> ConflictReport | None:
    """Record the conflict and re-dispatch the implementer to resolve it.

    Returns the report when a resolution attempt was started, and None when the
    attempt budget is spent — the caller blocks the ticket in that case.
    """
    attempt = attempts_so_far(session, worktree) + 1
    if attempt > max_attempts:
        logger.info(
            "Conflict resolution budget spent for worktree %s (%s attempts)",
            worktree.id,
            max_attempts,
        )
        return None

    files = conflicted_files(repo_root)
    if not files:
        logger.warning("Asked to resolve conflicts in %s but git reports none", repo_root)
        return None

    report = ConflictReport(
        worktree_id=worktree.id,
        ticket_id=ticket.id,
        merge_attempt_number=attempt,
        conflict_type="merge_conflict",
        conflict_details=f"{len(files)} conflicted file(s): {', '.join(files)}",
        resolution_attempted=True,
    )
    report.conflicting_files = files
    session.add(report)
    session.commit()

    stage_key = ticket.workflow_stage_key or run.stage_key
    record_rework_feedback(
        session,
        ticket,
        target_stage=stage_key,
        from_stage=CONFLICT_STAGE_CONTEXT,
        context=_feedback(worktree, files, _conflict_excerpt(repo_root, files), attempt),
        run_id=run.id,
    )

    _dispatch_resolver(session, run, ticket, stage_key)
    return report


def _dispatch_resolver(session: Session, run: AgentRun, ticket: Ticket, stage_key: str) -> None:
    """Re-run the ticket's stage agent, in the same worktree, on the conflict.

    Imported at call time — orchestration and run_service both reach this
    module through the automation pipeline, so module-level imports cycle.
    """
    from loregarden.services.orchestration import OrchestrationService
    from loregarden.services.run_service import schedule_agent_run

    orchestration = OrchestrationService(session)
    resolver = orchestration.start_run(ticket, stage_key=stage_key, agent_id=run.agent_id)
    # Same worktree: the conflict only exists there, and a run in a fresh
    # checkout would see a clean tree and nothing to resolve.
    resolver.worktree_id = run.worktree_id
    session.add(resolver)
    session.commit()

    schedule_agent_run(resolver.id)
    logger.info("Dispatched conflict-resolution run %s for ticket %s", resolver.id, ticket.id)


def mark_resolved(session: Session, worktree: Worktree) -> None:
    """Record that the latest attempt on this worktree worked."""
    report = session.exec(
        select(ConflictReport)
        .where(ConflictReport.worktree_id == worktree.id)
        .order_by(ConflictReport.merge_attempt_number.desc())
    ).first()
    if report and not report.resolution_successful:
        report.resolution_successful = True
        session.add(report)
        session.commit()
