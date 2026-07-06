"""Unit tests for ConflictDetectorService."""

import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
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
)
from loregarden.services.conflict_detector import ConflictDetectorService


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
def worktree(session: Session, workspace: Workspace, agent_run: AgentRun) -> Worktree:
    """Create test worktree."""
    wt = Worktree(
        id=str(uuid4()),
        workspace_id=workspace.id,
        agent_run_id=agent_run.id,
        parent_branch="main",
        worktree_path="/tmp/test-worktree",
        state=WorktreeState.ACTIVE,
        merge_base="abc123def456",
    )
    session.add(wt)
    session.commit()
    session.refresh(wt)
    return wt


@pytest.fixture
def conflict_detector_service(session: Session) -> ConflictDetectorService:
    """Create ConflictDetectorService."""
    return ConflictDetectorService(session, repo_path="/tmp/test-repo")


class TestGetConflictPreview:
    """Test conflict preview detection."""

    @pytest.mark.asyncio
    async def test_get_conflict_preview_no_conflicts(
        self,
        conflict_detector_service: ConflictDetectorService,
        worktree: Worktree,
    ):
        """Test conflict preview when no conflicts exist."""
        with patch("subprocess.run") as mock_run:
            # First call: git fetch
            # Second call: git merge --no-commit (no conflicts, return code 0)
            # Third call: git merge --abort
            mock_run.side_effect = [
                MagicMock(returncode=0),  # fetch
                MagicMock(returncode=0),  # merge dry-run
                MagicMock(returncode=0),  # merge abort
            ]

            result = await conflict_detector_service.get_conflict_preview(
                worktree, "main"
            )

            assert result["has_conflicts"] is False
            assert result["conflicting_files"] == []
            assert result["auto_mergeable"] is True
            assert "clean merge" in result["summary"].lower()

    @pytest.mark.asyncio
    async def test_get_conflict_preview_with_conflicts(
        self,
        conflict_detector_service: ConflictDetectorService,
        worktree: Worktree,
    ):
        """Test conflict preview when conflicts exist."""
        with patch("subprocess.run") as mock_run, patch.object(
            conflict_detector_service,
            "_extract_conflict_files",
            return_value=["src/auth.ts", "src/db.ts"],
        ), patch.object(
            conflict_detector_service,
            "_check_auto_mergeable",
            return_value=False,
        ):
            # First call: git fetch
            # Second call: git merge --no-commit (conflicts, return code 1)
            mock_run.side_effect = [
                MagicMock(returncode=0),  # fetch
                MagicMock(returncode=1, stderr="CONFLICT (content)"),  # merge
            ]

            result = await conflict_detector_service.get_conflict_preview(
                worktree, "main"
            )

            assert result["has_conflicts"] is True
            assert result["conflicting_files"] == ["src/auth.ts", "src/db.ts"]
            assert result["auto_mergeable"] is False
            assert "2 files" in result["summary"]


class TestGetConflictDetails:
    """Test detailed conflict information."""

    @pytest.mark.asyncio
    async def test_get_conflict_details_no_conflicts(
        self,
        conflict_detector_service: ConflictDetectorService,
        worktree: Worktree,
    ):
        """Test conflict details when no conflicts."""
        with patch.object(
            conflict_detector_service,
            "get_conflict_preview",
            return_value={
                "has_conflicts": False,
                "conflicting_files": [],
                "auto_mergeable": True,
            },
        ):
            result = await conflict_detector_service.get_conflict_details(
                worktree, "main"
            )

            assert result["conflicts"] == []
            assert result["severity"] == "low"
            assert len(result["suggestions"]) > 0

    @pytest.mark.asyncio
    async def test_get_conflict_details_with_conflicts(
        self,
        conflict_detector_service: ConflictDetectorService,
        worktree: Worktree,
    ):
        """Test conflict details with conflicts."""
        with patch.object(
            conflict_detector_service,
            "get_conflict_preview",
            return_value={
                "has_conflicts": True,
                "conflicting_files": ["src/auth.ts"],
                "auto_mergeable": False,
            },
        ), patch.object(
            conflict_detector_service,
            "_get_file_conflict_details",
            return_value={
                "file": "src/auth.ts",
                "status": "conflicted",
                "ours_lines": 2,
                "theirs_lines": 1,
                "preview": "<<<<<<< HEAD\n...",
            },
        ), patch.object(
            conflict_detector_service,
            "_assess_severity",
            return_value="high",
        ):
            result = await conflict_detector_service.get_conflict_details(
                worktree, "main"
            )

            assert len(result["conflicts"]) == 1
            assert result["conflicts"][0]["file"] == "src/auth.ts"
            assert result["severity"] == "high"
            assert len(result["suggestions"]) > 0


