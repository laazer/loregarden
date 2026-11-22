"""Background CI poll: each workspace's open pull requests, into `CIService.observe`.

One `gh pr list` per workspace per tick (`settings.ci_poll_tick_seconds`).
Each open PR whose branch a ticket publishes is observed. A PR no ticket owns
(one opened by hand from a session branch, say) is not ours and is passed over.

Nothing here is silent. Every failure on the tick (a workspace whose PRs could
not be listed, a ticket whose observation raised, the merge queue's own errors,
the server not following main) lands in its `WorkspacePoll.errors`, and
`report_tick_errors` logs each at warning when it starts and again when it
clears. Before that the list was returned to a loop that discarded it, so a
push the queue kept failing, or a landed PR whose ticket it could not close,
was reported nowhere. The loop outlives a bad tick, as the GitHub sync loop does.

Not started when `ci_enabled` is off, and not in a sandboxed branch server: a
snapshot reopening its stale copies of done tickets would dispatch agents
against work that has already moved on.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from loregarden.config import settings
from loregarden.db.session import engine
from loregarden.models.domain import PrListingState, Workspace
from loregarden.services.active_workspaces import active_workspaces
from loregarden.services.ci_checks import list_open_pull_requests
from loregarden.services.ci_service import CIService
from loregarden.services.follow_main import follow_main, is_server_checkout
from loregarden.services.follow_main_status import STUCK_STATES
from loregarden.services.github_pr_listing import GitHubReadError
from loregarden.services.merge_queue_loop import advance_workspace
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session

logger = logging.getLogger(__name__)


#: Each workspace's errors on its last tick. Most failures stand (a repository
#: `gh` cannot see, a push GitHub keeps refusing) and fail the same way every
#: tick; each is logged when it starts and when it clears, not every two minutes.
_standing_errors: dict[str, frozenset[str]] = {}


@dataclass
class WorkspacePoll:
    """One workspace's tick: what was observed, and what failed."""

    slug: str
    observed: list[str] = field(default_factory=list)
    dispatched: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def poll_workspace(session: Session, workspace: Workspace) -> WorkspacePoll:
    """Observe every open PR in ``workspace`` that a ticket publishes."""
    poll = WorkspacePoll(slug=workspace.slug)
    repo_root = resolve_workspace_root(workspace)
    if not (repo_root / ".git").exists():
        return poll
    try:
        listing = list_open_pull_requests(repo_root)
    except GitHubReadError as exc:
        poll.errors.append(f"could not list pull requests: {exc}")
        return poll
    if listing.state is not PrListingState.COMPLETE:
        # The PRs that were read are still observed; the ones past the last
        # page are not, and that is reported rather than read as "no more PRs".
        partial = (
            f"GitHub reported more open pull requests after the last page; "
            f"only {len(listing.pull_requests)} were observed"
        )
        poll.errors.append(f"could not list pull requests: {partial}")
    service = CIService(session)
    for pr in listing.pull_requests:
        ticket = service.ticket_for_branch(workspace, pr.branch)
        if ticket is None:
            continue
        try:
            _, outcome = service.observe(ticket, workspace, pr)
        except Exception as exc:  # noqa: BLE001 — one PR must not starve the rest; reported below
            session.rollback()
            logger.exception("CI poll for %s: observing PR #%s failed", workspace.slug, pr.number)
            poll.errors.append(f"PR #{pr.number} ({ticket.external_id}): {exc}")
            continue
        poll.observed.append(ticket.external_id)
        if outcome is not None and outcome.dispatched:
            poll.dispatched.append(ticket.external_id)
    return poll


def run_ci_poll(session: Session) -> list[WorkspacePoll]:
    """One tick: every active workspace, its merge queue, then the log-retention prune."""
    polls: list[WorkspacePoll] = []
    for workspace in active_workspaces(session):
        poll = poll_workspace(session, workspace)
        _advance_merge_queue(session, workspace, poll)
        if is_server_checkout(workspace):
            _follow_main(session, workspace, poll)
        polls.append(poll)
    CIService(session).prune_logs()
    report_tick_errors(polls)
    return polls


def report_tick_errors(polls: list[WorkspacePoll]) -> None:
    """Log each workspace's errors at warning when they start and when they clear."""
    for poll in polls:
        current = frozenset(poll.errors)
        before = _standing_errors.get(poll.slug, frozenset())
        for error in sorted(current - before):
            logger.warning("CI poll for %s: %s", poll.slug, error)
        if cleared := before - current:
            logger.warning("CI poll for %s: cleared: %s", poll.slug, "; ".join(sorted(cleared)))
        if current:
            _standing_errors[poll.slug] = current
        else:
            _standing_errors.pop(poll.slug, None)


def _advance_merge_queue(session: Session, workspace: Workspace, poll: WorkspacePoll) -> None:
    """One merge queue cycle (`merge_queue_loop`), on the tick that already reads the PRs."""
    try:
        cycle = advance_workspace(session, workspace)
    except Exception as exc:  # noqa: BLE001 — one workspace's queue must not starve the rest; reported below
        session.rollback()
        logger.exception("Merge queue for %s: the cycle failed", workspace.slug)
        poll.errors.append(f"merge queue: {exc}")
        return
    poll.errors.extend(f"merge queue: {error}" for error in cycle.errors)


def _follow_main(session: Session, workspace: Workspace, poll: WorkspacePoll) -> None:
    """After the queue: pull what it landed into the server's checkout (`follow_main`)."""
    try:
        status = follow_main(session, workspace)
    except Exception as exc:  # noqa: BLE001 — following main must not stop the tick; reported below
        session.rollback()
        logger.exception("Follow main for %s failed", workspace.slug)
        poll.errors.append(f"follow main: {exc}")
        return
    if status.state in STUCK_STATES:
        poll.errors.append(f"follow main: {status.state.value}: {status.detail}")


def _tick() -> list[WorkspacePoll]:
    with Session(engine) as session:
        return run_ci_poll(session)


async def run_ci_poll_loop(tick_seconds: float) -> None:
    """Poll every `tick_seconds` until cancelled."""
    while True:
        try:
            await asyncio.sleep(tick_seconds)
            await asyncio.to_thread(_tick)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the loop outliving a bad tick is the point
            logger.exception("CI poll tick failed; continuing")


def start_ci_poll_loop(tick_seconds: float | None = None) -> asyncio.Task | None:
    """Start the poller, or return None when CI is off or the tick disables it."""
    if tick_seconds is None:
        tick_seconds = settings.ci_poll_tick_seconds
    if not settings.ci_enabled or tick_seconds <= 0:
        logger.warning(
            "CI poll not started (ci_enabled=%s, tick=%s): failing checks on tickets' "
            "pull requests will not be recorded or fixed",
            settings.ci_enabled,
            tick_seconds,
        )
        return None
    return asyncio.create_task(run_ci_poll_loop(tick_seconds))
