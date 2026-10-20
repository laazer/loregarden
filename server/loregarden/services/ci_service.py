"""CI results for tickets' pull requests, and the auto-fix loop they drive.

One pipeline, fed by the poller (`ci_poll_scheduler`) and by a GitHub webhook
when one can reach this server. Both of them call `observe`:

1. **Record.** One `CIRunResult` per (ticket, head commit), updated as its
   checks settle. When a commit first reads as failing, the failed jobs' logs
   are fetched once and kept on the row.
2. **Settle.** A running `AutoFixAttempt` is closed by the first settled
   result on a *newer* commit (passing → succeeded, failing → failed), or by
   the ticket coming back without one: blocked, abandoned, or done with the
   branch tip unchanged.
3. **Fix.** A settled failure on a `done` ticket reopens it for one repair
   turn (`ci_autofix_dispatch`), at most `ci_retry_limit` times between
   passing results. When that budget is spent the ticket is blocked for a person.
   A tree root's PR is on its integration branch: the root's own branch takes
   that branch first, so the turn fixes what CI ran (`root_integration_sync`).

Every refusal is an `AutoFixOutcome` carrying the reason. On the automatic
path a refusal that someone must act on is also written down as a `REFUSED`
attempt and logged at warning. The expected ones are not: the ticket is still
in flight, or this commit has already had its attempt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path

from loregarden.config import settings
from loregarden.models.domain import (
    AutoFixAttempt,
    AutoFixStatus,
    CIRunResult,
    CIStatus,
    Ticket,
    TicketState,
    Workspace,
)
from loregarden.services.ci_autofix_dispatch import block_for_ci, dispatch_ci_fix
from loregarden.services.ci_checks import (
    OpenPullRequest,
    check_name,
    failing_checks,
    failure_logs,
    rollup,
)
from loregarden.services.git_branch import resolve_ticket_branch
from loregarden.services.git_merge_noco import rev
from loregarden.services.publish_tree import published_branch
from loregarden.services.root_integration_sync import take_integration_branch
from loregarden.services.target_branch import TargetBranchError, integration_branch_for
from loregarden.services.ticket_ids import resolve as resolve_external_id
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

GITHUB_PROVIDER = "github_actions"
#: Attempt states that spent a fix turn, and so count against `ci_retry_limit`.
_CHARGED = (AutoFixStatus.RUNNING, AutoFixStatus.SUCCEEDED, AutoFixStatus.FAILED)
_IN_FLIGHT = (AutoFixStatus.PENDING, AutoFixStatus.RUNNING)
#: The verdicts that close a running attempt when they land on a newer commit.
_SETTLING = {
    CIStatus.PASSING: AutoFixStatus.SUCCEEDED,
    CIStatus.SKIPPED: AutoFixStatus.SUCCEEDED,
    CIStatus.FAILING: AutoFixStatus.FAILED,
}


class AutoFixRefusal(StrEnum):
    """Why no fix turn was dispatched."""

    DISABLED = "disabled"
    NOT_FAILING = "not_failing"
    #: This commit already had its attempt (the automatic path only).
    ALREADY_ATTEMPTED = "already_attempted"
    IN_FLIGHT = "in_flight"
    #: The ticket is not done; its own workflow publishes the next commit.
    TICKET_NOT_DONE = "ticket_not_done"
    BUDGET_SPENT = "budget_spent"
    #: The PR is on neither the ticket's own branch nor the integration branch it roots.
    UNREACHABLE_BRANCH = "unreachable_branch"
    #: A tree root's branch could not take its integration branch before the turn.
    BRANCH_SYNC_FAILED = "branch_sync_failed"
    #: The PR's head is not the local branch tip, so a fix would be built on stale work.
    HEAD_MOVED = "head_moved"
    DISPATCH_FAILED = "dispatch_failed"


#: Refusals that need nobody: the automatic path records nothing for them.
_QUIET = frozenset(
    {
        AutoFixRefusal.DISABLED,
        AutoFixRefusal.NOT_FAILING,
        AutoFixRefusal.ALREADY_ATTEMPTED,
        AutoFixRefusal.IN_FLIGHT,
        AutoFixRefusal.TICKET_NOT_DONE,
    }
)


@dataclass(frozen=True)
class AutoFixOutcome:
    attempt: AutoFixAttempt | None
    refusal: AutoFixRefusal | None = None
    detail: str = ""

    @property
    def dispatched(self) -> bool:
        return self.refusal is None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    # SQLite hands datetimes back naive; they were written as UTC.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class CIService:
    """CI results, attempts, and the fix loop, for one session."""

    def __init__(self, session: Session):
        self.session = session

    # -- Reads ---------------------------------------------------------------

    def latest_result(self, ticket_id: str) -> CIRunResult | None:
        return self.session.exec(
            select(CIRunResult)
            .where(CIRunResult.ticket_id == ticket_id)
            .order_by(col(CIRunResult.updated_at).desc())
        ).first()

    def attempts(self, ticket_id: str) -> list[AutoFixAttempt]:
        """Every attempt on this ticket, oldest first."""
        return list(
            self.session.exec(
                select(AutoFixAttempt)
                .join(CIRunResult, col(CIRunResult.id) == col(AutoFixAttempt.ci_run_result_id))
                .where(CIRunResult.ticket_id == ticket_id)
                .order_by(col(AutoFixAttempt.created_at))
            ).all()
        )

    def _result_for(self, ticket_id: str, head_sha: str) -> CIRunResult | None:
        return self.session.exec(
            select(CIRunResult).where(
                CIRunResult.ticket_id == ticket_id, CIRunResult.head_sha == head_sha
            )
        ).first()

    def _last_pass_at(self, ticket_id: str) -> datetime | None:
        row = self.session.exec(
            select(CIRunResult)
            .where(CIRunResult.ticket_id == ticket_id, CIRunResult.status == CIStatus.PASSING)
            .order_by(col(CIRunResult.updated_at).desc())
        ).first()
        return _as_utc(row.updated_at) if row else None

    def charged_attempts(self, ticket_id: str) -> int:
        """Fix turns spent since CI last passed on this ticket."""
        since = self._last_pass_at(ticket_id)
        return sum(
            1
            for attempt in self.attempts(ticket_id)
            if attempt.status in _CHARGED and (since is None or _as_utc(attempt.created_at) > since)
        )

    # -- Mapping a pull request to its ticket --------------------------------

    def ticket_for_branch(self, workspace: Workspace, branch: str) -> Ticket | None:
        """The ticket whose published branch is ``branch``, or None.

        A ticket's own branch name is stored when it was set by hand; otherwise
        the branch ends in the ticket's id (`loregarden/lg-…`, `integration/lg-…`).
        Either way the match is confirmed against the branch the ticket actually
        publishes, so a PR whose name only happens to end like an id is not
        claimed.
        """
        if not branch:
            return None
        ticket = self.session.exec(
            select(Ticket).where(Ticket.workspace_id == workspace.id, Ticket.branch == branch)
        ).first() or resolve_external_id(
            self.session, branch.rsplit("/", 1)[-1], workspace_id=workspace.id
        )
        if ticket is None:
            return None
        own = resolve_ticket_branch(ticket)
        if branch == own:
            return ticket
        try:
            publishes, _ = published_branch(resolve_workspace_root(workspace), ticket)
        except TargetBranchError:
            return None
        return ticket if branch == publishes else None

    # -- The pipeline --------------------------------------------------------

    def observe(
        self, ticket: Ticket, workspace: Workspace, pr: OpenPullRequest
    ) -> tuple[CIRunResult, AutoFixOutcome | None]:
        """Record what GitHub reports for ``pr`` and act on it. See the module docstring."""
        repo_root = resolve_workspace_root(workspace)
        result = self._record(ticket, workspace, pr, repo_root)
        self.settle_attempts(ticket, result, repo_root=repo_root, branch=pr.branch)
        if result.status is not CIStatus.FAILING:
            return result, None
        outcome = self.trigger_auto_fix(
            ticket, result, branch=pr.branch, repo_root=repo_root, manual=False
        )
        return result, outcome

    def _record(
        self, ticket: Ticket, workspace: Workspace, pr: OpenPullRequest, repo_root: Path
    ) -> CIRunResult:
        result = self._result_for(ticket.id, pr.head_sha) or CIRunResult(
            workspace_id=workspace.id,
            ticket_id=ticket.id,
            provider=GITHUB_PROVIDER,
            head_sha=pr.head_sha,
            pr_number=pr.number,
            logs_url=pr.url,
        )
        status = rollup(pr.checks)
        # A person skipped CI for this commit (`skip_ci_check`); the checks
        # GitHub still reports do not overrule them. A new commit is a new row.
        if result.status is not CIStatus.SKIPPED:
            result.status = status
        if result.status is CIStatus.FAILING and result.failure_summary is None:
            self._attach_failure(result, pr, repo_root)
        result.updated_at = _now()
        self.session.add(result)
        self.session.commit()
        self.session.refresh(result)
        return result

    def _attach_failure(self, result: CIRunResult, pr: OpenPullRequest, repo_root: Path) -> None:
        """Fetched once per failing commit: the logs do not change after the run."""
        names = [check_name(check) for check in failing_checks(pr.checks)]
        logs = failure_logs(repo_root, pr.checks)
        summary = f"{len(names)} failing check(s): {', '.join(names)}"
        if logs.errors:
            summary += ". No log for: " + "; ".join(logs.errors)
            logger.warning(
                "CI logs for %s @ %s incomplete: %s",
                pr.head,
                pr.head_sha[:12],
                "; ".join(logs.errors),
            )
        result.failure_summary = summary
        result.full_logs = logs.text or None

    def settle_attempts(
        self, ticket: Ticket, latest: CIRunResult, *, repo_root: Path, branch: str
    ) -> list[AutoFixAttempt]:
        """Close every running attempt on ``ticket`` that ``latest`` decides."""
        settled: list[AutoFixAttempt] = []
        for attempt in self.attempts(ticket.id):
            if attempt.status not in _IN_FLIGHT:
                continue
            origin = self.session.get(CIRunResult, attempt.ci_run_result_id)
            verdict = self._verdict(ticket, attempt, origin, latest, repo_root, branch)
            if verdict is None:
                continue
            attempt.status, attempt.result_summary = verdict
            attempt.completed_at = _now()
            self.session.add(attempt)
            settled.append(attempt)
            log = logger.info if attempt.status is AutoFixStatus.SUCCEEDED else logger.warning
            log("CI auto-fix %s on %s: %s", attempt.status.value, ticket.external_id, verdict[1])
        if settled:
            self.session.commit()
        return settled

    def _verdict(
        self,
        ticket: Ticket,
        attempt: AutoFixAttempt,
        origin: CIRunResult | None,
        latest: CIRunResult,
        repo_root: Path,
        branch: str,
    ) -> tuple[AutoFixStatus, str] | None:
        failing_sha = origin.head_sha if origin else ""
        if latest.head_sha != failing_sha and latest.status in _SETTLING:
            return (
                _SETTLING[latest.status],
                f"CI {latest.status.value} at {latest.head_sha[:12]} after the fix",
            )
        if ticket.state is TicketState.BLOCKED:
            first = (ticket.blocking_issues or "no reason recorded").strip().splitlines()[0]
            return AutoFixStatus.FAILED, f"the fix turn blocked: {first}"
        if ticket.state is TicketState.WONT_DO:
            return AutoFixStatus.FAILED, "the ticket was abandoned during the fix"
        if (
            ticket.state is TicketState.DONE
            and attempt.status is AutoFixStatus.RUNNING
            and rev(repo_root, f"refs/heads/{branch}") == failing_sha
        ):
            return AutoFixStatus.FAILED, "the fix turn finished without a new commit"
        return None

    def trigger_auto_fix(
        self,
        ticket: Ticket,
        result: CIRunResult,
        *,
        branch: str,
        repo_root: Path,
        manual: bool,
    ) -> AutoFixOutcome:
        """Dispatch one fix turn for ``result``, or say why not.

        ``manual`` is a person asking (the API): it may retry a commit that
        already had its attempt, and its refusals go back to that person rather
        than into the attempt history.
        """
        refusal = self._precheck(ticket, result, manual=manual, branch=branch, repo_root=repo_root)
        if refusal is not None:
            return self._refuse(ticket, result, refusal[0], refusal[1], record=not manual)
        own = resolve_ticket_branch(ticket)
        if branch != own:
            # A tree root: its turn runs on its own branch, so that branch
            # takes what the PR holds first. `publish_tree` folds it back.
            synced = take_integration_branch(repo_root, own=own, integration=branch)
            if not synced.ok:
                return self._refuse(
                    ticket,
                    result,
                    AutoFixRefusal.BRANCH_SYNC_FAILED,
                    synced.detail,
                    record=not manual,
                )

        attempt = self._new_attempt(ticket, result, AutoFixStatus.PENDING, None)
        number, limit = attempt.attempt_number, settings.ci_retry_limit
        reason = (
            f"CI failed on {branch} at {result.head_sha[:12]} ({result.failure_summary}); "
            f"reopened for fix attempt {number} of {limit}."
        )
        dispatch = dispatch_ci_fix(
            self.session,
            ticket,
            brief=_brief(result, branch, own, number, limit),
            reason=reason,
            evidence={
                "pr_number": result.pr_number,
                "head_sha": result.head_sha,
                "attempt": number,
                "ci_run_result_id": result.id,
            },
        )
        if not dispatch.ok:
            attempt.status = AutoFixStatus.REFUSED
            attempt.result_summary = dispatch.detail
            attempt.completed_at = _now()
            self.session.add(attempt)
            self.session.commit()
            logger.warning(
                "CI auto-fix for %s not dispatched: %s", ticket.external_id, dispatch.detail
            )
            return AutoFixOutcome(attempt, AutoFixRefusal.DISPATCH_FAILED, dispatch.detail)
        attempt.status = AutoFixStatus.RUNNING
        self.session.add(attempt)
        self.session.commit()
        return AutoFixOutcome(attempt)

    def _precheck(
        self,
        ticket: Ticket,
        result: CIRunResult,
        *,
        manual: bool,
        branch: str,
        repo_root: Path,
    ) -> tuple[AutoFixRefusal, str] | None:
        if not settings.ci_enabled:
            return AutoFixRefusal.DISABLED, "CI auto-fix is turned off (ci_enabled)"
        if result.status is not CIStatus.FAILING:
            return AutoFixRefusal.NOT_FAILING, f"CI is {result.status.value}, not failing"
        attempts = self.attempts(ticket.id)
        if not manual and any(a.ci_run_result_id == result.id for a in attempts):
            return AutoFixRefusal.ALREADY_ATTEMPTED, "this commit has already had its attempt"
        if any(a.status in _IN_FLIGHT for a in attempts):
            return AutoFixRefusal.IN_FLIGHT, "a fix attempt is already running"
        if ticket.state is not TicketState.DONE:
            return (
                AutoFixRefusal.TICKET_NOT_DONE,
                f"the ticket is {ticket.state.value}; its own workflow publishes the next commit",
            )
        if self.charged_attempts(ticket.id) >= settings.ci_retry_limit:
            return (
                AutoFixRefusal.BUDGET_SPENT,
                f"CI is still failing on {branch} at {result.head_sha[:12]} after "
                f"{settings.ci_retry_limit} fix attempt(s) ({result.failure_summary}). "
                "Fix it on the branch and requeue.",
            )
        reachable = _reachable_branches(ticket)
        if branch not in reachable:
            return (
                AutoFixRefusal.UNREACHABLE_BRANCH,
                f"{branch} is not a branch a repair turn on this ticket can reach "
                f"({' or '.join(reachable)}). Fix it on the branch by hand.",
            )
        tip = rev(repo_root, f"refs/heads/{branch}")
        if tip != result.head_sha:
            return (
                AutoFixRefusal.HEAD_MOVED,
                f"the pull request's head {result.head_sha[:12]} is not the local {branch} "
                f"({tip[:12] or 'missing'}); a fix built here would not apply to it",
            )
        return None

    def _refuse(
        self,
        ticket: Ticket,
        result: CIRunResult,
        refusal: AutoFixRefusal,
        detail: str,
        *,
        record: bool,
    ) -> AutoFixOutcome:
        if not record or refusal in _QUIET:
            return AutoFixOutcome(None, refusal, detail)
        attempt = self._new_attempt(ticket, result, AutoFixStatus.REFUSED, detail)
        logger.warning("CI auto-fix for %s refused: %s", ticket.external_id, detail)
        if refusal is AutoFixRefusal.BUDGET_SPENT:
            blocked = block_for_ci(self.session, ticket, detail)
            if not blocked.ok:
                logger.warning(
                    "Could not block %s for its failing CI: %s",
                    ticket.external_id,
                    blocked.detail,
                )
        return AutoFixOutcome(attempt, refusal, detail)

    def _new_attempt(
        self,
        ticket: Ticket,
        result: CIRunResult,
        status: AutoFixStatus,
        summary: str | None,
    ) -> AutoFixAttempt:
        attempt = AutoFixAttempt(
            ci_run_result_id=result.id,
            attempt_number=self.charged_attempts(ticket.id) + 1,
            status=status,
            result_summary=summary,
            completed_at=_now() if status is AutoFixStatus.REFUSED else None,
        )
        self.session.add(attempt)
        self.session.commit()
        self.session.refresh(attempt)
        return attempt

    # -- Operator actions ----------------------------------------------------

    def trigger_latest(self, ticket: Ticket) -> AutoFixOutcome:
        """A person asking for a fix turn on the ticket's latest CI result."""
        result = self.latest_result(ticket.id)
        if result is None:
            return AutoFixOutcome(None, AutoFixRefusal.NOT_FAILING, "no CI result for this ticket")
        workspace = self.session.get(Workspace, ticket.workspace_id)
        if workspace is None:
            return AutoFixOutcome(
                None, AutoFixRefusal.DISPATCH_FAILED, "the ticket has no workspace"
            )
        repo_root = resolve_workspace_root(workspace)
        try:
            branch, _ = published_branch(repo_root, ticket)
        except TargetBranchError as exc:
            return AutoFixOutcome(None, AutoFixRefusal.UNREACHABLE_BRANCH, str(exc))
        return self.trigger_auto_fix(
            ticket, result, branch=branch, repo_root=repo_root, manual=True
        )

    def skip_ci_check(self, ticket_id: str) -> CIRunResult | None:
        """Mark the latest result SKIPPED: a person decided this commit's CI does not gate it."""
        result = self.latest_result(ticket_id)
        if result is None:
            return None
        result.status = CIStatus.SKIPPED
        result.updated_at = _now()
        self.session.add(result)
        self.session.commit()
        logger.warning("CI check skipped by hand for ticket %s at %s", ticket_id, result.head_sha)
        return result

    def prune_logs(self, now: datetime | None = None) -> int:
        """Drop stored logs older than `ci_log_retention_days`. Returns rows pruned."""
        cutoff = (now or _now()) - timedelta(days=settings.ci_log_retention_days)
        rows = self.session.exec(
            select(CIRunResult).where(
                col(CIRunResult.full_logs).is_not(None), col(CIRunResult.updated_at) < cutoff
            )
        ).all()
        for row in rows:
            row.full_logs = None
            self.session.add(row)
        if rows:
            self.session.commit()
        return len(rows)


