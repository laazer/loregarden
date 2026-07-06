"""Unit tests for WorktreeService."""

import pytest
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

from sqlmodel import Session

from loregarden.models.domain import (
    AgentRun,
    RunStatus,
    Ticket,
    TicketState,
    Workspace,
    WorkItemType,
    Worktree,
    WorktreeState,
    ConflictReport,
)
from loregarden.services.worktree_service import WorktreeService


@pytest.fixture
def workspace(session: Session) -> Workspace:
    """Create test workspace."""
    ws = Workspace(
        id=str(uuid4()),
        slug="test-workspace",
        name="Test Workspace",
    )
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture
def ticket(session: Session, workspace: Workspace) -> Ticket:
    """Create test ticket."""
    ticket = Ticket(
        id=str(uuid4()),
        external_id="feature-123",
        workspace_id=workspace.id,
        title="Test feature",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.FEATURE,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


@pytest.fixture
def agent_run(session: Session, workspace: Workspace, ticket: Ticket) -> AgentRun:
    """Create test agent run."""
    run = AgentRun(
        id=str(uuid4()),
        workspace_id=workspace.id,
        ticket_id=ticket.id,
        agent_id="test-agent",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


@pytest.fixture
def worktree_service(session: Session, tmp_path: Path) -> WorktreeService:
    """Create WorktreeService with temp repo path."""
    repo_path = tmp_path / "test-repo"
    repo_path.mkdir()
    return WorktreeService(session, str(repo_path))


class TestCreateWorktree:
    """Test worktree creation."""

    def test_create_worktree_success(
        self, worktree_service: WorktreeService, agent_run: AgentRun
    ):
        """Test successful worktree creation."""
        with patch("subprocess.run") as mock_run, patch.object(
            Path, "mkdir"
        ) as mock_mkdir:
            # Mock git worktree add
            mock_run.return_value = MagicMock(
                stdout="abc123def456\n", returncode=0, text=""
            )

            worktree = worktree_service.create_worktree(
                workspace_id=agent_run.workspace_id,
                agent_run_id=agent_run.id,
                parent_branch="main",
            )

            assert worktree is not None
            assert worktree.workspace_id == agent_run.workspace_id
            assert worktree.agent_run_id == agent_run.id
            assert worktree.parent_branch == "main"
            assert worktree.state == WorktreeState.ACTIVE
            assert worktree.merge_base == "abc123def456"

    def test_create_worktree_agent_run_not_found(
        self, worktree_service: WorktreeService
    ):
        """Test worktree creation with invalid agent run."""
        worktree = worktree_service.create_worktree(
            workspace_id="invalid-workspace",
            agent_run_id="invalid-run",
            parent_branch="main",
        )

        assert worktree is None

    def test_create_worktree_git_command_fails(
        self,
        worktree_service: WorktreeService,
        agent_run: AgentRun,
    ):
        """Test worktree creation when git command fails."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                1, "git", stderr="fatal: branch 'main' not found"
            )

            worktree = worktree_service.create_worktree(
                workspace_id=agent_run.workspace_id,
                agent_run_id=agent_run.id,
                parent_branch="nonexistent",
            )

            assert worktree is None


class TestDetectConflicts:
    """Test conflict detection."""

    def test_detect_conflicts_no_conflicts(
        self, worktree_service: WorktreeService, session: Session, agent_run: AgentRun
    ):
        """Test conflict detection when no conflicts exist."""
        # Create worktree record
        worktree = Worktree(
            id=str(uuid4()),
            workspace_id=agent_run.workspace_id,
            agent_run_id=agent_run.id,
            parent_branch="main",
            worktree_path="/tmp/test-worktree",
            state=WorktreeState.ACTIVE,
        )
        session.add(worktree)
        session.commit()

        with patch("subprocess.run") as mock_run:
            # First call: git fetch
            # Second call: git merge --no-commit (no conflicts, return code 0)
            # Third call: git merge --abort
            mock_run.side_effect = [
                MagicMock(returncode=0),  # fetch
                MagicMock(returncode=0),  # merge dry-run
                MagicMock(returncode=0),  # merge abort
            ]

            has_conflicts = worktree_service.detect_conflicts(worktree, "main")

            assert has_conflicts is False
            assert worktree.has_conflicts is False
            assert worktree.conflict_files == []

    def test_detect_conflicts_with_conflicts(
        self, worktree_service: WorktreeService, session: Session, agent_run: AgentRun
    ):
        """Test conflict detection when conflicts exist."""
        worktree = Worktree(
            id=str(uuid4()),
            workspace_id=agent_run.workspace_id,
            agent_run_id=agent_run.id,
            parent_branch="main",
            worktree_path="/tmp/test-worktree",
            state=WorktreeState.ACTIVE,
        )
        session.add(worktree)
        session.commit()

        with patch("subprocess.run") as mock_run, patch.object(
            worktree_service, "_extract_conflict_files", return_value=["file1.py", "file2.py"]
        ):
            # First call: git fetch
            # Second call: git merge --no-commit (returns 1 for conflicts)
            mock_run.side_effect = [
                MagicMock(returncode=0),  # fetch
                MagicMock(returncode=1, stderr="CONFLICT (content)"),  # merge fails
            ]

            has_conflicts = worktree_service.detect_conflicts(worktree, "main")

            assert has_conflicts is True
            assert worktree.has_conflicts is True
            assert worktree.conflict_files == ["file1.py", "file2.py"]
            assert "2 files" in worktree.conflict_summary


class TestMergeWorktree:
    """Test worktree merging."""

    def test_merge_worktree_no_changes(
        self, worktree_service: WorktreeService, session: Session, agent_run: AgentRun
    ):
        """Test merge when worktree has no changes."""
        worktree = Worktree(
            id=str(uuid4()),
            workspace_id=agent_run.workspace_id,
            agent_run_id=agent_run.id,
            parent_branch="main",
            worktree_path="/tmp/test-worktree",
            state=WorktreeState.ACTIVE,
        )
        session.add(worktree)
        session.commit()

        with patch("subprocess.run") as mock_run:
            # git status --porcelain returns empty
            mock_run.return_value = MagicMock(stdout="", returncode=0)

            result = worktree_service.merge_worktree(worktree, "main")

            assert result is True
            assert worktree.state == WorktreeState.MERGED
            assert worktree.merged_at is not None

    def test_merge_worktree_with_conflicts_no_auto_resolve(
        self, worktree_service: WorktreeService, session: Session, agent_run: AgentRun
    ):
        """Test merge fails when conflicts exist and auto-resolve is False."""
        worktree = Worktree(
            id=str(uuid4()),
            workspace_id=agent_run.workspace_id,
            agent_run_id=agent_run.id,
            parent_branch="main",
            worktree_path="/tmp/test-worktree",
            state=WorktreeState.ACTIVE,
        )
        session.add(worktree)
        session.commit()

        with patch("subprocess.run") as mock_run, patch.object(
            worktree_service, "detect_conflicts", return_value=True
        ):
            # git status (has changes)
            mock_run.return_value = MagicMock(stdout="M file1.py\n", returncode=0)

            result = worktree_service.merge_worktree(worktree, "main", auto_resolve=False)

            assert result is False
            assert worktree.state == WorktreeState.FAILED

    def test_merge_worktree_with_auto_resolve_success(
        self, worktree_service: WorktreeService, session: Session, agent_run: AgentRun
    ):
        """Test merge with auto-resolve succeeds."""
        worktree = Worktree(
            id=str(uuid4()),
            workspace_id=agent_run.workspace_id,
            agent_run_id=agent_run.id,
            parent_branch="main",
            worktree_path="/tmp/test-worktree",
            state=WorktreeState.ACTIVE,
        )
        session.add(worktree)
        session.commit()

        with patch("subprocess.run") as mock_run, patch.object(
            worktree_service, "detect_conflicts", return_value=True
        ), patch.object(
            worktree_service, "_auto_resolve_conflicts", return_value=True
        ), patch.object(
            worktree_service, "_extract_conflict_files", return_value=[]
        ):
            # git status (has changes)
            # git checkout main
            # git rev-parse (get branch name)
            # git merge
            mock_run.side_effect = [
                MagicMock(stdout="M file1.py\n", returncode=0),  # status
                MagicMock(returncode=0),  # checkout main
                MagicMock(stdout="feature-branch\n", returncode=0),  # rev-parse
                MagicMock(returncode=0),  # merge
            ]

            result = worktree_service.merge_worktree(
                worktree, "main", auto_resolve=True
            )

            assert result is True
            assert worktree.state == WorktreeState.MERGED
            assert worktree.merged_at is not None

    def test_merge_worktree_not_active(
        self, worktree_service: WorktreeService, session: Session, agent_run: AgentRun
    ):
        """Test merge fails when worktree not in ACTIVE state."""
        worktree = Worktree(
            id=str(uuid4()),
            workspace_id=agent_run.workspace_id,
            agent_run_id=agent_run.id,
            parent_branch="main",
            worktree_path="/tmp/test-worktree",
            state=WorktreeState.FAILED,
        )
        session.add(worktree)
        session.commit()

        result = worktree_service.merge_worktree(worktree, "main")

        assert result is False


class TestCleanupWorktree:
    """Test worktree cleanup."""

    def test_cleanup_worktree_success(
        self, worktree_service: WorktreeService, session: Session, agent_run: AgentRun
    ):
        """Test successful worktree cleanup."""
        worktree = Worktree(
            id=str(uuid4()),
            workspace_id=agent_run.workspace_id,
            agent_run_id=agent_run.id,
            parent_branch="main",
            worktree_path=str(worktree_service.worktree_base / "test-worktree"),
            state=WorktreeState.MERGED,
        )
        session.add(worktree)
        session.commit()

        with patch("subprocess.run") as mock_run, patch("shutil.rmtree"):
            mock_run.return_value = MagicMock(returncode=0)

            with patch.object(Path, "exists", return_value=True):
                result = worktree_service.cleanup_worktree(worktree)

                assert result is True
                assert worktree.state == WorktreeState.CLEANUP
                assert worktree.cleaned_at is not None

    def test_cleanup_worktree_invalid_path(
        self, worktree_service: WorktreeService, session: Session, agent_run: AgentRun
    ):
        """Test cleanup fails for path outside base directory."""
        worktree = Worktree(
            id=str(uuid4()),
            workspace_id=agent_run.workspace_id,
            agent_run_id=agent_run.id,
            parent_branch="main",
            worktree_path="/etc/passwd",  # Outside base directory
            state=WorktreeState.MERGED,
        )
        session.add(worktree)
        session.commit()

        result = worktree_service.cleanup_worktree(worktree)

        assert result is False

    def test_cleanup_worktree_git_command_fails(
        self, worktree_service: WorktreeService, session: Session, agent_run: AgentRun
    ):
        """Test cleanup handles git command failure gracefully."""
        worktree = Worktree(
            id=str(uuid4()),
            workspace_id=agent_run.workspace_id,
            agent_run_id=agent_run.id,
            parent_branch="main",
            worktree_path=str(worktree_service.worktree_base / "test-worktree"),
            state=WorktreeState.MERGED,
        )
        session.add(worktree)
        session.commit()

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                1, "git", stderr="worktree not found"
            )

            result = worktree_service.cleanup_worktree(worktree)

            assert result is False


class TestAutoResolveConflicts:
    """Test auto-conflict resolution."""

    def test_auto_resolve_conflicts_success(
        self, worktree_service: WorktreeService
    ):
        """Test successful auto-resolution of conflicts."""
        worktree_path = Path("/tmp/test-worktree")

        with patch("subprocess.run") as mock_run, patch.object(
            worktree_service, "_extract_conflict_files", return_value=["file1.py"]
        ):
            # git checkout --ours
            # git add
            # git commit
            mock_run.side_effect = [
                MagicMock(returncode=0),  # checkout
                MagicMock(returncode=0),  # add
                MagicMock(returncode=0),  # commit
            ]

            result = worktree_service._auto_resolve_conflicts(worktree_path)

            assert result is True

    def test_auto_resolve_conflicts_command_fails(
        self, worktree_service: WorktreeService
    ):
        """Test auto-resolve fails when git command fails."""
        worktree_path = Path("/tmp/test-worktree")

        with patch("subprocess.run") as mock_run, patch.object(
            worktree_service, "_extract_conflict_files", return_value=["file1.py"]
        ):
            mock_run.side_effect = subprocess.CalledProcessError(
                1, "git", stderr="merge error"
            )

            result = worktree_service._auto_resolve_conflicts(worktree_path)

            assert result is False


class TestExtractConflictFiles:
    """Test conflict file extraction."""

    def test_extract_conflict_files_success(
        self, worktree_service: WorktreeService
    ):
        """Test extracting conflicting files."""
        worktree_path = Path("/tmp/test-worktree")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="file1.py\nfile2.js\ndir/file3.tsx\n", returncode=0
            )

            files = worktree_service._extract_conflict_files(worktree_path)

            assert files == ["file1.py", "file2.js", "dir/file3.tsx"]

    def test_extract_conflict_files_empty(
        self, worktree_service: WorktreeService
    ):
        """Test extracting when no conflicts."""
        worktree_path = Path("/tmp/test-worktree")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)

            files = worktree_service._extract_conflict_files(worktree_path)

            assert files == []


class TestQueryMethods:
    """Test worktree query methods."""

    def test_get_worktree(
        self, worktree_service: WorktreeService, session: Session, agent_run: AgentRun
    ):
        """Test fetching worktree by ID."""
        worktree = Worktree(
            id=str(uuid4()),
            workspace_id=agent_run.workspace_id,
            agent_run_id=agent_run.id,
            parent_branch="main",
            worktree_path="/tmp/test",
            state=WorktreeState.ACTIVE,
        )
        session.add(worktree)
        session.commit()

        fetched = worktree_service.get_worktree(worktree.id)

        assert fetched is not None
        assert fetched.id == worktree.id

    def test_get_active_worktrees(
        self, worktree_service: WorktreeService, session: Session, workspace: Workspace, agent_run: AgentRun
    ):
        """Test fetching active worktrees."""
        # Create multiple worktrees with different states
        wt1 = Worktree(
            id=str(uuid4()),
            workspace_id=workspace.id,
            agent_run_id=agent_run.id,
            parent_branch="main",
            worktree_path="/tmp/test1",
            state=WorktreeState.ACTIVE,
        )
        wt2 = Worktree(
            id=str(uuid4()),
            workspace_id=workspace.id,
            agent_run_id=agent_run.id,
            parent_branch="main",
            worktree_path="/tmp/test2",
            state=WorktreeState.MERGED,
        )
        session.add_all([wt1, wt2])
        session.commit()

        active = worktree_service.get_active_worktrees(workspace.id)

        assert len(active) == 1
        assert active[0].id == wt1.id

    def test_get_worktrees_by_run(
        self, worktree_service: WorktreeService, session: Session, agent_run: AgentRun
    ):
        """Test fetching worktrees by agent run."""
        wt1 = Worktree(
            id=str(uuid4()),
            workspace_id=agent_run.workspace_id,
            agent_run_id=agent_run.id,
            parent_branch="main",
            worktree_path="/tmp/test1",
            state=WorktreeState.ACTIVE,
        )
        wt2 = Worktree(
            id=str(uuid4()),
            workspace_id=agent_run.workspace_id,
            agent_run_id=str(uuid4()),  # Different run
            parent_branch="main",
            worktree_path="/tmp/test2",
            state=WorktreeState.ACTIVE,
        )
        session.add_all([wt1, wt2])
        session.commit()

        worktrees = worktree_service.get_worktrees_by_run(agent_run.id)

        assert len(worktrees) == 1
        assert worktrees[0].id == wt1.id


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
