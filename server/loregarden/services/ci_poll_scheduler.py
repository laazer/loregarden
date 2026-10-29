"""Background CI poll: each workspace's open pull requests, into `CIService.observe`.

One `gh pr list` per workspace per tick (`settings.ci_poll_tick_seconds`).
Each open PR whose branch a ticket publishes is observed. A PR no ticket owns
(one opened by hand from a session branch, say) is not ours and is passed over.

Nothing here is silent. A workspace whose PRs could not be listed is logged at
warning (once per distinct error) and reported in the tick's result, and so is
a ticket whose observation raised. The loop outlives a bad tick, as the GitHub sync loop does.

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
from loregarden.services.github_pr_listing import GitHubReadError
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session

logger = logging.getLogger(__name__)


#: The last listing failure per workspace. A repository `gh` cannot see fails
#: the same way every tick; it is logged when it starts, when it changes, and
#: when it clears, not every two minutes.
_last_list_error: dict[str, str] = {}


def _report_list_failure(slug: str, error: str) -> None:
    if _last_list_error.get(slug) == error:
        return
    _last_list_error[slug] = error
    logger.warning(
        "CI poll for %s: could not list pull requests (repeats suppressed until it changes): %s",
        slug,
        error,
    )


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
        _report_list_failure(workspace.slug, str(exc))
        return poll
    if listing.state is PrListingState.COMPLETE:
        if _last_list_error.pop(workspace.slug, None) is not None:
            logger.warning("CI poll for %s: listing pull requests works again", workspace.slug)
    else:
        # The PRs that were read are still observed; the ones past the last
        # page are not, and that is reported rather than read as "no more PRs".
        partial = (
            f"GitHub reported more open pull requests after the last page; "
            f"only {len(listing.pull_requests)} were observed"
        )
        poll.errors.append(f"could not list pull requests: {partial}")
        _report_list_failure(workspace.slug, partial)
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
    """One tick: every active workspace, then the log-retention prune."""
    polls = [poll_workspace(session, workspace) for workspace in active_workspaces(session)]
    CIService(session).prune_logs()
    return polls


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
