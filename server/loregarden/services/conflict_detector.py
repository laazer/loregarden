"""Whether a worktree would merge cleanly, and whether that could be determined."""
# kept-for: lg-milestone-that-2266 - its criteria reuse get_conflict_preview's `checked` flag

import logging
from pathlib import Path

from loregarden.models.domain import Worktree
from loregarden.services.git_subprocess import run_git
from loregarden.services.publish_ref import comparison_ref
from sqlmodel import Session

logger = logging.getLogger(__name__)


class ConflictDetectorService:
    """Detect merge conflicts."""

    def __init__(self, session: Session, repo_path: str):
        self.session = session
        self.repo_path = Path(repo_path).resolve()

    async def get_conflict_preview(
        self,
        worktree: Worktree,
        target_branch: str = "main",
    ) -> dict:
        """
        Detect and report conflicts without actually merging.

        Args:
            worktree: Worktree to check for conflicts
            target_branch: Branch to check conflicts against (default: main)

        Returns:
            {
                "checked": bool,        # False when the check itself failed
                "has_conflicts": bool,
                "conflicting_files": list[str],
                "summary": str,
                "auto_mergeable": bool,
                "conflict_details": dict,
            }

        ``checked`` exists because "no conflicts" and "could not tell" are
        different answers that used to look identical: a git failure returned
        ``has_conflicts: False``, which the API reported as "Ready to merge".
        Callers must treat ``checked=False`` as unknown, never as clean.
        """
        try:
            worktree_path = Path(worktree.worktree_path)

            # A failed fetch is not fatal, but every merge-base below is then
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
                    "fetch origin failed in %s (rc=%s); conflict preview is computed "
                    "against a possibly stale origin/%s",
                    worktree_path,
                    fetch.returncode,
                    target_branch,
                )

            # Try dry-run merge to detect conflicts
            result = run_git(
                ["merge", "--no-commit", "--no-ff", comparison_ref(target_branch)],
                cwd=str(worktree_path),
                capture_output=True,
                text=True,
            )

            has_conflicts = result.returncode != 0

            if has_conflicts:
                # Extract conflicting files
                conflict_files = self._extract_conflict_files(worktree_path)

                # Try to determine if auto-mergeable
                auto_mergeable = self._check_auto_mergeable(worktree_path, conflict_files)

                # Abort the dry-run merge
                # silent-ok: undoing a merge we only started to probe; if it did
                # not start there is nothing to abort, and the next probe re-runs
                run_git(
                    ["merge", "--abort"],
                    cwd=str(worktree_path),
                    check=False,
                    capture_output=True,
                )

                summary = f"Merge conflicts in {len(conflict_files)} file"
                if len(conflict_files) != 1:
                    summary += "s"

                return {
                    "checked": True,
                    "has_conflicts": True,
                    "conflicting_files": conflict_files,
                    "summary": summary,
                    "auto_mergeable": auto_mergeable,
                    "conflict_details": {
                        "conflict_count": len(conflict_files),
                        "merge_base": worktree.merge_base,
                        "target_branch": target_branch,
                    },
                }
            else:
                # No conflicts, abort merge
                # silent-ok: undoing a merge we only started to probe; if it did
                # not start there is nothing to abort, and the next probe re-runs
                run_git(
                    ["merge", "--abort"],
                    cwd=str(worktree_path),
                    check=False,
                    capture_output=True,
                )

                return {
                    "checked": True,
                    "has_conflicts": False,
                    "conflicting_files": [],
                    "summary": f"Clean merge with {target_branch}",
                    "auto_mergeable": True,
                    "conflict_details": {},
                }

        except Exception as e:  # noqa: BLE001 - boundary: any failure means "unknown"
            logger.exception("Error checking conflict preview for %s", worktree.worktree_path)
            # Fail closed. `has_conflicts: True` with `checked: False` keeps a
            # caller that ignores `checked` from auto-merging on a broken probe.
            return {
                "checked": False,
                "has_conflicts": True,
                "conflicting_files": [],
                "summary": f"Could not check for conflicts: {e}",
                "auto_mergeable": False,
                "error": str(e),
            }

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
            # propagate: get_conflict_preview turns it into checked=False.
            logger.exception("Error extracting conflict files from %s", worktree_path)
            raise

    def _check_auto_mergeable(
        self,
        worktree_path: Path,
        conflict_files: list[str],
    ) -> bool:
        """
        Check if conflicts can be auto-merged.

        Returns True if:
        - No conflicts, or
        - All conflicts are in non-critical files
        """
        if not conflict_files:
            return True

        # Files that can usually be auto-merged
        auto_merge_patterns = [
            "*.json",
            "*.lock",
            "*.md",
            "CHANGELOG*",
        ]

        for file_path in conflict_files:
            # Check if file matches auto-merge pattern
            is_mergeable = any(
                file_path.endswith(pattern.replace("*", "")) for pattern in auto_merge_patterns
            )
            if not is_mergeable:
                # Check if conflict is simple (only whitespace/formatting)
                if not self._is_simple_conflict(worktree_path, file_path):
                    return False

        return True

    def _is_simple_conflict(self, worktree_path: Path, file_path: str) -> bool:
        """Check if conflict is simple (whitespace/formatting only)."""
        try:
            result = run_git(
                ["diff", "--", file_path],
                cwd=str(worktree_path),
                capture_output=True,
                text=True,
            )

            diff_output = result.stdout
            # If diff is mostly conflict markers and whitespace, it's simple
            lines_with_code = [
                line
                for line in diff_output.split("\n")
                if line.strip() and not line.startswith(("<<<<<<", ">>>>>>", "======"))
            ]

            return len(lines_with_code) < 5

        except Exception:  # noqa: BLE001 - boundary: any failure means "not simple"
            # Fail closed: "not simple" keeps the conflict out of auto-resolution.
            logger.exception("Error inspecting conflict in %s", file_path)
            return False
