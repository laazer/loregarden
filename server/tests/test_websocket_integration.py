"""Integration tests for WebSocket event flow from backend to frontend."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from loregarden.websocket_events import WebSocketServer
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool


@pytest.fixture
def db_session():
    """Create in-memory SQLite database for testing."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def mock_ws_server():
    """Create mock WebSocket server."""
    ws = Mock(spec=WebSocketServer)
    ws.broadcast_execution_update = AsyncMock()
    ws.broadcast_conflict_detected = AsyncMock()
    ws.broadcast_conflict_resolved = AsyncMock()
    ws.broadcast_error = AsyncMock()
    ws.broadcast_queue_promoted = AsyncMock()
    ws.broadcast_run_completed = AsyncMock()
    return ws


@pytest.mark.asyncio
class TestWebSocketEventFlow:
    """Test complete flow of WebSocket events."""

    async def test_execution_update_event_contains_correct_data(self, db_session, mock_ws_server):
        """Verify execution_update event has all required fields."""
        with patch('loregarden.websocket_events.get_ws_server', return_value=mock_ws_server):
            from loregarden.websocket_events import emit_execution_update

            active_runs = [
                {
                    'run_id': 'run-1',
                    'slot_number': 1,
                    'ticket_id': 'ticket-1',
                    'elapsed_seconds': 60,
                    'status': 'running',
                }
            ]
            queued_runs = [
                {
                    'run_id': 'run-2',
                    'ticket_id': 'ticket-2',
                    'position': 1,
                    'wait_seconds': 0,
                }
            ]
            stats = {
                'max_concurrent': 3,
                'active_count': 1,
                'available_slots': 2,
                'queued_count': 1,
            }

            emit_execution_update(
                workspace_id='ws-1',
                active_runs=active_runs,
                queued_runs=queued_runs,
                stats=stats,
            )

            mock_ws_server.broadcast_execution_update.assert_called_once()
            call_args = mock_ws_server.broadcast_execution_update.call_args
            data = call_args[1]['data']
            assert data['activeRuns'] == active_runs
            assert data['queuedRuns'] == queued_runs
            assert data['stats'] == stats

    async def test_conflict_detected_event_contains_conflict_data(self, db_session, mock_ws_server):
        """Verify conflict_detected event has all required fields."""
        with patch('loregarden.websocket_events.get_ws_server', return_value=mock_ws_server):
            from loregarden.websocket_events import emit_conflict_detected

            conflicts = [
                {
                    'file': 'src/main.py',
                    'status': 'conflicted',
                    'ours_lines': 2,
                    'theirs_lines': 1,
                }
            ]
            preview = {
                'conflicting_files': ['src/main.py'],
                'summary': 'Merge conflicts in 1 file',
                'auto_mergeable': False,
            }

            emit_conflict_detected(
                worktree_id='wt-1',
                run_id='run-1',
                conflicts=conflicts,
                preview=preview,
                severity='medium',
            )

            mock_ws_server.broadcast_conflict_detected.assert_called_once()
            call_args = mock_ws_server.broadcast_conflict_detected.call_args
            data = call_args[1]['data']
            assert data['conflicts'] == conflicts
            assert data['severity'] == 'medium'
            assert data['preview'] == preview

    async def test_queue_promoted_event_has_run_details(self, db_session, mock_ws_server):
        """Verify queue_promoted event has run and slot information."""
        with patch('loregarden.websocket_events.get_ws_server', return_value=mock_ws_server):
            from loregarden.websocket_events import emit_queue_promoted

            emit_queue_promoted(
                workspace_id='ws-1',
                run_id='run-2',
                slot_number=2,
            )

            mock_ws_server.broadcast_queue_promoted.assert_called_once()
            call_args = mock_ws_server.broadcast_queue_promoted.call_args
            assert call_args[1]['workspaceId'] == 'ws-1'
            assert call_args[1]['data']['runId'] == 'run-2'
            assert call_args[1]['data']['slotNumber'] == 2

    async def test_run_completed_event_includes_status(self, db_session, mock_ws_server):
        """Verify run_completed event includes final status."""
        with patch('loregarden.websocket_events.get_ws_server', return_value=mock_ws_server):
            from loregarden.websocket_events import emit_run_completed

            emit_run_completed(
                workspace_id='ws-1',
                run_id='run-1',
                status='success',
            )

            mock_ws_server.broadcast_run_completed.assert_called_once()
            call_args = mock_ws_server.broadcast_run_completed.call_args
            assert call_args[1]['data']['runId'] == 'run-1'
            assert call_args[1]['data']['status'] == 'success'

    async def test_error_event_includes_context(self, db_session, mock_ws_server):
        """Verify error event includes error code and context."""
        with patch('loregarden.websocket_events.get_ws_server', return_value=mock_ws_server):
            from loregarden.websocket_events import emit_error

            emit_error(
                target_room='workspace:ws-1',
                message='Failed to create run',
                code='RUN_CREATION_ERROR',
                context={'ticket_id': 'ticket-1'},
            )

            mock_ws_server.broadcast_error.assert_called_once()
            call_args = mock_ws_server.broadcast_error.call_args
            assert call_args[1]['data']['code'] == 'RUN_CREATION_ERROR'
            assert call_args[1]['data']['message'] == 'Failed to create run'
            assert call_args[1]['data']['context']['ticket_id'] == 'ticket-1'

    async def test_multiple_events_emitted_for_run_completion(self, db_session, mock_ws_server):
        """Verify multiple events emitted in sequence for run completion."""
        with patch('loregarden.websocket_events.get_ws_server', return_value=mock_ws_server):
            from loregarden.websocket_events import (
                emit_execution_update,
                emit_queue_promoted,
                emit_run_completed,
            )

            # Simulate run completion sequence
            emit_run_completed(workspace_id='ws-1', run_id='run-1', status='success')
            emit_queue_promoted(workspace_id='ws-1', run_id='run-2', slot_number=1)
            emit_execution_update(
                workspace_id='ws-1',
                active_runs=[{'run_id': 'run-2', 'slot_number': 1}],
                queued_runs=[],
                stats={'active_count': 1, 'queued_count': 0},
            )

            # Verify all events were emitted
            mock_ws_server.broadcast_run_completed.assert_called_once()
            mock_ws_server.broadcast_queue_promoted.assert_called_once()
            mock_ws_server.broadcast_execution_update.assert_called_once()

    async def test_event_emission_safe_when_ws_unavailable(self, db_session):
        """Verify event emission handles missing WebSocket gracefully."""
        with patch('loregarden.websocket_events.get_ws_server', return_value=None):
            from loregarden.websocket_events import emit_execution_update

            # Should not raise even when WebSocket is None
            emit_execution_update(
                workspace_id='ws-1',
                active_runs=[],
                queued_runs=[],
                stats={},
            )

            # No exception raised = success

    async def test_event_routing_to_correct_room(self, db_session, mock_ws_server):
        """Verify events are routed to correct WebSocket rooms."""
        with patch('loregarden.websocket_events.get_ws_server', return_value=mock_ws_server):
            from loregarden.websocket_events import (
                emit_conflict_detected,
                emit_execution_update,
            )

            # Workspace event should target workspace room
            emit_execution_update(
                workspace_id='ws-1',
                active_runs=[],
                queued_runs=[],
                stats={},
            )

            call_args = mock_ws_server.broadcast_execution_update.call_args
            assert call_args[1]['workspaceId'] == 'ws-1'

            # Worktree event should target worktree room
            emit_conflict_detected(
                worktree_id='wt-1',
                run_id='run-1',
                conflicts=[],
                preview={},
                severity='low',
            )

            call_args = mock_ws_server.broadcast_conflict_detected.call_args
            assert call_args[1]['worktreeId'] == 'wt-1'

    async def test_event_includes_timestamp(self, db_session, mock_ws_server):
        """Verify events include timestamp."""
        with patch('loregarden.websocket_events.get_ws_server', return_value=mock_ws_server):
            from loregarden.websocket_events import emit_run_completed

            emit_run_completed(
                workspace_id='ws-1',
                run_id='run-1',
                status='success',
            )

            call_args = mock_ws_server.broadcast_run_completed.call_args
            data = call_args[1]['data']
            assert 'timestamp' in data
            # Timestamp should be ISO format
            assert 'T' in data['timestamp']
