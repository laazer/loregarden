"""Conflict detection and reporting service."""

import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from loregarden.models.domain import ConflictReport, Worktree
from sqlmodel import Session, select

logger = logging.getLogger(__name__)


class ConflictDetectorService:
    """Detect and report merge conflicts."""

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
                "has_conflicts": bool,
                "conflicting_files": list[str],
                "summary": str,
                "auto_mergeable": bool,
                "conflict_details": dict,
            }
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
            result = subprocess.run(
                ["git", "merge", "--no-commit", "--no-ff", f"origin/{target_branch}"],
                cwd=str(worktree_path),
                capture_output=True,
                text=True,
            )

            has_conflicts = result.returncode != 0

            if has_conflicts:
                # Extract conflicting files
                conflict_files = self._extract_conflict_files(worktree_path)

                # Try to determine if auto-mergeable
                auto_mergeable = self._check_auto_mergeable(
                    worktree_path, conflict_files
                )

                # Abort the dry-run merge
                subprocess.run(
                    ["git", "merge", "--abort"],
                    cwd=str(worktree_path),
                    check=False,
                    capture_output=True,
                )

                summary = f"Merge conflicts in {len(conflict_files)} file"
                if len(conflict_files) != 1:
                    summary += "s"

                return {
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
                subprocess.run(
                    ["git", "merge", "--abort"],
                    cwd=str(worktree_path),
                    check=False,
                    capture_output=True,
                )

                return {
                    "has_conflicts": False,
                    "conflicting_files": [],
                    "summary": f"Clean merge with {target_branch}",
                    "auto_mergeable": True,
                    "conflict_details": {},
                }

        except Exception as e:
            logger.error(f"Error checking conflict preview: {e}", exc_info=True)
            return {
                "has_conflicts": False,
                "conflicting_files": [],
                "summary": f"Error checking conflicts: {str(e)}",
                "auto_mergeable": False,
                "error": str(e),
            }

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
                file_path.endswith(pattern.replace("*", ""))
                for pattern in auto_merge_patterns
            )
            if not is_mergeable:
                # Check if conflict is simple (only whitespace/formatting)
                if not self._is_simple_conflict(worktree_path, file_path):
                    return False

        return True

    def _is_simple_conflict(self, worktree_path: Path, file_path: str) -> bool:
        """Check if conflict is simple (whitespace/formatting only)."""
        try:
            result = subprocess.run(
                ["git", "diff", "--", file_path],
                cwd=str(worktree_path),
                capture_output=True,
                text=True,
            )

            diff_output = result.stdout
            # If diff is mostly conflict markers and whitespace, it's simple
            lines_with_code = [
                line for line in diff_output.split("\n")
                if line.strip() and not line.startswith(("<<<<<<", ">>>>>>", "======"))
            ]

            return len(lines_with_code) < 5

        except Exception:
            return False

    async def get_conflict_details(
        self,
        worktree: Worktree,
        target_branch: str = "main",
    ) -> dict:
        """
        Get detailed conflict information.

        Args:
            worktree: Worktree to check
            target_branch: Branch to check against

        Returns:
            {
                "conflicts": [
                    {
                        "file": "src/auth.ts",
                        "ours_lines": 10,
                        "theirs_lines": 5,
                        "preview": "<<<<<<< HEAD\n...",
                    },
                    ...
                ],
                "suggestions": list[str],
                "severity": "low" | "medium" | "high",
            }
        """
        try:
            worktree_path = Path(worktree.worktree_path)

            preview = await self.get_conflict_preview(worktree, target_branch)
            if not preview["has_conflicts"]:
                return {
                    "conflicts": [],
                    "suggestions": ["Clean merge possible"],
                    "severity": "low",
                }

            conflict_files = preview["conflicting_files"]
            conflicts = []

            for file_path in conflict_files:
                conflict_info = self._get_file_conflict_details(
                    worktree_path, file_path
                )
                conflicts.append(conflict_info)

            suggestions = self._generate_suggestions(conflicts, preview)
            severity = self._assess_severity(conflicts)

            return {
                "conflicts": conflicts,
                "suggestions": suggestions,
                "severity": severity,
                "total_conflicts": len(conflicts),
            }

        except Exception as e:
            logger.error(f"Error getting conflict details: {e}", exc_info=True)
            return {
                "conflicts": [],
                "suggestions": [f"Error analyzing conflicts: {str(e)}"],
                "severity": "high",
                "error": str(e),
            }

    def _get_file_conflict_details(
        self,
        worktree_path: Path,
        file_path: str,
    ) -> dict:
        """Get conflict details for a specific file."""
        try:
            # Read file to get conflict markers
            file_full_path = worktree_path / file_path
            if not file_full_path.exists():
                return {
                    "file": file_path,
                    "status": "deleted_in_one_branch",
                    "ours_lines": 0,
                    "theirs_lines": 0,
                    "preview": "",
                }

            with open(file_full_path) as f:
                content = f.read()

            # Extract conflict sections
            ours_count = content.count("<<<<<<< HEAD")
            theirs_count = content.count(">>>>>>> ")

            # Get preview (first 500 chars of file)
            preview = content[:500]
            if len(content) > 500:
                preview += "\n... (truncated)"

            return {
                "file": file_path,
                "status": "conflicted",
                "ours_lines": ours_count,
                "theirs_lines": theirs_count,
                "preview": preview,
            }

        except Exception as e:
            logger.error(f"Error getting file conflict details: {e}")
            return {
                "file": file_path,
                "status": "error",
                "error": str(e),
            }

    def _generate_suggestions(
        self,
        conflicts: list[dict],
        preview: dict,
    ) -> list[str]:
        """Generate resolution suggestions."""
        suggestions = []

        if not conflicts:
            return ["Clean merge possible"]

        # Suggest based on conflict count
        if len(conflicts) == 1:
            suggestions.append(f"Only 1 file conflicts ({conflicts[0]['file']})")
            suggestions.append("Consider resolving manually then pushing")
        elif len(conflicts) <= 3:
            suggestions.append(f"{len(conflicts)} files conflict")
            suggestions.append("Review and resolve each conflict")
        else:
            suggestions.append(f"Multiple ({len(conflicts)}) files conflict")
            suggestions.append("Consider rebasing or cherry-picking instead of merge")

        # Suggest based on auto-mergeable
        if preview.get("auto_mergeable"):
            suggestions.append("✓ Can auto-merge (conflicts in low-risk files)")
        else:
            suggestions.append("⚠️ Cannot auto-merge (conflicts in critical code)")
            suggestions.append("Manual resolution required")

        return suggestions

    def _assess_severity(self, conflicts: list[dict]) -> str:
        """Assess conflict severity."""
        if not conflicts:
            return "low"

        # Count conflicts in critical files
        critical_patterns = [
            ".py", ".ts", ".js", ".jsx", ".tsx",
            ".go", ".rust", ".java", ".cpp", ".c",
        ]

        critical_conflicts = sum(
            1 for c in conflicts
            if any(c["file"].endswith(pat) for pat in critical_patterns)
        )

        if critical_conflicts == 0:
            return "low"
        elif critical_conflicts <= 2:
            return "medium"
        else:
            return "high"

    async def create_conflict_report(
        self,
        worktree_id: str,
        ticket_id: str,
        conflict_preview: dict,
    ) -> str | None:
        """
        Create a conflict report record.

        Args:
            worktree_id: Worktree ID
            ticket_id: Ticket ID
            conflict_preview: Result from get_conflict_preview()

        Returns:
            Report ID if created, None on error
        """
        try:
            report = ConflictReport(
                id=str(uuid4()),
                worktree_id=worktree_id,
                ticket_id=ticket_id,
                merge_attempt_number=1,
                conflict_type="merge_conflict",
                conflicting_files=conflict_preview.get("conflicting_files", []),
                conflict_details=conflict_preview.get("summary", ""),
                resolution_attempted=False,
                created_at=datetime.now(timezone.utc),
            )

            self.session.add(report)
            self.session.commit()

            logger.info(f"Created conflict report {report.id}")
            return report.id

        except Exception as e:
            logger.error(f"Error creating conflict report: {e}", exc_info=True)
            return None

    def get_conflict_report(self, report_id: str) -> ConflictReport | None:
        """Fetch a conflict report."""
        stmt = select(ConflictReport).where(ConflictReport.id == report_id)
        return self.session.exec(stmt).first()

    def get_worktree_conflicts(self, worktree_id: str) -> list[ConflictReport]:
        """Get all conflict reports for a worktree."""
        stmt = select(ConflictReport).where(
            ConflictReport.worktree_id == worktree_id
        ).order_by(ConflictReport.created_at.desc())
        return self.session.exec(stmt).all()
