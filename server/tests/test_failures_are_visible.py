"""A failed check must never be reportable as a successful one.

Each test here pins a place where "it broke" and "there is nothing wrong" used to
produce the same value. They are behavioural, not cosmetic: the conflict cases in
particular guard the one call that decides whether a merge may proceed, where
returning the success value on failure meant a broken git call read as permission
to merge.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from loregarden.api.analytics import get_analytics, get_global_analytics
from loregarden.models.domain import Worktree
from loregarden.services.conflict_detector import ConflictDetectorService
from loregarden.services.permission_allowlist import load_allowlist
from loregarden.services.worktree_service import ConflictDetectionError, WorktreeService


def _worktree(tmp_path) -> Worktree:
    return Worktree(
        id="wt-1",
        workspace_id="ws-1",
        agent_run_id="run-1",
        branch_name="feature",
        worktree_path=str(tmp_path),
    )


class TestConflictPreviewReportsUnknown:
    @pytest.mark.asyncio
    async def test_broken_check_is_not_reported_as_clean(self, tmp_path):
        service = ConflictDetectorService(MagicMock(), repo_path=str(tmp_path))

        with patch(
            "loregarden.services.conflict_detector.run_git",
            side_effect=OSError("git is not on PATH"),
        ):
            preview = await service.get_conflict_preview(_worktree(tmp_path))

        assert preview["checked"] is False, "a failed probe must not claim it checked"
        # Fail closed: a caller that ignores `checked` must not auto-merge.
        assert preview["has_conflicts"] is True
        assert preview["auto_mergeable"] is False
        assert "git is not on PATH" in preview["error"]

    @pytest.mark.asyncio
    async def test_clean_merge_is_marked_checked(self, tmp_path):
        service = ConflictDetectorService(MagicMock(), repo_path=str(tmp_path))
        ok = MagicMock(returncode=0, stdout="", stderr="")

        with patch("loregarden.services.conflict_detector.run_git", return_value=ok):
            preview = await service.get_conflict_preview(_worktree(tmp_path))

        assert preview["checked"] is True
        assert preview["has_conflicts"] is False


class TestDetectConflictsRaisesOnFailure:
    def test_failure_raises_rather_than_returning_false(self, tmp_path):
        service = WorktreeService(MagicMock(), repo_path=str(tmp_path))

        with patch(
            "loregarden.services.worktree_service.run_git",
            side_effect=OSError("git is not on PATH"),
        ):
            with pytest.raises(ConflictDetectionError):
                service.detect_conflicts(_worktree(tmp_path))

    def test_clean_merge_still_returns_false(self, tmp_path):
        session = MagicMock()
        service = WorktreeService(session, repo_path=str(tmp_path))
        ok = MagicMock(returncode=0, stdout="", stderr="")

        with patch("loregarden.services.worktree_service.run_git", return_value=ok):
            assert service.detect_conflicts(_worktree(tmp_path)) is False


class TestAnalyticsFailsLoudly:
    """`metrics: []` is a valid answer, so it must not double as the error channel."""

    @pytest.mark.asyncio
    async def test_global_analytics_raises_instead_of_returning_empty(self):
        with patch(
            "loregarden.api.analytics._build_metrics",
            side_effect=RuntimeError("no such column"),
        ):
            with pytest.raises(HTTPException) as caught:
                await get_global_analytics(range="7d", session=MagicMock())

        assert caught.value.status_code == 500
        assert "no such column" in str(caught.value.detail)

    @pytest.mark.asyncio
    async def test_workspace_analytics_raises_instead_of_returning_empty(self):
        with patch(
            "loregarden.api.analytics._build_metrics",
            side_effect=RuntimeError("no such column"),
        ):
            with pytest.raises(HTTPException) as caught:
                await get_analytics(workspace_id="ws-1", range="7d", session=MagicMock())

        assert caught.value.status_code == 500


class TestCorruptAllowlistIsNotSilent:
    def test_unparseable_allowlist_is_logged(self, caplog):
        with caplog.at_level("WARNING"):
            assert load_allowlist("{not json") == []
        assert caplog.records, "a corrupt allowlist must not be discarded silently"

    def test_wrong_shape_allowlist_is_logged(self, caplog):
        with caplog.at_level("WARNING"):
            assert load_allowlist('{"a": 1}') == []
        assert caplog.records

    def test_absent_allowlist_is_quiet(self, caplog):
        """No rules configured is not a failure; it must not produce noise."""
        with caplog.at_level("WARNING"):
            assert load_allowlist(None) == []
        assert not caplog.records
