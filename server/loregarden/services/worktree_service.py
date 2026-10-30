"""Git worktree service for parallel agent execution isolation."""

import logging
import os
import subprocess
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from uuid import uuid4

from loregarden.models.domain import (
    AgentRun,
    BaxterChatSession,
    PrimaryCheckoutUse,
    Ticket,
    Workspace,
    Worktree,
    WorktreeState,
)
from loregarden.services.branch_names import (
    chat_session_branch,
    resolve_ticket_branch,
    validate_branch_name,
)
from loregarden.services.branch_takes_target import MergeTurnSeat, take_target
from loregarden.services.directory_release import (
    DirectoryOutsideBase,
    DirectoryReleaseFailed,
    DirectoryState,
    ReleaseDisposition,
    directory_state,
    release_directory,
)
from loregarden.services.git_subprocess import run_git
from loregarden.services.primary_checkout import DirtyPrimaryCheckoutError, require_clean_checkout
from loregarden.services.process_identity import (
    BootIdentityUnavailable,
    ClaimIdentity,
    claim_fields,
)
from loregarden.services.publish_ref import comparison_ref
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, select

logger = logging.getLogger(__name__)


class WorktreeRefreshError(RuntimeError):
    """An existing branch could not be brought up to its target branch.

    Raised, not degraded into "run in the shared checkout": a ticket whose
    branch cannot take its prerequisites' landed work is exactly the ticket
    that must not start (lg-milestone-that-769).
    """


class CleanupRefusal(Enum):
    """Why a checkout the service was asked to remove is still on disk.

    Two conditions an operator has to answer differently, and
    ``cleanup_worktree`` collapsed both into one ``False``: a recorded path
    outside the service's base, which nothing will ever make removable, and an
    exception out of ``git worktree remove`` plus the ``shutil.rmtree``
    fallback, which the next attempt often clears.

    A plain ``Enum`` and deliberately NOT a ``StrEnum``, which is the one place
    this diverges from :class:`~loregarden.services.worktree_lifecycle.KeepReason`
    next door. ``KeepReason``'s values ARE the wording its callers print. These
    are machine slugs that reach no operator, because the boot log and the
    chat-delete toast need DIFFERENT sentences for the SAME cause — so one
    string per member cannot be the copy for both, and every caller picks its
    own words from the member.
    """

    #: The recorded path is not under ``worktree_base``. Nothing was attempted,
    #: and no retry will change the answer: the row's path or the workspace's
    #: ``repo_path`` is wrong.
    OUTSIDE_BASE = "outside_base"
    #: The removal was attempted and raised — a permission error, a file another
    #: process holds open, an NFS stall. Often transient, so retrying is real.
    REMOVE_RAISED = "remove_raised"


class ConflictDetectionError(RuntimeError):
    """The conflict check could not be completed.

    Distinct from "no conflicts" on purpose. `detect_conflicts` used to return
    ``False`` on any failure, which is the same value it returns for a clean
    merge — so a broken git call read as permission to merge.
    """


def repo_path_for_workspace(session: Session, workspace_id: str) -> str:
    """The checkout a workspace's worktrees hang off.

    Every caller used to pass ``"."``, which resolves against the server
    process's CWD — loregarden's own server directory — so worktrees were cut
    from the control plane's repository instead of the workspace being
    orchestrated. It happened to look right only when orchestrating loregarden
    itself.
    """
    workspace = session.get(Workspace, workspace_id)
    if not workspace:
        raise ValueError(f"Workspace not found: {workspace_id}")
    return str(resolve_workspace_root(workspace))


def repo_path_for_worktree(session: Session, worktree: Worktree) -> str:
    """Same, for a handler that starts from a worktree row."""
    return repo_path_for_workspace(session, worktree.workspace_id)


#: Directories a gate needs but git never puts in a worktree, because they are
#: gitignored build output. Linked from the parent checkout rather than
#: installed: `npm ci` per ticket costs minutes, and a worktree of the same repo
#: wants the same dependencies by definition.
_LINKED_TOOLCHAIN_DIRS = ("client/node_modules",)


