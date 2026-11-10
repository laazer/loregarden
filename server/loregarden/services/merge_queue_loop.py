"""The merge queue loop: stage the merged result, wait for its checks, land exactly that tree.

lg-milestone-that-969. Advanced once per workspace on the CI poll tick
(`ci_poll_scheduler`). For the head entry, in order:

1. **Stage.** Fetch the base from origin and cut the candidate merged onto its
   tip with no checkout (`git_merge_noco.squash_onto`: `merge-tree` then
   `commit-tree`, one commit whose only parent is the base tip). A conflict
   fails the entry with git's words and the conflicted paths, files a ticket a
   person resolves it through, and the next entry runs. A merge git cannot
   compute at all is *undetermined*, never "no conflicts".
2. **Publish the staging ref.** Push the staged commit as the PR's head
   (``publish/<branch>``). Not bors' `staging` branch, though 793 gave that
   CI contexts: two of the four checks the base requires, `PR UX section` and
   `PR fix coverage`, run only on `pull_request` events, so the PR head is the
   one ref where every required check runs on the staged commit.
3. **Wait.** Read the PR from GitHub: the required contexts come from the
   base's own rules, and each must have passed *on the staged commit*. A
   required check that has not reported, one still running, and GitHub not
   answering are three different waits (`MergeQueueWait`). A wait past
   `merge_queue_check_timeout_seconds` is `TIMED_OUT`, with the elapsed time.
4. **Land, under the tree-equality assertion.** Fetch the base again. If it
   moved since staging the staged commit is discarded and the entry restages:
   the tested tree is no longer the tree that would land. Otherwise, before
   anything moves, GitHub's own head tree must equal the staged tree and
   GitHub's base must be the tip it was cut on — then GitHub squash-merges a
   head with one commit on the base tip, whose tree is the staged tree, and the
   ruleset's strict up-to-date policy refuses it if the base moved in between.
   The merge is pinned to the staged head (`--match-head-commit`). Afterwards
   the merge commit's tree is read back and must equal the staged tree; a
   mismatch is reported with both oids, never logged and continued.
5. **Clean up.** The staging ref is deleted (GitHub's delete-on-merge usually
   has); a cleanup that cannot finish is reported, and the entry still reads
   merged because GitHub said so.

**A pull request admitted by number** (no ticket; `merge_queue_pull_heads`)
stages onto its author's own head branch. A head there that the queue did not
push is the author's newer work: a testing entry restages as for any moved
head, and staging (`_authors_head`) adopts that work instead of pushing the old
head over it. When such an entry leaves the queue unmerged, the reason is also
posted on the PR, since no ticket carries it.

**Direction is fixed.** Every merge here puts the candidate onto the base:
`_stage` takes the base tip as the parent and the candidate as the content.
Nothing here writes to a tree's integration branch; a PR admitted by number has
no other branch, so its staged commit replaces its head, leased on what the
author last left there.

**Cancellability** is declared per step in `STEPS` and justified by
idempotence: cutting a commit or reading checks only recomputes a decision, so
a newer cycle supersedes it; pushing, merging and deleting mutate shared
state, so once begun they are not cancellable. An operator's withdraw is
refused while an entry is merging for that reason (`merge_queue.withdraw`).

**Withdrawn under the loop.** Every state change is a compare-and-swap
(`merge_queue.transition`). An entry an operator withdrew, or a new publish
re-pointed, between this cycle's read and its write simply does not match;
the loop reports it as `EntryGone` and moves on.

Nothing here returns False, [] or {} for a failure it would also return on
success.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from pathlib import Path

from loregarden.config import settings
from loregarden.models.domain import (
    MergeQueueEntry,
    MergeQueueState,
    MergeQueueWait,
    PullRequestCheckOutcome,
    PullRequestState,
    Workspace,
    comparable_utc,
    utcnow,
)
from loregarden.services.git_automation import push_commit_as
from loregarden.services.git_merge_noco import MergeOutcome, squash_onto
from loregarden.services.git_subprocess import run_gh, run_git
from loregarden.services.github_pr_listing import GitHubReadError
from loregarden.services.merge_preservation import target_preserved
from loregarden.services.merge_queue import (
    entry_ticket,
    head_entry,
    live_entries,
    retry,
    transition,
)
from loregarden.services.merge_queue_github import (
    GhQueueGitHub,
    QueueGitHub,
    StagedPullRequest,
    read_required,
    unreported,
)
from loregarden.services.merge_queue_landed_tickets import close_landed_tickets
from loregarden.services.merge_queue_pull_heads import (
    author_owns_head,
    drop_pull_pin,
    fetch_pull_head,
)
from loregarden.services.merge_queue_reports import (
    artifact_open,
    close_card,
    file_conflict_artifact,
    report_still_blocked,
    report_stopped,
)
from loregarden.services.pull_request_merge import MergeFailed
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)


class MergeVerdict(StrEnum):
    """A merge check's three answers. Undetermined is never read as clean."""

    CLEAN = "clean"
    CONFLICTED = "conflicted"
    UNDETERMINED = "undetermined"