class TestExtractConflictFiles:
    """Test conflict file extraction."""

    def test_extract_conflict_files_success(
        self,
        conflict_detector_service: ConflictDetectorService,
    ):
        """Test extracting conflicting files."""
        worktree_path = Path("/tmp/test-worktree")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="src/auth.ts\nsrc/db.ts\npackage.json\n", returncode=0
            )

            files = conflict_detector_service._extract_conflict_files(worktree_path)

            assert files == ["src/auth.ts", "src/db.ts", "package.json"]

    def test_extract_conflict_files_empty(
        self,
        conflict_detector_service: ConflictDetectorService,
    ):
        """Test extracting when no conflicts."""
        worktree_path = Path("/tmp/test-worktree")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)

            files = conflict_detector_service._extract_conflict_files(worktree_path)

            assert files == []


class TestCheckAutoMergeable:
    """Test auto-merge detection."""

    def test_check_auto_mergeable_no_conflicts(
        self,
        conflict_detector_service: ConflictDetectorService,
    ):
        """Test auto-merge when no conflicts."""
        worktree_path = Path("/tmp/test-worktree")

        result = conflict_detector_service._check_auto_mergeable(worktree_path, [])

        assert result is True

    def test_check_auto_mergeable_json_files(
        self,
        conflict_detector_service: ConflictDetectorService,
    ):
        """Test auto-merge with JSON files."""
        worktree_path = Path("/tmp/test-worktree")

        with patch.object(
            conflict_detector_service,
            "_is_simple_conflict",
            return_value=True,
        ):
            result = conflict_detector_service._check_auto_mergeable(
                worktree_path, ["package.json"]
            )

            assert result is True

    def test_check_auto_mergeable_code_files(
        self,
        conflict_detector_service: ConflictDetectorService,
    ):
        """Test auto-merge with code files."""
        worktree_path = Path("/tmp/test-worktree")

        with patch.object(
            conflict_detector_service,
            "_is_simple_conflict",
            return_value=False,
        ):
            result = conflict_detector_service._check_auto_mergeable(
                worktree_path, ["src/auth.ts"]
            )

            assert result is False