def _reachable_branches(ticket: Ticket) -> tuple[str, ...]:
    """The PR branches a repair turn on ``ticket`` can commit to: its own, and the
    integration branch named after it when it roots a tree."""
    own = resolve_ticket_branch(ticket)
    try:
        return own, integration_branch_for(ticket)
    except TargetBranchError:
        return (own,)


def _brief(result: CIRunResult, branch: str, own: str, attempt: int, limit: int) -> str:
    logs = result.full_logs or "(no log could be read; open the checks on the pull request)"
    tree = (
        f"'{branch}' is this tree's integration branch: every child's work, landed. It has "
        f"been merged into this ticket's branch '{own}', so the failing code is here. The "
        "cause may be in any child's work or in how two of them combine. "
        if branch != own
        else ""
    )
    return (
        f"This ticket was done, but CI failed on its pull request #{result.pr_number} "
        f"({result.logs_url}) at {result.head_sha[:12]} on '{branch}'. "
        f"{result.failure_summary}.\n\n"
        f"{tree}"
        f"You are CI fix attempt {attempt} of {limit}. Reproduce the failure locally, fix "
        "its cause on this branch with the smallest change that does it, commit, and report "
        f"`pass`. Landing pushes your commit to '{branch}' and CI runs again. Do not "
        "weaken, skip or delete a check to make it pass. If the failure is not something "
        "this branch caused (an outage, a flaky runner, a secret it cannot see), report "
        "`blocked` and say so.\n\n"
        f"Failed job logs (tail):\n\n{logs}"
    )
