"""Git worktree service for parallel agent execution isolation."""

import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from loregarden.models.domain import (
    AgentRun,
    BaxterChatSession,
    Ticket,
    Workspace,
    Worktree,
    WorktreeState,
)
from loregarden.services.git_branch import (
    chat_session_branch,
    resolve_ticket_branch,
    validate_branch_name,
)
from loregarden.services.git_subprocess import run_git
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session, select

logger = logging.getLogger(__name__)


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
        """
        try:
            # Validate agent run exists
            agent_run_stmt = select(AgentRun).where(AgentRun.id == agent_run_id)
            agent_run = self.session.exec(agent_run_stmt).first()
            if not agent_run:
                logger.warning(f"Agent run not found: {agent_run_id}")
                return None

            # Ensure worktree base directory exists
            self.worktree_base.mkdir(parents=True, exist_ok=True)

            # Generate unique worktree path: .worktrees/run-{run_id}-{random}
            worktree_name = f"run-{agent_run_id[:8]}-{str(uuid4())[:8]}"
            worktree_path = self.worktree_base / worktree_name
            branch = branch or worktree_name

            # `add <path> <parent_branch>` checked *parent_branch itself* out
            # here, which git refuses when the root already has it checked out
            # — and when it did work, the run committed straight onto main.
            # `-B <branch> <parent_branch>` cuts the run its own branch instead,
            # and -B rather than -b so a retried run reuses its branch.
            logger.info(f"Creating worktree: {worktree_path} on {branch} from {parent_branch}")
            run_git(
                ["worktree", "add", "-B", branch, str(worktree_path), parent_branch],
                cwd=str(self.repo_path),
                check=True,
                capture_output=True,
            )

            # Get current commit on new worktree (merge base)
            result = run_git(
                ["rev-parse", "HEAD"],
                cwd=str(worktree_path),
                check=True,
                capture_output=True,
                text=True,
            )
            merge_base = result.stdout.strip()

            # Create worktree record
            worktree = Worktree(
                id=str(uuid4()),
                workspace_id=workspace_id,
                agent_run_id=agent_run_id,
                parent_branch=parent_branch,
                branch=branch,
                worktree_path=str(worktree_path),
                state=WorktreeState.ACTIVE,
                merge_base=merge_base,
            )

            self.session.add(worktree)
            self.session.commit()

            logger.info(f"Created worktree {worktree.id} at {worktree_path}")
            return worktree

        except subprocess.CalledProcessError as e:
            logger.error(f"Git command failed: {e.stderr}", exc_info=True)
            return None
        except Exception as e:
            logger.error(f"Error creating worktree: {e}", exc_info=True)
            return None

    def get_or_create_for_ticket(
        self,
        ticket: Ticket,
        agent_run_id: str,
        parent_branch: str = "main",
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
                return existing
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
                return existing
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
    ) -> Worktree | None:
        """Check `branch` out in a new directory without ever resetting it."""
        try:
            self.worktree_base.mkdir(parents=True, exist_ok=True)
            worktree_path = self.worktree_base / name

            if self._branch_exists(branch):
                add_args = ["worktree", "add", str(worktree_path), branch]
            else:
                add_args = ["worktree", "add", "-b", branch, str(worktree_path), parent_branch]

            logger.info("Creating ticket worktree %s on %s", worktree_path, branch)
            run_git(add_args, cwd=str(self.repo_path), check=True, capture_output=True)
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
            # git merge --no-commit --no-ff origin/target_branch
            result = run_git(
                ["merge", "--no-commit", "--no-ff", f"origin/{target_branch}"],
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

    def cleanup_worktree(self, worktree: Worktree) -> bool:
        """
        Remove worktree and clean up filesystem.

        Args:
            worktree: Worktree record

        Returns:
            True if successful, False on error
        """
        try:
            worktree_path = Path(worktree.worktree_path)

            # Verify path is within worktree base directory
            if not str(worktree_path).startswith(str(self.worktree_base)):
                logger.error(f"Worktree path outside base: {worktree_path}")
                return False

            # Remove git worktree
            if worktree_path.exists():
                logger.info(f"Removing worktree: {worktree_path}")
                # silent-ok: removal is verified below — if the path still
                # exists the shutil fallback removes it, so a failure here has a
                # real alternate path rather than passing unnoticed
                run_git(
                    ["worktree", "remove", "--force", str(worktree_path)],
                    cwd=str(self.repo_path),
                    check=False,  # Don't fail if worktree already gone
                    capture_output=True,
                )

                # Remove directory if still exists
                if worktree_path.exists():
                    import shutil

                    shutil.rmtree(worktree_path)

            # Update record
            worktree.state = WorktreeState.CLEANUP
            worktree.cleaned_at = datetime.now(timezone.utc)
            self.session.add(worktree)
            self.session.commit()

            logger.info(f"Cleaned up worktree {worktree.id}")
            return True

        except Exception as e:
            logger.error(f"Error cleaning up worktree: {e}", exc_info=True)
            return False

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
