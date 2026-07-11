"""Git worktree service for parallel agent execution isolation."""

import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from loregarden.models.domain import (
    AgentRun,
    Worktree,
    WorktreeState,
)
from sqlmodel import Session, select

logger = logging.getLogger(__name__)


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
    ) -> Worktree | None:
        """
        Create an isolated git worktree for an agent run.

        Args:
            workspace_id: Workspace ID
            agent_run_id: Agent run ID
            parent_branch: Branch to base worktree on (default: main)

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

            # Create worktree: git worktree add <path> <branch>
            logger.info(f"Creating worktree: {worktree_path} from {parent_branch}")
            subprocess.run(
                ["git", "worktree", "add", str(worktree_path), parent_branch],
                cwd=str(self.repo_path),
                check=True,
                capture_output=True,
            )

            # Get current commit on new worktree (merge base)
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
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

    def detect_conflicts(self, worktree: Worktree, target_branch: str = "main") -> bool:
        """
        Check if merging worktree back to target branch would cause conflicts.

        Args:
            worktree: Worktree record
            target_branch: Branch to merge into (default: main)

        Returns:
            True if conflicts detected, False otherwise
        """
        try:
            worktree_path = Path(worktree.worktree_path)

            # Fetch latest from remote
            subprocess.run(
                ["git", "fetch", "origin"],
                cwd=str(worktree_path),
                check=False,
                capture_output=True,
            )

            # Try dry-run merge to detect conflicts
            # git merge --no-commit --no-ff origin/target_branch
            result = subprocess.run(
                ["git", "merge", "--no-commit", "--no-ff", f"origin/{target_branch}"],
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
                subprocess.run(
                    ["git", "merge", "--abort"],
                    cwd=str(worktree_path),
                    check=False,
                    capture_output=True,
                )

            self.session.add(worktree)
            self.session.commit()

            return has_conflicts

        except Exception as e:
            logger.error(f"Error detecting conflicts: {e}", exc_info=True)
            return False

    def _extract_conflict_files(self, worktree_path: Path) -> list[str]:
        """Extract list of files with merge conflicts."""
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", "--diff-filter=U"],
                cwd=str(worktree_path),
                check=True,
                capture_output=True,
                text=True,
            )
            files = result.stdout.strip().split("\n")
            return [f for f in files if f]  # Filter empty strings
        except Exception as e:
            logger.error(f"Error extracting conflict files: {e}")
            return []

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
            result = subprocess.run(
                ["git", "status", "--porcelain"],
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
            subprocess.run(
                ["git", "checkout", target_branch],
                cwd=str(self.repo_path),
                check=True,
                capture_output=True,
            )

            # Merge worktree branch into main
            # Validate the worktree HEAD resolves to a branch before merging
            subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(worktree_path),
                check=True,
                capture_output=True,
                text=True,
            )

            # Cherry-pick or merge commits from worktree
            result = subprocess.run(
                ["git", "merge", f"{worktree_path.name}"],
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

        except Exception as e:
            logger.error(f"Error merging worktree: {e}", exc_info=True)
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
                subprocess.run(
                    ["git", "checkout", "--ours", file_path],
                    cwd=str(worktree_path),
                    check=True,
                    capture_output=True,
                )

            # Stage resolved files
            subprocess.run(
                ["git", "add"] + conflict_files,
                cwd=str(worktree_path),
                check=True,
                capture_output=True,
            )

            # Complete merge
            subprocess.run(
                ["git", "commit", "-m", "Auto-resolved merge conflicts"],
                cwd=str(worktree_path),
                check=True,
                capture_output=True,
            )

            logger.info(f"Auto-resolved conflicts in {len(conflict_files)} files")
            return True

        except Exception as e:
            logger.error(f"Error auto-resolving conflicts: {e}")
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
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(worktree_path)],
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
        return self.session.exec(stmt).all()

    def get_worktrees_by_run(self, agent_run_id: str) -> list[Worktree]:
        """Get all worktrees for an agent run."""
        stmt = select(Worktree).where(Worktree.agent_run_id == agent_run_id)
        return self.session.exec(stmt).all()