class TestGetFileConflictDetails:
    """Test file-level conflict details."""

    def test_get_file_conflict_details_success(
        self,
        conflict_detector_service: ConflictDetectorService,
        tmp_path: Path,
    ):
        """Test getting file conflict details."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()

        file_path = worktree_path / "src" / "auth.ts"
        file_path.parent.mkdir(parents=True)
        file_path.write_text("<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> branch\n")

        result = conflict_detector_service._get_file_conflict_details(
            worktree_path, "src/auth.ts"
        )

        assert result["file"] == "src/auth.ts"
        assert result["status"] == "conflicted"
        assert result["ours_lines"] == 1
        assert "<<<<<<< HEAD" in result["preview"]

    def test_get_file_conflict_details_deleted(
        self,
        conflict_detector_service: ConflictDetectorService,
        tmp_path: Path,
    ):
        """Test file conflict for deleted file."""
        worktree_path = tmp_path / "worktree"
        worktree_path.mkdir()

        result = conflict_detector_service._get_file_conflict_details(
            worktree_path, "deleted.ts"
        )

        assert result["status"] == "deleted_in_one_branch"


class TestGenerateSuggestions:
    """Test suggestion generation."""

    def test_generate_suggestions_single_conflict(
        self,
        conflict_detector_service: ConflictDetectorService,
    ):
        """Test suggestions for single conflict."""
        conflicts = [{"file": "src/auth.ts", "status": "conflicted"}]
        preview = {"auto_mergeable": False}

        suggestions = conflict_detector_service._generate_suggestions(
            conflicts, preview
        )

        assert len(suggestions) > 0
        assert any("1 file" in s for s in suggestions)

    def test_generate_suggestions_multiple_conflicts(
        self,
        conflict_detector_service: ConflictDetectorService,
    ):
        """Test suggestions for multiple conflicts."""
        conflicts = [
            {"file": "src/auth.ts", "status": "conflicted"},
            {"file": "src/db.ts", "status": "conflicted"},
            {"file": "src/api.ts", "status": "conflicted"},
            {"file": "src/utils.ts", "status": "conflicted"},
        ]
        preview = {"auto_mergeable": False}

        suggestions = conflict_detector_service._generate_suggestions(
            conflicts, preview
        )

        assert len(suggestions) > 0
        assert any("multiple" in s.lower() for s in suggestions)


class TestAssessSeverity:
    """Test conflict severity assessment."""

    def test_assess_severity_no_conflicts(
        self,
        conflict_detector_service: ConflictDetectorService,
    ):
        """Test severity with no conflicts."""
        severity = conflict_detector_service._assess_severity([])

        assert severity == "low"

    def test_assess_severity_json_conflicts(
        self,
        conflict_detector_service: ConflictDetectorService,
    ):
        """Test severity with JSON conflicts."""
        conflicts = [
            {"file": "package.json", "status": "conflicted"},
            {"file": "tsconfig.json", "status": "conflicted"},
        ]

        severity = conflict_detector_service._assess_severity(conflicts)

        assert severity == "low"

    def test_assess_severity_code_conflicts(
        self,
        conflict_detector_service: ConflictDetectorService,
    ):
        """Test severity with code conflicts."""
        conflicts = [
            {"file": "src/auth.ts", "status": "conflicted"},
            {"file": "src/db.ts", "status": "conflicted"},
            {"file": "src/api.ts", "status": "conflicted"},
        ]

        severity = conflict_detector_service._assess_severity(conflicts)

        assert severity == "high"


class TestCreateConflictReport:
    """Test conflict report creation."""

    @pytest.mark.asyncio
    async def test_create_conflict_report_success(
        self,
        conflict_detector_service: ConflictDetectorService,
        worktree: Worktree,
        ticket: Ticket,
    ):
        """Test successful conflict report creation."""
        conflict_preview = {
            "has_conflicts": True,
            "conflicting_files": ["src/auth.ts", "src/db.ts"],
            "summary": "Merge conflicts in 2 files",
        }

        report_id = await conflict_detector_service.create_conflict_report(
            worktree_id=worktree.id,
            ticket_id=ticket.id,
            conflict_preview=conflict_preview,
        )

        assert report_id is not None

        # Verify report created
        report = conflict_detector_service.get_conflict_report(report_id)
        assert report is not None
        assert report.ticket_id == ticket.id
        assert report.conflict_files == ["src/auth.ts", "src/db.ts"]


class TestQueryMethods:
    """Test query methods."""

    def test_get_conflict_report(
        self,
        conflict_detector_service: ConflictDetectorService,
        worktree: Worktree,
        ticket: Ticket,
        session: Session,
    ):
        """Test fetching conflict report."""
        from loregarden.models.domain import ConflictReport

        report = ConflictReport(
            id=str(uuid4()),
            worktree_id=worktree.id,
            ticket_id=ticket.id,
            merge_attempt_number=1,
            conflict_type="merge_conflict",
            conflicting_files=["src/auth.ts"],
            conflict_details="Conflict in src/auth.ts",
        )
        session.add(report)
        session.commit()

        fetched = conflict_detector_service.get_conflict_report(report.id)

        assert fetched is not None
        assert fetched.id == report.id

    def test_get_worktree_conflicts(
        self,
        conflict_detector_service: ConflictDetectorService,
        worktree: Worktree,
        ticket: Ticket,
        session: Session,
    ):
        """Test fetching all conflicts for a worktree."""
        from loregarden.models.domain import ConflictReport

        report1 = ConflictReport(
            id=str(uuid4()),
            worktree_id=worktree.id,
            ticket_id=ticket.id,
            merge_attempt_number=1,
            conflict_type="merge_conflict",
            conflicting_files=["src/auth.ts"],
            conflict_details="Conflict 1",
        )
        report2 = ConflictReport(
            id=str(uuid4()),
            worktree_id=worktree.id,
            ticket_id=ticket.id,
            merge_attempt_number=2,
            conflict_type="merge_conflict",
            conflicting_files=["src/db.ts"],
            conflict_details="Conflict 2",
        )
        session.add_all([report1, report2])
        session.commit()

        conflicts = conflict_detector_service.get_worktree_conflicts(worktree.id)

        assert len(conflicts) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