class TreeMismatch(RuntimeError):
    """The tree about to land, or that landed, is not the tree the checks passed on."""

    def __init__(self, what: str, staged_tree: str, other_tree: str) -> None:
        self.staged_tree = staged_tree
        self.other_tree = other_tree
        super().__init__(f"{what}: staged tree {staged_tree} != {other_tree}")


@dataclass
class CycleReport:
    """One workspace's cycle: what each entry it touched did."""

    slug: str
    actions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Staged:
    verdict: MergeVerdict
    sha: str = ""
    tree: str = ""
    base_sha: str = ""
    detail: str = ""
    conflicted_files: tuple[str, ...] = ()


def merge_verdict(outcome: MergeOutcome) -> MergeVerdict:
    """What a `MergeOutcome` says about the merge, in three words, not two."""
    if outcome.ok:
        return MergeVerdict.CLEAN
    if outcome.conflicted:
        return MergeVerdict.CONFLICTED
    return MergeVerdict.UNDETERMINED


#: How many entries one cycle moves through when each ends at once (a conflict
#: or a refusal), so a run of dead candidates does not cost a tick apiece.
_ENTRIES_PER_CYCLE = 4


class MergeQueueLoop:
    def __init__(
        self, session: Session, workspace: Workspace, github: QueueGitHub | None = None
    ) -> None:
        self.session = session
        self.workspace = workspace
        self.repo_root = resolve_workspace_root(workspace)
        self.github = github if github is not None else GhQueueGitHub(self.repo_root)
        self.report = CycleReport(slug=workspace.slug)

    # -- the cycle ----------------------------------------------------------

    def advance(self) -> CycleReport:
        self._remind_blocked()
        seen: set[str] = set()
        for _ in range(_ENTRIES_PER_CYCLE):
            entry = head_entry(self.session, self.workspace.id)
            if entry is None or entry.id in seen:
                break
            seen.add(entry.id)
            self._mark_behind(entry)
            if not self._step(entry):
                break
        return self.report

    def _step(self, entry: MergeQueueEntry) -> bool:
        """Advance ``entry`` one step. True when it left the queue and the next may run."""
        state = entry.state
        if state in (MergeQueueState.QUEUED, MergeQueueState.STAGING):
            return self._stage(entry, state)
        if state is MergeQueueState.TESTING:
            return self._wait(entry)
        if state is MergeQueueState.MERGING:
            return self._resume_merging(entry)
        return True

    def _mark_behind(self, head: MergeQueueEntry) -> None:
        for entry in live_entries(self.session, self.workspace.id):
            if entry.id != head.id and entry.state is MergeQueueState.QUEUED:
                if entry.wait is not MergeQueueWait.BEHIND:
                    # A label only: an entry withdrawn meanwhile matches nothing,
                    # and there is nothing to report about not labelling it.
                    transition(
                        self.session,
                        entry,
                        MergeQueueState.QUEUED,
                        wait=MergeQueueWait.BEHIND,
                        detail=f"behind PR #{head.pr_number}",
                    )

    def _remind_blocked(self) -> None:
        """Conflict tickets still open are re-reported at notice level; done ones retry."""
        blocked = self.session.exec(
            select(MergeQueueEntry)
            .where(MergeQueueEntry.workspace_id == self.workspace.id)
            .where(MergeQueueEntry.state == MergeQueueState.FAILED)
            .where(MergeQueueEntry.wait == MergeQueueWait.CONFLICT_ARTIFACT_OPEN)
            .order_by(col(MergeQueueEntry.position))
        ).all()
        for entry in blocked:
            still_open = artifact_open(self.session, entry)
            if still_open:
                report_still_blocked(entry)
                self.report.actions.append(
                    f"PR #{entry.pr_number}: blocked on {entry.artifact_ref}"
                )
                continue
            try:
                retry(self.session, entry.id)
            except (LookupError, ValueError) as exc:
                self.report.errors.append(
                    f"PR #{entry.pr_number}: retry after {entry.artifact_ref}: {exc}"
                )
                logger.warning("Merge queue: could not retry PR #%s: %s", entry.pr_number, exc)
                continue
            close_card(self.session, entry, reason=f"{entry.artifact_ref} resolved; retried")
            self.report.actions.append(f"PR #{entry.pr_number}: {entry.artifact_ref} done; retried")

    # -- step 1 and 2: stage and publish ------------------------------------

    def _begin_staging(self, entry: MergeQueueEntry, expected: MergeQueueState) -> bool | None:
        """Mark the entry staging; None to go on, else the cycle's result for it."""
        if expected is MergeQueueState.QUEUED and not transition(
            self.session,
            entry,
            MergeQueueState.QUEUED,
            state=MergeQueueState.STAGING,
            wait=MergeQueueWait.NONE,
            detail="staging",
        ):
            return self._gone(entry, "before staging")
        if entry.attempts >= settings.merge_queue_max_attempts:
            return self._stop(
                entry,
                MergeQueueState.STAGING,
                MergeQueueState.FAILED,
                detail=f"staged {entry.attempts} times without landing: the base keeps moving "
                "under it or its push keeps failing; retry once it settles",
            )
        return None

    def _stage(self, entry: MergeQueueEntry, expected: MergeQueueState) -> bool:
        begun = self._begin_staging(entry, expected)
        if begun is not None:
            return begun
        message = self._staged_message(entry)
        if message is None:
            return False
        staged = self.cut(entry, message)
        if staged.verdict is MergeVerdict.CONFLICTED:
            return self._conflicted(entry, staged)
        if staged.verdict is MergeVerdict.UNDETERMINED:
            return self._stop(
                entry,
                MergeQueueState.STAGING,
                MergeQueueState.FAILED,
                detail=f"whether it merges onto {entry.base_branch} could not be determined: "
                f"{staged.detail}",
            )
        lease: str | None = None
        if author_owns_head(entry):
            held = self._authors_head(entry)
            if held is None:
                return False
            lease = held
        pushed = push_commit_as(
            self.repo_root, entry.pr_head_branch, staged.sha, base=entry.base_branch, lease=lease
        )
        if not pushed.ok:
            # Transient as a rule (the network, a lease another publish just
            # took); counted, so a push that never succeeds ends in FAILED.
            self.report.errors.append(f"PR #{entry.pr_number}: push failed: {pushed.detail}")
            logger.warning("Merge queue: PR #%s push failed: %s", entry.pr_number, pushed.detail)
            return self._hold(
                entry,
                MergeQueueState.STAGING,
                when="after its push failed",
                state=MergeQueueState.QUEUED,
                attempts=entry.attempts + 1,
                detail=f"push of the staged commit failed, staging again: {pushed.detail}",
            )
        if not transition(
            self.session,
            entry,
            MergeQueueState.STAGING,
            state=MergeQueueState.TESTING,
            wait=MergeQueueWait.CHECKS_UNREPORTED,
            staged_sha=staged.sha,
            pushed_sha=staged.sha,
            staged_tree=staged.tree,
            staged_base_sha=staged.base_sha,
            attempts=entry.attempts + 1,
            testing_started_at=utcnow(),
            checks_json="[]",
            detail=f"staged {staged.sha[:12]} on {entry.base_branch} {staged.base_sha[:12]}; "
            "waiting for its checks",
        ):
            return self._gone(entry, "after its staged commit was pushed")
        self.report.actions.append(f"PR #{entry.pr_number}: staged {staged.sha[:12]}")
        return False

    def _authors_head(self, entry: MergeQueueEntry) -> str | None:
        """What the PR head holds now, when it is safe to push over; None when it is not.

        Safe means it is the work this entry carries or the commit the queue
        last pushed. Anything else is a push its author made while the entry
        waited: the entry adopts it and stages again next cycle, and the push
        leases on the value read here so one landing in between is refused.
        """
        held, failure = fetch_pull_head(self.repo_root, entry.pr_number)
        if failure:
            self.report.errors.append(f"PR #{entry.pr_number}: {failure}")
            self._hold(
                entry,
                MergeQueueState.STAGING,
                when="while its head could not be fetched",
                state=MergeQueueState.QUEUED,
                attempts=entry.attempts + 1,
                detail=f"could not read PR #{entry.pr_number}'s head before staging: {failure}",
            )
            return None
        if held in (entry.head_sha, entry.pushed_sha):
            return held
        if transition(
            self.session,
            entry,
            MergeQueueState.STAGING,
            state=MergeQueueState.QUEUED,
            wait=MergeQueueWait.NONE,
            head_sha=held,
            detail=f"its author pushed {held[:12]} while it waited; staging that instead",
        ):
            logger.info("Merge queue: PR #%s adopted its author's push %s", entry.pr_number, held)
            self.report.actions.append(f"PR #{entry.pr_number}: adopted {held[:12]}")
        else:
            self._gone(entry, "while adopting its author's push")
        return None

    def _staged_message(self, entry: MergeQueueEntry) -> str | None:
        """The staged commit's message; None when the PR could not be read (held).

        GitHub names a one-commit squash after that commit, so this is what
        lands on the base. A tree entry is named after its ticket. A pull
        request admitted by number keeps its author's title and body, whose
        `Ticket:` lines are how the tickets it lands are found again.
        """
        ticket = entry_ticket(self.session, entry)
        if ticket is not None:
            return f"{ticket.external_id}: {ticket.title}"
        try:
            pull = self.github.read_pull_request(entry.pr_number)
        except GitHubReadError as exc:
            self.report.errors.append(f"PR #{entry.pr_number}: {exc}")
            self._hold(
                entry,
                MergeQueueState.STAGING,
                when="while its title could not be read",
                state=MergeQueueState.QUEUED,
                attempts=entry.attempts + 1,
                detail=f"could not read PR #{entry.pr_number} before staging: {exc}",
            )
            return None
        title = pull.title.strip() or f"PR #{entry.pr_number}"
        body = pull.body.strip()
        return f"{title}\n\n{body}" if body else title

    def cut(self, entry: MergeQueueEntry, message: str) -> Staged:
        """The candidate merged onto the base's fetched tip. The base is always the parent."""
        base_sha, failure = fetch_base(self.repo_root, entry.base_branch)
        if failure:
            return Staged(MergeVerdict.UNDETERMINED, detail=failure)
        if not _is_commit(self.repo_root, entry.head_sha):
            return Staged(
                MergeVerdict.UNDETERMINED,
                detail=f"{entry.head_sha[:12]} is not a commit in {self.repo_root}",
            )
        outcome = squash_onto(self.repo_root, base_sha, entry.head_sha, message=message)
        verdict = merge_verdict(outcome)
        if verdict is not MergeVerdict.CLEAN:
            return Staged(
                verdict,
                base_sha=base_sha,
                detail=outcome.detail,
                conflicted_files=outcome.conflicted_files,
            )
        try:
            kept = target_preserved(
                self.repo_root,
                target_sha=base_sha,
                source_sha=entry.head_sha,
                result_sha=outcome.sha,
            )
        except RuntimeError as exc:
            return Staged(MergeVerdict.UNDETERMINED, base_sha=base_sha, detail=str(exc))
        if not kept.ok:
            return Staged(MergeVerdict.UNDETERMINED, base_sha=base_sha, detail=kept.detail)
        return Staged(
            MergeVerdict.CLEAN,
            sha=outcome.sha,
            tree=tree_of(self.repo_root, outcome.sha),
            base_sha=base_sha,
        )

    def _conflicted(self, entry: MergeQueueEntry, staged: Staged) -> bool:
        files = json.dumps(list(staged.conflicted_files))
        detail = (
            f"conflicts with {entry.base_branch} {staged.base_sha[:12]} in "
            f"{', '.join(staged.conflicted_files)}\n\n{staged.detail}"
        )
        if not transition(
            self.session,
            entry,
            MergeQueueState.STAGING,
            state=MergeQueueState.FAILED,
            wait=MergeQueueWait.NONE,
            conflicted_files_json=files,
            detail=detail,
        ):
            return self._gone(entry, "while recording its conflict")
        self.session.refresh(entry)
        ticket = entry_ticket(self.session, entry)
        artifact = file_conflict_artifact(self.session, entry, ticket) if ticket else None
        # Blocked-on-an-artifact only once one exists: `_remind_blocked` retries
        # an entry whose artifact is closed, and "none was filed" is not closed.
        if artifact is not None and transition(
            self.session,
            entry,
            MergeQueueState.FAILED,
            wait=MergeQueueWait.CONFLICT_ARTIFACT_OPEN,
            artifact_ref=artifact.external_id,
        ):
            self.session.refresh(entry)
        report_stopped(self.session, entry)
        self._tell_author(entry)
        self.report.actions.append(f"PR #{entry.pr_number}: conflicted; {entry.artifact_ref}")
        return True

    # -- step 3: wait -------------------------------------------------------

    def _wait(self, entry: MergeQueueEntry) -> bool:
        started = entry.testing_started_at or entry.state_changed_at
        elapsed = utcnow() - comparable_utc(started)
        limit = timedelta(seconds=settings.merge_queue_check_timeout_seconds)
        if elapsed > limit:
            return self._stop(
                entry,
                MergeQueueState.TESTING,
                MergeQueueState.TIMED_OUT,
                detail=f"checks on {entry.staged_sha[:12]} did not finish within "
                f"{int(limit.total_seconds() // 60)} minutes "
                f"(waited {int(elapsed.total_seconds() // 60)}m{int(elapsed.total_seconds() % 60)}s)",
            )
        try:
            pr = self.github.read_pull_request(entry.pr_number)
            required = self.github.required_contexts(entry.base_branch)
        except GitHubReadError as exc:
            self.report.errors.append(f"PR #{entry.pr_number}: {exc}")
            return self._hold(
                entry,
                MergeQueueState.TESTING,
                when="while GitHub could not be asked",
                wait=MergeQueueWait.GITHUB_UNREACHABLE,
                detail=f"GitHub could not be asked about PR #{entry.pr_number}: {exc}",
            )
        if pr.state is not PullRequestState.OPEN:
            return self._closed_outside(entry, pr)
        if pr.head_sha != entry.staged_sha:
            return self._restage(
                entry,
                f"PR #{entry.pr_number}'s head is {pr.head_sha[:12]}, not the staged "
                f"{entry.staged_sha[:12]}",
            )
        readings = read_required(pr.checks, required)
        checks_json = json.dumps([reading.model_dump(mode="json") for reading in readings])
        failing = [r for r in readings if r.outcome is PullRequestCheckOutcome.FAILING]
        if failing:
            log = self.github.failure_log(pr.checks)
            return self._stop(
                entry,
                MergeQueueState.TESTING,
                MergeQueueState.FAILED,
                detail=f"required check(s) failed on {entry.staged_sha[:12]}: "
                f"{', '.join(r.name for r in failing)}",
                checks_json=checks_json,
                log_tail=log,
            )
        missing = unreported(pr.checks, required)
        if missing:
            return self._hold(
                entry,
                MergeQueueState.TESTING,
                when="while its checks were unreported",
                wait=MergeQueueWait.CHECKS_UNREPORTED,
                checks_json=checks_json,
                detail=f"waiting for {', '.join(missing)} to report on {entry.staged_sha[:12]}",
            )
        running = [r.name for r in readings if r.outcome is PullRequestCheckOutcome.PENDING]
        if running:
            return self._hold(
                entry,
                MergeQueueState.TESTING,
                when="while its checks ran",
                wait=MergeQueueWait.CHECKS_RUNNING,
                checks_json=checks_json,
                detail=f"{', '.join(running)} still running on {entry.staged_sha[:12]}",
            )
        return self._land(entry, pr, checks_json)

    # -- step 4 and 5: land and clean up -------------------------------------

    def _land(self, entry: MergeQueueEntry, pr: StagedPullRequest, checks_json: str) -> bool:
        base_sha, failure = fetch_base(self.repo_root, entry.base_branch)
        if failure:
            return self._hold(
                entry,
                MergeQueueState.TESTING,
                when="while the base could not be fetched",
                wait=MergeQueueWait.GITHUB_UNREACHABLE,
                checks_json=checks_json,
                detail=f"checks passed; {failure}",
            )
        if base_sha != entry.staged_base_sha:
            return self._restage(
                entry,
                f"{entry.base_branch} moved from {entry.staged_base_sha[:12]} to "
                f"{base_sha[:12]} after staging; the tested tree is no longer what would land",
            )
        if not transition(
            self.session,
            entry,
            MergeQueueState.TESTING,
            state=MergeQueueState.MERGING,
            wait=MergeQueueWait.NONE,
            checks_json=checks_json,
            detail=f"required checks passed on {entry.staged_sha[:12]}; merging",
        ):
            return self._gone(entry, "as it was about to merge")
        self.session.refresh(entry)
        try:
            assert_lands_staged_tree(self.repo_root, entry, pr)
        except TreeMismatch as exc:
            return self._stop(
                entry,
                MergeQueueState.MERGING,
                MergeQueueState.FAILED,
                detail=f"refused to merge: {exc}",
            )
        try:
            self.github.merge(entry.pr_number, entry.staged_sha)
        except MergeFailed as exc:
            return self._merge_refused(entry, str(exc))
        return self._confirm_merged(entry)

    def _merge_refused(self, entry: MergeQueueEntry, why: str) -> bool:
        """GitHub would not merge. A moved base restages; anything else stops the entry."""
        base_sha, failure = fetch_base(self.repo_root, entry.base_branch)
        if not failure and base_sha != entry.staged_base_sha:
            if not self._relabel(entry, MergeQueueState.MERGING, MergeQueueState.TESTING):
                return self._gone(entry, "after GitHub refused the merge")
            return self._restage(entry, f"GitHub refused the merge as the base moved: {why}")
        return self._stop(
            entry,
            MergeQueueState.MERGING,
            MergeQueueState.FAILED,
            detail=f"GitHub refused to merge {entry.staged_sha[:12]}: {why}",
        )

    def _confirm_merged(self, entry: MergeQueueEntry) -> bool:
        try:
            merged = self.github.read_pull_request(entry.pr_number)
        except GitHubReadError as exc:
            # The merge was accepted; whether it landed is unknown until GitHub
            # answers. Stay MERGING — `_resume_merging` asks again next cycle.
            self.report.errors.append(f"PR #{entry.pr_number}: {exc}")
            return self._hold(
                entry,
                MergeQueueState.MERGING,
                when="while its merge was unconfirmed",
                wait=MergeQueueWait.GITHUB_UNREACHABLE,
                detail=f"merge requested; GitHub could not confirm it yet: {exc}",
            )
        if merged.state is not PullRequestState.MERGED:
            return self._stop(
                entry,
                MergeQueueState.MERGING,
                MergeQueueState.FAILED,
                detail=f"GitHub accepted the merge but reports PR #{entry.pr_number} "
                f"{merged.state.value}",
            )
        if merged.merge_tree != entry.staged_tree:
            # Main has moved and cannot be unmoved; this is the alarm, with both oids.
            return self._stop(
                entry,
                MergeQueueState.MERGING,
                MergeQueueState.FAILED,
                merge_commit=merged.merge_commit,
                landed_tree=merged.merge_tree,
                detail=f"LANDED A DIFFERENT TREE: merge commit {merged.merge_commit} has tree "
                f"{merged.merge_tree}, the checks passed on {entry.staged_tree}",
            )
        cleanup = delete_staging_ref(self.repo_root, entry.pr_head_branch)
        if author_owns_head(entry):
            unpinned = drop_pull_pin(self.repo_root, entry.pr_number)
            cleanup = "; ".join(part for part in (cleanup, unpinned) if part)
        detail = f"merged as {merged.merge_commit[:12]}; landed tree equals the tested tree"
        if cleanup:
            detail += f" (cleanup: {cleanup})"
            logger.warning("Merge queue: PR #%s cleanup: %s", entry.pr_number, cleanup)
        if not transition(
            self.session,
            entry,
            MergeQueueState.MERGING,
            state=MergeQueueState.MERGED,
            wait=MergeQueueWait.NONE,
            merge_commit=merged.merge_commit,
            landed_tree=merged.merge_tree,
            detail=detail,
        ):
            return self._gone(entry, f"after it merged as {merged.merge_commit[:12]}")
        self.session.refresh(entry)
        close_card(self.session, entry, reason="merged")
        logger.info(
            "Merge queue %s: PR #%s merged as %s (tree %s)",
            self.workspace.slug,
            entry.pr_number,
            merged.merge_commit[:12],
            merged.merge_tree[:12],
        )
        self.report.actions.append(f"PR #{entry.pr_number}: merged {merged.merge_commit[:12]}")
        if author_owns_head(entry):
            self._close_named_tickets(entry, merged)
        return True

    def _close_named_tickets(self, entry: MergeQueueEntry, merged: StagedPullRequest) -> None:
        """Close the tickets a landed PR's body names; no ticket on the entry carries them."""
        landed = close_landed_tickets(
            self.session, entry, body=merged.body, merge_commit=merged.merge_commit
        )
        if landed.closed:
            self.report.actions.append(f"PR #{entry.pr_number}: closed {', '.join(landed.closed)}")
        if landed.left_to_rollup:
            self.report.actions.append(
                f"PR #{entry.pr_number}: left {', '.join(landed.left_to_rollup)} to its rollup"
            )
        self.report.errors.extend(
            f"PR #{entry.pr_number} landed; ticket {problem}" for problem in landed.problems
        )

    def _resume_merging(self, entry: MergeQueueEntry) -> bool:
        """An entry left MERGING by a crash or an unanswered confirmation."""
        try:
            pr = self.github.read_pull_request(entry.pr_number)
        except GitHubReadError as exc:
            self.report.errors.append(f"PR #{entry.pr_number}: {exc}")
            return self._hold(
                entry,
                MergeQueueState.MERGING,
                when="while GitHub could not be asked",
                wait=MergeQueueWait.GITHUB_UNREACHABLE,
                detail=f"merging; GitHub could not be asked whether it landed: {exc}",
            )
        if pr.state is PullRequestState.MERGED:
            return self._confirm_merged(entry)
        if not self._relabel(entry, MergeQueueState.MERGING, MergeQueueState.TESTING):
            return self._gone(entry, "while resuming its merge")
        return self._wait(entry)

    # -- endings --------------------------------------------------------------

    def _restage(self, entry: MergeQueueEntry, why: str) -> bool:
        """Discard the staged commit; the entry stages again on the current tip."""
        if not transition(
            self.session,
            entry,
            MergeQueueState.TESTING,
            state=MergeQueueState.QUEUED,
            wait=MergeQueueWait.BASE_MOVED,
            staged_sha="",
            staged_tree="",
            staged_base_sha="",
            testing_started_at=None,
            detail=f"restaging: {why}",
        ):
            return self._gone(entry, "while restaging")
        logger.info("Merge queue: PR #%s restaging: %s", entry.pr_number, why)
        self.report.actions.append(f"PR #{entry.pr_number}: restaging")
        return False

    def _closed_outside(self, entry: MergeQueueEntry, pr: StagedPullRequest) -> bool:
        if pr.state is PullRequestState.MERGED and pr.merge_tree == entry.staged_tree:
            if not self._relabel(entry, MergeQueueState.TESTING, MergeQueueState.MERGING):
                return self._gone(entry, "after it merged outside the queue")
            return self._confirm_merged(entry)
        return self._stop(
            entry,
            MergeQueueState.TESTING,
            MergeQueueState.FAILED,
            detail=f"PR #{entry.pr_number} was {pr.state.value} outside the queue",
            merge_commit=pr.merge_commit,
            landed_tree=pr.merge_tree,
        )

    def _stop(
        self,
        entry: MergeQueueEntry,
        expected: MergeQueueState,
        state: MergeQueueState,
        *,
        detail: str,
        **fields: str,
    ) -> bool:
        if not transition(
            self.session,
            entry,
            expected,
            state=state,
            wait=MergeQueueWait.NONE,
            detail=detail,
            **fields,
        ):
            return self._gone(entry, f"before it could be marked {state.value}")
        self.session.refresh(entry)
        report_stopped(self.session, entry)
        self._tell_author(entry)
        self.report.actions.append(f"PR #{entry.pr_number}: {state.value}")
        return True

    def _tell_author(self, entry: MergeQueueEntry) -> None:
        """Post why a PR admitted by number left the queue on the PR itself.

        A tree's reason lives on its ticket; this PR has none, and its author
        reads the PR. The inbox card `report_stopped` filed is the durable
        record either way, so a comment that cannot be posted is reported on
        the cycle and logged, not retried.
        """
        if not author_owns_head(entry):
            return
        body = (
            f"The merge queue stopped PR #{entry.pr_number} ({entry.state.value}).\n\n"
            f"{entry.detail}"
        )
        if entry.log_tail:
            body += f"\n\nFailing log tail:\n\n```\n{entry.log_tail[-4000:]}\n```"
        body += (
            "\n\nFix it on this branch and enqueue it again "
            f"(`loregarden_enqueue_merge pr_number={entry.pr_number}`)."
        )
        failure = self.github.comment(entry.pr_number, body)
        if failure:
            self.report.errors.append(f"PR #{entry.pr_number}: comment failed: {failure}")
            logger.warning("Merge queue: PR #%s comment failed: %s", entry.pr_number, failure)

    def _hold(
        self, entry: MergeQueueEntry, expected: MergeQueueState, *, when: str, **fields: object
    ) -> bool:
        """Record why the head entry is not moving; False, so the cycle stops on it.

        If someone moved it first the write matches nothing, and that is the
        named outcome `_gone` reports — never a write that silently did nothing.
        """
        if not transition(self.session, entry, expected, **fields):
            return self._gone(entry, when)
        return False

    def _relabel(
        self, entry: MergeQueueEntry, expected: MergeQueueState, state: MergeQueueState
    ) -> bool:
        moved = transition(self.session, entry, expected, state=state)
        if moved:
            self.session.refresh(entry)
        return moved

    def _gone(self, entry: MergeQueueEntry, when: str) -> bool:
        """The named outcome for an entry someone else moved: withdrawn, re-pointed, retried."""
        self.session.expire_all()
        current = self.session.get(MergeQueueEntry, entry.id)
        now = current.state.value if current else "deleted"
        self.report.actions.append(f"PR #{entry.pr_number}: moved to {now} {when}; left alone")
        logger.info("Merge queue: PR #%s moved to %s %s; left alone", entry.pr_number, now, when)
        return True


