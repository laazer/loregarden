"""What an integration merge turn may spend, and when a turn may run again.

A tip of an integration branch gets `max_conflict_resolve_attempts` merge
turns. Only a turn that tried and was refused spends one: its run's
`failure_kind` is `WORK`. A turn that never got to try — a CLI usage limit or
auth failure, a spawn failure, loregarden's own crash — is `HARNESS`, and
spends nothing. Before that distinction, a quota outage spent two blobert
trees' budgets within a minute of the sweep reaching them, and nothing would
have retried once the quota reset (lg-milestone-that-959).

A `HARNESS` failure is not retried at once either: the cause is usually still
there, and the sweep ticks every 30 seconds. The next turn waits for a backoff
that doubles with each consecutive harness failure on the tip, so an outage
costs a handful of failed spawns, not one every tick, and for a usage limit
that names its reset, until then.

Runs from before `failure_kind` existed carry none, and count as neither.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from loregarden.models.domain import AgentRun, BlockKind, RunStatus, Ticket, Workspace
from loregarden.models.domain.enums import comparable_utc, utcnow
from loregarden.services.git_automation_config import resolve_git_automation
from loregarden.services.usage_limits import detect_usage_limit
from sqlmodel import Session, select

#: `AgentRun.stage_key` for these turns. Not a workflow stage: nothing advances on it.
INTEGRATION_MERGE_STAGE = "integration-merge"

#: The wait after the first harness failure on a tip; it doubles after each next one.
HARNESS_RETRY_BASE = timedelta(minutes=15)
#: The longest wait between turns that keep failing on the harness.
HARNESS_RETRY_CAP = timedelta(hours=6)


def _failed_turns(session: Session, root: Ticket, *, branch_sha: str) -> list[AgentRun]:
    """Failed merge turns on this exact integration tip, oldest first."""
    return list(
        session.exec(
            select(AgentRun)
            .where(
                AgentRun.ticket_id == root.id,
                AgentRun.stage_key == INTEGRATION_MERGE_STAGE,
                AgentRun.start_head_sha == branch_sha,
                AgentRun.status == RunStatus.FAILED,
            )
            .order_by(AgentRun.started_at)
        ).all()
    )


def refused_turns(session: Session, root: Ticket, *, branch_sha: str) -> list[AgentRun]:
    """The turns on this tip that spent an attempt: they tried, and were refused.

    Keyed by the tip so a new landing on the branch, or a person's own merge,
    starts a fresh budget, while a base that keeps moving does not buy the same
    unmergeable tip endless turns.
    """
    return [
        run
        for run in _failed_turns(session, root, branch_sha=branch_sha)
        if run.failure_kind is BlockKind.WORK
    ]


def harness_retry_at(session: Session, root: Ticket, *, branch_sha: str) -> datetime | None:
    """When a turn may next run on this tip after harness failures; None if it is not waiting.

    Counts the harness failures since the tip's last refused turn, so a turn
    that did get to try resets the backoff.
    """
    since: list[AgentRun] = []
    for run in _failed_turns(session, root, branch_sha=branch_sha):
        if run.failure_kind is BlockKind.WORK:
            since = []
        elif run.failure_kind is BlockKind.HARNESS:
            since.append(run)
    if not since:
        return None
    latest = since[-1]
    last = latest.finished_at or latest.started_at
    if last is None:
        return None
    delay = min(HARNESS_RETRY_BASE * 2 ** (len(since) - 1), HARNESS_RETRY_CAP)
    retry_at = comparable_utc(last) + delay
    # A usage limit that says when it lifts is waited out: a turn before then
    # cannot run either.
    limit = detect_usage_limit(latest.stderr)
    if limit is not None and limit.reset_at is not None:
        return max(retry_at, comparable_utc(limit.reset_at))
    return retry_at


def merge_turn_refusal(
    session: Session, workspace: Workspace, root: Ticket, *, branch_sha: str
) -> str:
    """Why no merge turn may run on the integration tip ``branch_sha``; "" when one may.

    The switch, the per-tip budget, and the harness backoff, asked before a
    turn is started — by `merge_base_by_agent`, and by the health sweep before
    it hands one off. The answer is what the conflict card says.
    """
    automation = resolve_git_automation(workspace, root)
    if not automation.auto_resolve_conflicts:
        return "auto_resolve_conflicts is off for this tree"
    spent = len(refused_turns(session, root, branch_sha=branch_sha))
    if spent >= automation.max_conflict_resolve_attempts:
        return (
            f"{spent} automatic merge attempt(s) on {branch_sha[:12]} were refused "
            f"(max_conflict_resolve_attempts={automation.max_conflict_resolve_attempts})"
        )
    retry_at = harness_retry_at(session, root, branch_sha=branch_sha)
    if retry_at is not None and utcnow() < retry_at:
        return (
            f"the last merge turn on {branch_sha[:12]} could not attempt the merge "
            f"(harness failure, not counted: {spent} of "
            f"{automation.max_conflict_resolve_attempts} attempts used); "
            f"the next turn runs after {retry_at:%Y-%m-%d %H:%M} UTC"
        )
    return ""