def _link_ignored_toolchains(repo_path: Path, worktree_path: Path) -> None:
    """Point a fresh worktree at the parent checkout's installed dependencies.

    Without this the client gate runs `npx oxlint` in a tree with no
    node_modules, npx tries to fetch it over the network on every gate, and the
    300s budget expires — which the orchestrator handed to the stage's own agent
    as a code failure, re-running a stage that had already passed. Every ticket
    hit it, every transition.

    Best-effort: a missing source, a link that cannot be made, or a directory
    the ticket already has are all fine and leave the gate to report for itself.
    """
    for relative in _LINKED_TOOLCHAIN_DIRS:
        source = repo_path / relative
        target = worktree_path / relative
        if not source.is_dir() or target.exists():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source, target_is_directory=True)
        except OSError:
            logger.warning(
                "Could not link %s into worktree %s; gates needing it will report unavailable",
                relative,
                worktree_path,
                exc_info=True,
            )


class WorktreeService:
    """Manage git worktrees for parallel agent execution isolation."""

    def __init__(self, session: Session, repo_path: str):
        self.session = session
        self.repo_path = Path(repo_path).resolve()
        self.worktree_base = self.repo_path.parent / ".worktrees"
        #: Where a release renames a checkout to before deleting it. A SIBLING
        #: of the base under the same parent, so `os.replace` is a same-device
        #: rename: a cross-device one answers EXDEV, which is not atomic and
        #: would put the check-then-act window straight back.
        self.park_root = self.repo_path.parent / ".worktrees-releasing"

    def create_worktree(
        self,
        workspace_id: str,
        agent_run_id: str,
        parent_branch: str = "main",
        branch: str = "",
    ) -> Worktree | None:
        """
        Create an isolated git worktree for an agent run.

        Args:
            workspace_id: Workspace ID
            agent_run_id: Agent run ID
            parent_branch: Branch to base the worktree's branch on
            branch: Branch to check out in the worktree. Defaults to a branch
                named after the run.

        Returns:
            Worktree record if successful, None on error

        An existing ``branch`` is checked out as it is. This used ``worktree add
        -B``, which resets an existing branch to ``parent_branch`` — so a retried
        run whose branch already carried commits had them orphaned on the way
        in, and the tree it got looked exactly like a first attempt
        (lg-milestone-that-772).
        """
        agent_run_stmt = select(AgentRun).where(AgentRun.id == agent_run_id)
        agent_run = self.session.exec(agent_run_stmt).first()
        if not agent_run:
            logger.warning(f"Agent run not found: {agent_run_id}")
            return None

        # Unique worktree path: .worktrees/run-{run_id}-{random}
        worktree_name = f"run-{agent_run_id[:8]}-{str(uuid4())[:8]}"
        return self._add_worktree(
            workspace_id=workspace_id,
            agent_run_id=agent_run_id,
            branch=branch or worktree_name,
            parent_branch=parent_branch,
            name=worktree_name,
            refresh=True,
        )

    def get_or_create_for_ticket(
        self,
        ticket: Ticket,
        agent_run_id: str,
        parent_branch: str = "main",
        seat: MergeTurnSeat | None = None,
    ) -> Worktree | None:
        """The one worktree a ticket's stages share, creating it on first use.

        Distinct from :meth:`create_worktree`, which cuts a fresh tree per run:
        a pipeline's stages have to see each other's work, so the second stage
        must land in the tree the first one wrote. That also rules out the
        ``-B`` create_worktree uses — resetting the ticket branch to its parent
        between stages would discard everything committed so far.
        """
        existing = self.active_worktree_for_ticket(ticket.id)
        if existing:
            if Path(existing.worktree_path).is_dir():
                return self._restamp_claim(existing)
            # Recorded but gone: a manual `git worktree remove`, a wiped temp
            # directory, or a cleanup that crashed halfway. Handing the path
            # back would set a nonexistent cwd on the run.
            logger.warning(
                "Worktree %s for ticket %s is missing at %s; cutting a replacement",
                existing.id,
                ticket.id,
                existing.worktree_path,
            )
            self._retire_missing(existing)

        branch = resolve_ticket_branch(ticket)
        validate_branch_name(branch)
        slug = branch.replace("/", "-")[:32]
        return self._add_worktree(
            workspace_id=ticket.workspace_id,
            agent_run_id=agent_run_id,
            ticket_id=ticket.id,
            branch=branch,
            parent_branch=parent_branch,
            name=f"ticket-{slug}-{str(uuid4())[:8]}",
            refresh=True,
            seat=seat,
        )

    def get_or_create_for_chat_session(
        self,
        chat_session: BaxterChatSession,
        agent_run_id: str,
        parent_branch: str = "main",
    ) -> Worktree | None:
        """The one worktree a chat thread's acting turns share.

        The conversational counterpart of :meth:`get_or_create_for_ticket`, and
        for the same reason: a follow-up message that says "no, rename it back"
        has to land in the tree the previous turn wrote to, so the thread is the
        unit of reuse rather than the turn.
        """
        existing = self.active_worktree_for_chat_session(chat_session.id)
        if existing:
            if Path(existing.worktree_path).is_dir():
                return self._restamp_claim(existing)
            logger.warning(
                "Worktree %s for chat session %s is missing at %s; cutting a replacement",
                existing.id,
                chat_session.id,
                existing.worktree_path,
            )
            self._retire_missing(existing)

        branch = chat_session_branch(chat_session)
        validate_branch_name(branch)
        slug = branch.replace("/", "-")[:32]
        return self._add_worktree(
            workspace_id=chat_session.workspace_id,
            agent_run_id=agent_run_id,
            chat_session_id=chat_session.id,
            branch=branch,
            parent_branch=parent_branch,
            name=f"chat-{slug}-{str(uuid4())[:8]}",
        )

    def active_worktree_for_chat_session(self, chat_session_id: str) -> Worktree | None:
        """The tree this conversation's acting turns are sharing, if it has one."""
        stmt = (
            select(Worktree)
            .where(Worktree.chat_session_id == chat_session_id)
            .where(Worktree.state == WorktreeState.ACTIVE)
            .order_by(Worktree.created_at.desc())
        )
        return self.session.exec(stmt).first()

    def active_worktree_for_ticket(self, ticket_id: str) -> Worktree | None:
        """The tree this ticket's stages are sharing, if it has one."""
        stmt = (
            select(Worktree)
            .where(Worktree.ticket_id == ticket_id)
            .where(Worktree.state == WorktreeState.ACTIVE)
            .order_by(Worktree.created_at.desc())
        )
        return self.session.exec(stmt).first()

    def _restamp_claim(self, worktree: Worktree) -> Worktree:
        """Record the process reusing this tree as its claimant.

        A ticket's stages share one tree across processes, and a chat thread's
        turns do the same, so a claim left at the process that CUT the tree is
        a claim the next sweep reads as a finished stage's pid — gone, and the
        tree therefore unclaimed while an agent is writing to it. The claim
        names whoever is using it now.

        A box that cannot read its own boot stamp must not lose the tree it
        just cut: an unstampable claim is left as it was, and the gate reads an
        unreadable or absent claim conservatively. This is the one place the
        raise is swallowed, and the reason is that the alternative is failing
        a run over its own bookkeeping.
        """
        try:
            claim = ClaimIdentity.current()
        except BootIdentityUnavailable:
            # silent-ok: the claim is bookkeeping, and an unstamped or stale one
            # is read as unproven by the gate, which keeps the tree — so the
            # failure cannot license a delete. Logged, never swallowed quietly.
            logger.warning(
                "Could not stamp a claim on worktree %s; the gate will treat it as unproven",
                worktree.id,
                exc_info=True,
            )
            return worktree
        worktree.claim_pid = str(claim.pid)
        worktree.claim_birth = claim.birth
        worktree.claim_boot_id = claim.boot_id
        self.session.add(worktree)
        self.session.commit()
        self.session.refresh(worktree)
        return worktree

    def _retire_missing(self, worktree: Worktree) -> None:
        """Mark a vanished worktree cleaned and free the branch it still holds.

        Git keeps admin metadata for a directory deleted behind its back and
        refuses to check the branch out again until the metadata is pruned.
        """
        worktree.state = WorktreeState.CLEANUP
        worktree.cleaned_at = datetime.now(timezone.utc)
        self.session.add(worktree)
        self.session.commit()
        # silent-ok: pruning stale metadata is idempotent and best-effort; the
        # next cleanup or `worktree add` prunes again if this one did nothing
        run_git(
            ["worktree", "prune"],
            cwd=str(self.repo_path),
            check=False,
            capture_output=True,
        )

    def _refresh_onto(self, worktree_path: Path, branch: str, parent_branch: str) -> None:
        merged = run_git(
            ["merge", "--no-edit", parent_branch],
            cwd=str(worktree_path),
            check=False,
            capture_output=True,
            text=True,
        )
        if merged.returncode == 0:
            return
        detail = (merged.stdout or merged.stderr or "git merge failed").strip()
        # silent-ok: cleanup on an already-failing path; the WorktreeRefreshError
        # below carries the merge's own output.
        run_git(["merge", "--abort"], cwd=str(worktree_path), check=False, capture_output=True)
        # The forced checkout deletion that used to run here is gone
        # (lg-external-harness-1026 AC16). It was the one destructive act in
        # this tree gated on nothing whatsoever — not a premise, not a receipt,
        # not a glance at the checkout — and its own silent-ok waiver argued it
        # was unnecessary: the next `worktree add` prunes the metadata either
        # way. The error below still carries git's own output, and the operator
        # still has the conflicted tree they need in order to resolve it, with
        # their uncommitted files in it.
        raise WorktreeRefreshError(
            f"Branch {branch!r} could not take {parent_branch!r} before starting: {detail}"
        )

    def _branch_exists(self, branch: str) -> bool:
        result = run_git(
            ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=str(self.repo_path),
            check=False,
            capture_output=True,
        )
        return result.returncode == 0

    def _add_worktree(
        self,
        workspace_id: str,
        agent_run_id: str,
        branch: str,
        parent_branch: str,
        name: str,
        ticket_id: str | None = None,
        chat_session_id: str | None = None,
        refresh: bool = False,
        seat: MergeTurnSeat | None = None,
    ) -> Worktree | None:
        """Check `branch` out in a new directory without ever resetting it.

        With ``refresh``, an existing ``branch`` is brought up to
        ``parent_branch`` by merging it in before the tree is handed out. The
        tree is cut when the ticket starts, so this is the one point that can
        see a prerequisite that landed after the ticket was scheduled. A merge
        conflict raises :class:`WorktreeRefreshError`; the half-cut tree is
        removed first so the next attempt starts clean. Before the tree is cut,
        a conflict goes to `branch_takes_target`, which may move the branch
        onto ``parent_branch`` or, given a ``seat``, run an agent's merge turn.
        """
        try:
            self.worktree_base.mkdir(parents=True, exist_ok=True)
            worktree_path = self.worktree_base / name

            existed = self._branch_exists(branch)
            if existed and refresh:
                taken = take_target(self.repo_path, branch, parent_branch, seat=seat)
                if not taken.ok:
                    raise WorktreeRefreshError(
                        f"Branch {branch!r} could not take {parent_branch!r} before "
                        f"starting: {taken.detail}"
                    )
            if existed:
                add_args = ["worktree", "add", str(worktree_path), branch]
            else:
                add_args = ["worktree", "add", "-b", branch, str(worktree_path), parent_branch]

            logger.info("Creating ticket worktree %s on %s", worktree_path, branch)
            run_git(add_args, cwd=str(self.repo_path), check=True, capture_output=True)
            if existed and refresh:
                self._refresh_onto(worktree_path, branch, parent_branch)
            _link_ignored_toolchains(self.repo_path, worktree_path)

            head = run_git(
                ["rev-parse", "HEAD"],
                cwd=str(worktree_path),
                check=True,
                capture_output=True,
                text=True,
            )

            worktree = Worktree(
                id=str(uuid4()),
                workspace_id=workspace_id,
                agent_run_id=agent_run_id,
                ticket_id=ticket_id,
                chat_session_id=chat_session_id,
                parent_branch=parent_branch,
                branch=branch,
                worktree_path=str(worktree_path),
                state=WorktreeState.ACTIVE,
                merge_base=head.stdout.strip(),
                **claim_fields(ClaimIdentity.current()),
            )
            self.session.add(worktree)
            self.session.commit()
            self.session.refresh(worktree)
            return worktree

        except subprocess.CalledProcessError as exc:
            logger.error("git worktree add failed: %s", exc.stderr, exc_info=True)
            return None

    def detect_conflicts(self, worktree: Worktree, target_branch: str = "main") -> bool:
        """
        Check if merging worktree back to target branch would cause conflicts.

        Args:
            worktree: Worktree record
            target_branch: Branch to merge into (default: main)

        Returns:
            True if conflicts detected, False otherwise

        Raises:
            ConflictDetectionError: the check could not be completed. This used
                to return ``False`` — indistinguishable from "no conflicts", on
                the one call that decides whether a merge may proceed.
        """
        try:
            worktree_path = Path(worktree.worktree_path)

            # A failed fetch is not fatal, but the merge-base below is then
            # computed against a stale origin/main — say so rather than
            # silently comparing against yesterday's remote.
            fetch = run_git(
                ["fetch", "origin"],
                cwd=str(worktree_path),
                check=False,
                capture_output=True,
            )
            if fetch.returncode != 0:
                logger.warning(
                    "fetch origin failed in %s (rc=%s); conflict detection is computed "
                    "against a possibly stale origin/%s",
                    worktree_path,
                    fetch.returncode,
                    target_branch,
                )

            # Try dry-run merge to detect conflicts
            # git merge --no-commit --no-ff <target as comparison_ref names it>
            result = run_git(
                ["merge", "--no-commit", "--no-ff", comparison_ref(target_branch)],
                cwd=str(worktree_path),
                capture_output=True,
                text=True,
            )

            has_conflicts = result.returncode != 0
            if has_conflicts:
                # Extract conflicting files from git status
                conflict_files = self._extract_conflict_files(worktree_path)
                worktree.has_conflicts = True
                worktree.conflict_files = conflict_files
                worktree.conflict_summary = f"Merge conflicts in {len(conflict_files)} files"

                logger.warning(f"Conflicts detected in worktree {worktree.id}: {conflict_files}")
            else:
                # Abort the dry-run merge
                # silent-ok: undoing a merge we only started to probe; if it did
                # not start there is nothing to abort, and the next probe re-runs
                run_git(
                    ["merge", "--abort"],
                    cwd=str(worktree_path),
                    check=False,
                    capture_output=True,
                )

            self.session.add(worktree)
            self.session.commit()

            return has_conflicts

        except Exception as e:  # noqa: BLE001 - boundary: any failure means "unknown"
            logger.exception("Error detecting conflicts for worktree %s", worktree.id)
            raise ConflictDetectionError(
                f"Could not determine merge conflicts for worktree {worktree.id}: {e}"
            ) from e

    def _extract_conflict_files(self, worktree_path: Path) -> list[str]:
        """Extract list of files with merge conflicts."""
        try:
            result = run_git(
                ["diff", "--name-only", "--diff-filter=U"],
                cwd=str(worktree_path),
                check=True,
                capture_output=True,
                text=True,
            )
            files = result.stdout.strip().split("\n")
            return [f for f in files if f]  # Filter empty strings
        except Exception:
            # Reached only after the dry-run merge already reported conflicts, so
            # an empty list here would read as "conflicts in 0 files". Let it
            # propagate: detect_conflicts turns it into ConflictDetectionError.
            logger.exception("Error extracting conflict files from %s", worktree_path)
            raise

    def merge_worktree(
        self,
        worktree: Worktree,
        target_branch: str = "main",
        auto_resolve: bool = False,
    ) -> bool:
        """
        Merge worktree changes back to target branch.

        Args:
            worktree: Worktree record
            target_branch: Branch to merge into
            auto_resolve: If True, attempt auto-merge with conflict resolution

        Returns:
            True if merge successful, False if conflicts or error
        """
        try:
            if worktree.state != WorktreeState.ACTIVE:
                logger.warning(f"Cannot merge worktree {worktree.id} in state {worktree.state}")
                return False

            worktree_path = Path(worktree.worktree_path)

            # Check if there are changes to commit
            result = run_git(
                ["status", "--porcelain"],
                cwd=str(worktree_path),
                check=True,
                capture_output=True,
                text=True,
            )

            if not result.stdout.strip():
                logger.info(f"No changes in worktree {worktree.id}")
                worktree.state = WorktreeState.MERGED
                worktree.merged_at = datetime.now(timezone.utc)
                self.session.add(worktree)
                self.session.commit()
                return True

            # The merge checks `target_branch` out in the primary checkout below;
            # refuse first if that checkout holds someone's uncommitted work (864).
            require_clean_checkout(Path(self.repo_path), use=PrimaryCheckoutUse.MERGE_WORKTREE)

            # Detect conflicts before attempting merge
            has_conflicts = self.detect_conflicts(worktree, target_branch)

            if has_conflicts:
                if not auto_resolve:
                    logger.warning(f"Merge conflicts in {worktree.id}, not auto-resolving")
                    worktree.state = WorktreeState.FAILED
                    self.session.add(worktree)
                    self.session.commit()
                    return False

                # Attempt auto-resolution (favor changes from worktree)
                logger.info(f"Attempting auto-resolution for {worktree.id}")
                if not self._auto_resolve_conflicts(worktree_path):
                    logger.error(f"Auto-resolution failed for {worktree.id}")
                    worktree.state = WorktreeState.FAILED
                    self.session.add(worktree)
                    self.session.commit()
                    return False

            # Perform the actual merge back to main repo
            run_git(
                ["checkout", target_branch],
                cwd=str(self.repo_path),
                check=True,
                capture_output=True,
            )

            # Merge the worktree's *branch*. The directory name is not a ref —
            # merging it resolved nothing and failed on every worktree whose
            # branch was not coincidentally named after its folder. Prefer the
            # recorded branch, and fall back to asking the worktree itself for
            # rows written before the column existed.
            branch = worktree.branch
            if not branch:
                head = run_git(
                    ["rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=str(worktree_path),
                    check=True,
                    capture_output=True,
                    text=True,
                )
                branch = head.stdout.strip()

            result = run_git(
                ["merge", branch],
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                logger.error(f"Merge failed: {result.stderr}")
                worktree.state = WorktreeState.FAILED
                self.session.add(worktree)
                self.session.commit()
                return False

            worktree.state = WorktreeState.MERGED
            worktree.merged_at = datetime.now(timezone.utc)
            self.session.add(worktree)
            self.session.commit()

            logger.info(f"Successfully merged worktree {worktree.id}")
            return True

        except DirtyPrimaryCheckoutError:
            # Nothing was touched: the worktree stays ACTIVE and mergeable once
            # the primary is clean. Raised so the caller names the paths rather
            # than reading a refusal as a failed merge.
            raise
        except Exception:  # noqa: BLE001 - boundary: any failure fails the merge
            logger.exception("Error merging worktree %s", worktree.id)
            worktree.state = WorktreeState.FAILED
            self.session.add(worktree)
            self.session.commit()
            return False

    def _auto_resolve_conflicts(self, worktree_path: Path) -> bool:
        """Attempt to auto-resolve merge conflicts using ours/theirs strategy."""
        try:
            # Accept ours (worktree changes) for all conflicts
            conflict_files = self._extract_conflict_files(worktree_path)

            for file_path in conflict_files:
                run_git(
                    ["checkout", "--ours", file_path],
                    cwd=str(worktree_path),
                    check=True,
                    capture_output=True,
                )

            # Stage resolved files
            run_git(
                ["add"] + conflict_files,
                cwd=str(worktree_path),
                check=True,
                capture_output=True,
            )

            # Complete merge
            run_git(
                ["commit", "-m", "Auto-resolved merge conflicts"],
                cwd=str(worktree_path),
                check=True,
                capture_output=True,
            )

            logger.info(f"Auto-resolved conflicts in {len(conflict_files)} files")
            return True

        except Exception:  # noqa: BLE001 - boundary: auto-resolution is all-or-nothing
            # The three git calls above are check=True, so a failure can land
            # between them: files checked out but not staged, or staged but not
            # committed. The caller only learns "False", so name the partial
            # state here — it is what someone debugging the worktree needs.
            logger.exception(
                "Error auto-resolving conflicts in %s; the worktree may be left "
                "part-resolved (checked out and/or staged but not committed)",
                worktree_path,
            )
            return False

    def _refuse_if_outside_base(self, worktree: Worktree) -> Path:
        """The recorded path, or a raise saying it is not ours to touch.

        OUTSIDE_BASE is decided HERE, on BOTH the recorded spelling and the
        resolved path, because each arm admits what the other refuses and only
        the pair is an invariant. ``is_relative_to`` compares path *parts*, so
        neither arm accepts the sibling whose name merely extends the base's
        (``.worktrees-salvage``) — which the ``str(path).startswith(str(base))``
        guard this replaced did accept. The resolved arm is needed because
        ``PurePath.is_relative_to`` does not normalise ``..``, so a lexical
        check alone admits a path that climbs back out. The lexical arm is
        needed because resolution follows symlinks: a path spelled outside the
        base that links *into* it resolves inside, and the row's recorded
        spelling is itself evidence — a checkout git created is spelled under
        the base, so one that is not was not created here.

        The two arms AGREE with ``release_directory`` rather than one covering
        for the other: it refuses a symlink final component outright, in either
        direction, because a checkout is a directory and never a link. So
        neither the outside->inside link this lexical arm catches nor the
        inside->inside link it cannot see reaches a rename.

        Judged on the RECORDED SPELLING before anything is read off the disk,
        which is why it is this method and not part of the release: a row whose
        checkout is already gone still has to be told apart from a row whose
        recorded path was never under this workspace's base, and only the
        second is a fault no boot will settle.

        Nothing is logged. The raise IS the surfacing, and it carries the cause
        the member cannot: which exception, from which call, with which errno,
        as ``__cause__``.
        """
        worktree_path = Path(worktree.worktree_path)
        if not (
            worktree_path.is_relative_to(self.worktree_base)
            and worktree_path.resolve().is_relative_to(self.worktree_base.resolve())
        ):
            raise DirectoryOutsideBase(
                f"{worktree.worktree_path!r} is outside {self.worktree_base}; nothing was "
                "attempted and no retry can change that",
                disposition=ReleaseDisposition.INTACT,
                refusal=CleanupRefusal.OUTSIDE_BASE,
                uncleaned_path=worktree.worktree_path,
            )
        return worktree_path

    def _mark_cleaned(self, worktree: Worktree) -> None:
        """Stamp the row CLEANUP: this workspace no longer owns a checkout here."""
        worktree.state = WorktreeState.CLEANUP
        worktree.cleaned_at = datetime.now(timezone.utc)
        self.session.add(worktree)
        self.session.commit()
        logger.info("Cleaned up worktree %s", worktree.id)

    def settle_absent_checkout(self, worktree: Worktree) -> None:
        """Stamp the row CLEANUP for a checkout the gate found ABSENT.

        The row half of :meth:`remove_checkout` with the destructive half left
        out, and the reason it exists is AC9. The gate stats the recorded path
        once to ask whether there is a checkout to gate at all; an ABSENT
        answer means there is nothing to gate, and that answer was then being
        spent on a rename-then-delete of whatever the path held when the
        release ran — which is the check-then-act window one level up from the
        one `release_directory` closed. A row stays ACTIVE while its checkout
        is missing, `resolve_execution_root` REUSES that row and recreates the
        checkout, and `reconcile_worktrees` runs on a timer: so the sweep read
        absent, a new run cut a checkout into the same recorded path, and the
        sweep deleted it, having consulted no receipt, no claim and no premise.

        Only the rename-then-delete is skipped. The containment judgement still
        runs, because it is made on the row's RECORDED SPELLING rather than on
        anything read off the disk, and an absent-but-out-of-base row is a
        permanent fault an operator has to clear rather than stale bookkeeping
        a boot can settle.

        A row recording NO path is the one shape that judgement has nothing to
        judge: there is no spelling to place inside or outside the base, and
        nothing on disk that stamping the row could reach. Refusing it left a
        row claiming a checkout at nowhere, reported by every pass and settled
        by none (lg-external-harness-1452).
        """
        if worktree.worktree_path:
            self._refuse_if_outside_base(worktree)
        self._mark_cleaned(worktree)

    def remove_checkout(self, worktree: Worktree) -> None:
        """Release a worktree's checkout and stamp the row CLEANUP.

        A command, not a question. ``None`` is the whole success case — which
        includes "nothing was on disk to unlink", the case
        `reconcile_worktrees` depends on — and every failure RAISES
        :class:`~loregarden.services.directory_release.DirectoryReleaseFailed`
        carrying the :class:`CleanupRefusal` member for the cause and the path
        a failed delete parked the checkout at.

        It used to answer ``False`` for three different things, then a
        ``CleanupRefusal`` member for two: better vocabulary, same shape — a
        value a caller can forget to read. All five production readers were
        truthiness readers of the bool, so a refusal read as a removal.
        `retire_worktree` is now the one place that catches this.

        Only reached for a checkout the gate OBSERVED, which is what makes the
        release here legitimate: see :meth:`settle_absent_checkout` for the
        absent half and why an absence may not license a rename-then-delete.

        Containment is judged by :meth:`_refuse_if_outside_base`, before
        anything is read off the disk.

        Nothing is logged. The raise IS the surfacing, and it carries the cause
        the member cannot: which exception, from which call, with which errno,
        as ``__cause__``.
        """
        worktree_path = self._refuse_if_outside_base(worktree)

        # `directory_state` first, so a path the kernel will not describe raises
        # rather than reading as nothing. It answers ABSENT for a plain file as
        # well as for a missing path, and a FILE recorded where a checkout
        # belongs is not nothing — settling the row on it would drop the only
        # record of a corrupt path — so the release is attempted and the
        # rename's own answer says what happened. `os.path.lexists` is second
        # for the reason it cannot be first: it returns False on an EACCES,
        # which is the swallow `directory_state` exists to prevent.
        if directory_state(worktree_path) is DirectoryState.PRESENT or os.path.lexists(
            worktree_path
        ):
            logger.info("Removing worktree: %s", worktree_path)
            try:
                release_directory(
                    worktree_path,
                    inside=self.worktree_base,
                    park_root=self.park_root,
                )
            # The member is this module's vocabulary, stamped on the way past:
            # `directory_release` cannot name a `CleanupRefusal` without
            # importing the module that imports it. Two handlers rather than a
            # type test, so a third cause added to the hierarchy has to choose.
            except DirectoryOutsideBase as failed:
                failed.refusal = CleanupRefusal.OUTSIDE_BASE
                raise
            except DirectoryReleaseFailed as failed:
                failed.refusal = CleanupRefusal.REMOVE_RAISED
                raise

        self._mark_cleaned(worktree)

    def get_worktree(self, worktree_id: str) -> Worktree | None:
        """Fetch worktree by ID."""
        stmt = select(Worktree).where(Worktree.id == worktree_id)
        return self.session.exec(stmt).first()

    def get_active_worktrees(self, workspace_id: str) -> list[Worktree]:
        """Get all active worktrees for a workspace."""
        stmt = select(Worktree).where(
            (Worktree.workspace_id == workspace_id) & (Worktree.state == WorktreeState.ACTIVE)
        )
        return list(self.session.exec(stmt).all())

    def get_worktrees_by_run(self, agent_run_id: str) -> list[Worktree]:
        """Get all worktrees for an agent run."""
        stmt = select(Worktree).where(Worktree.agent_run_id == agent_run_id)
        return list(self.session.exec(stmt).all())