# -- git and GitHub helpers ---------------------------------------------------


def fetch_base(repo_root: Path, base_branch: str) -> tuple[str, str]:
    """The base's tip on origin, fetched now, and why not ("" when it was read)."""
    tracking = f"refs/remotes/origin/{base_branch}"
    fetched = run_git(
        ["fetch", "--quiet", "origin", f"+refs/heads/{base_branch}:{tracking}"],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if fetched.returncode != 0:
        reason = (fetched.stderr or fetched.stdout or "git fetch failed").strip()
        return "", f"could not fetch {base_branch} from origin: {reason}"
    tip = run_git(
        ["rev-parse", "--verify", f"{tracking}^{{commit}}"],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if tip.returncode != 0:
        return "", f"origin has no {base_branch}: {tip.stderr.strip()}"
    return tip.stdout.strip(), ""


def tree_of(repo_root: Path, commit: str) -> str:
    result = run_git(
        ["rev-parse", f"{commit}^{{tree}}"],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"no tree for {commit}: {result.stderr.strip()}")
    return result.stdout.strip()


def _is_commit(repo_root: Path, sha: str) -> bool:
    return (
        run_git(
            ["cat-file", "-e", f"{sha}^{{commit}}"],
            cwd=str(repo_root),
            check=False,
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )


def assert_lands_staged_tree(
    repo_root: Path, entry: MergeQueueEntry, pr: StagedPullRequest
) -> None:
    """Refuse unless what GitHub would merge is exactly the tree that went green.

    Three readings must agree before the base moves: our staged commit's tree,
    GitHub's tree for the PR head, and GitHub's base being the tip the commit
    was cut on (so its squash, a one-commit head on that tip, writes that tree).
    """
    local = tree_of(repo_root, entry.staged_sha)
    if local != entry.staged_tree:
        raise TreeMismatch(f"staged commit {entry.staged_sha} now reads", entry.staged_tree, local)
    if pr.head_sha != entry.staged_sha:
        raise TreeMismatch(
            f"GitHub's head {pr.head_sha} is not the staged commit {entry.staged_sha}",
            entry.staged_tree,
            pr.head_tree,
        )
    if pr.head_tree != entry.staged_tree:
        raise TreeMismatch(f"GitHub's head {pr.head_sha} has", entry.staged_tree, pr.head_tree)
    if pr.base_sha != entry.staged_base_sha:
        raise TreeMismatch(
            f"GitHub compares against base {pr.base_sha}, not {entry.staged_base_sha}; a squash "
            "there would not write the staged tree",
            entry.staged_tree,
            f"(merge onto {pr.base_sha})",
        )


def delete_staging_ref(repo_root: Path, branch: str) -> str:
    """Delete the staging ref on GitHub; "" when done or already gone, else why not."""
    if not branch:
        return ""
    try:
        proc = run_gh(
            ["api", "-X", "DELETE", f"repos/{{owner}}/{{repo}}/git/refs/heads/{branch}"],
            cwd=repo_root,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"could not delete {branch}: gh did not run: {exc}"
    if proc.returncode == 0:
        return ""
    detail = (proc.stderr or proc.stdout or "").strip()
    if "Reference does not exist" in detail:
        return ""
    return f"could not delete {branch}: {detail or 'gh api DELETE failed'}"


def advance_workspace(session: Session, workspace: Workspace) -> CycleReport:
    """One cycle for ``workspace``; what the CI poll tick calls."""
    repo_root = resolve_workspace_root(workspace)
    if not (repo_root / ".git").exists():
        return CycleReport(slug=workspace.slug)
    return MergeQueueLoop(session, workspace).advance()
