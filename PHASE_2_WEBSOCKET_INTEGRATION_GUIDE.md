# Phase 2.1 WebSocket Integration Guide

## Overview

This guide shows how to add WebSocket event emissions to existing backend services. The events are emitted at key points in the execution lifecycle.

## Setup

### 1. Initialize WebSocket in your Flask app

```python
# server/loregarden/main.py
from flask import Flask
from flask_socketio import SocketIO
from loregarden.websocket import WebSocketServer
from loregarden.websocket_events import init_websocket

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# Initialize WebSocket server
ws_server = WebSocketServer(socketio)
ws_server.initialize_handlers()
init_websocket(ws_server)  # Make it available to services

# Register blueprints
app.register_blueprint(router)

if __name__ == '__main__':
    socketio.run(app, debug=True)
```

### 2. Add imports to services

Each service that emits events needs to import the helper functions:

```python
from loregarden.websocket_events import (
    emit_execution_update,
    emit_conflict_detected,
    emit_conflict_resolved,
    emit_queue_promoted,
    emit_run_completed,
    emit_error,
)
```

## Integration Points

### Task 2.1-B2: API Endpoint Updates (api/parallel.py)

#### Location 1: `create_parallel_run` endpoint

When a run is created, emit execution update:

```python
# BEFORE: endpoint returns result
return result

# AFTER: add event emission
try:
    # Get updated status for broadcast
    queue_service = ParallelQueueService(session, max_concurrent=max_concurrent)
    active_runs = await queue_service.get_active_runs(ticket.workspace_id)
    queued_runs = await queue_service.get_queued_runs(ticket.workspace_id)
    stats = await queue_service.get_stats(ticket.workspace_id)
    
    # Emit to workspace subscribers
    emit_execution_update(
        workspace_id=ticket.workspace_id,
        active_runs=active_runs,
        queued_runs=queued_runs,
        stats=stats,
    )
except Exception as e:
    logger.warning(f'Failed to emit execution_update: {e}')

return result
```

#### Location 2: `get_parallel_status` endpoint

This endpoint is called by polling clients. After returning status, emit it:

```python
# Return status to client
return {
    "active_runs": active_runs,
    "queued_runs": queued_runs,
    "stats": stats,
    ...
}
# Note: WebSocket clients don't call this endpoint, they get events instead
```

#### Location 3: Run completion endpoint (if exists)

When a run completes:

```python
@router.post("/runs/{run_id}/complete")
async def complete_run(run_id: str, status: str, session: Session):
    # Mark run as complete in DB
    run = session.get(AgentRun, run_id)
    run.status = status
    session.add(run)
    session.commit()
    
    # Emit completion event
    emit_run_completed(
        workspace_id=run.workspace_id,
        run_id=run_id,
        status=status,
    )
    
    # Emit updated status
    queue_service = ParallelQueueService(session)
    active_runs = await queue_service.get_active_runs(run.workspace_id)
    queued_runs = await queue_service.get_queued_runs(run.workspace_id)
    stats = await queue_service.get_stats(run.workspace_id)
    
    emit_execution_update(
        workspace_id=run.workspace_id,
        active_runs=active_runs,
        queued_runs=queued_runs,
        stats=stats,
    )
    
    return {"status": "completed"}
```

### Task 2.1-B3: Conflict Detector Updates (services/conflict_detector.py)

#### Location 1: `detect_conflicts` method

When conflicts are detected:

```python
def detect_conflicts(self, worktree_id: str, run_id: str) -> ConflictPreview:
    """Detect conflicts in merge."""
    
    # ... existing detection logic ...
    
    # After detecting conflicts, emit event
    if conflicts:
        emit_conflict_detected(
            worktree_id=worktree_id,
            run_id=run_id,
            conflicts=self._format_conflicts(conflicts),
            preview=preview,
            severity=preview['severity'],
        )
    
    return preview
```

#### Location 2: `resolve_conflicts` method (if exists)

When conflicts are resolved:

```python
def resolve_conflicts(self, worktree_id: str, run_id: str, strategy: str = 'auto'):
    """Resolve detected conflicts."""
    
    # ... existing resolution logic ...
    
    # After successful resolution, emit cleared event
    emit_conflict_resolved(
        worktree_id=worktree_id,
        run_id=run_id,
    )
    
    return result
```

#### Location 3: Error handling

On conflict detection failure:

```python
try:
    conflicts = self._detect_merge_conflicts(...)
except Exception as e:
    emit_error(
        target_room=f'worktree:{worktree_id}',
        message=f'Failed to detect conflicts: {str(e)}',
        code='CONFLICT_DETECTION_ERROR',
        context={'worktree_id': worktree_id, 'run_id': run_id}
    )
    raise
```

### Task 2.1-B4: Queue Service Updates (services/parallel_queue.py)

#### Location 1: `queue_run` method

When a run is queued:

```python
async def queue_run(self, run: AgentRun) -> QueuedRun:
    """Queue a run if no slots available."""
    
    # ... existing queueing logic ...
    
    # After run is queued, emit status update
    active_runs = await self.get_active_runs(run.workspace_id)
    queued_runs = await self.get_queued_runs(run.workspace_id)
    stats = await self.get_stats(run.workspace_id)
    
    emit_execution_update(
        workspace_id=run.workspace_id,
        active_runs=active_runs,
        queued_runs=queued_runs,
        stats=stats,
    )
    
    return queued_run
```

#### Location 2: `promote_from_queue` method

When a run is promoted from queue to active:

```python
async def promote_from_queue(self, workspace_id: str) -> Optional[AgentRun]:
    """Promote next queued run to available slot."""
    
    # ... existing promotion logic ...
    
    # After successful promotion
    if promoted_run:
        # Emit promotion event
        emit_queue_promoted(
            workspace_id=workspace_id,
            run_id=promoted_run.run_id,
            slot_number=promoted_run.slot_number,
        )
        
        # Emit updated status
        active_runs = await self.get_active_runs(workspace_id)
        queued_runs = await self.get_queued_runs(workspace_id)
        stats = await self.get_stats(workspace_id)
        
        emit_execution_update(
            workspace_id=workspace_id,
            active_runs=active_runs,
            queued_runs=queued_runs,
            stats=stats,
        )
    
    return promoted_run
```

#### Location 3: `on_run_complete` method

When a run completes:

```python
async def on_run_complete(self, run: AgentRun) -> None:
    """Handle run completion and trigger queue promotion."""
    
    # ... existing completion logic ...
    
    # Emit run completion event
    emit_run_completed(
        workspace_id=run.workspace_id,
        run_id=run.run_id,
        status=run.status,
    )
    
    # Promote from queue if waiting
    await self.promote_from_queue(run.workspace_id)
    
    # Emit updated status
    active_runs = await self.get_active_runs(run.workspace_id)
    queued_runs = await self.get_queued_runs(run.workspace_id)
    stats = await self.get_stats(run.workspace_id)
    
    emit_execution_update(
        workspace_id=run.workspace_id,
        active_runs=active_runs,
        queued_runs=queued_runs,
        stats=stats,
    )
```

## Event Flow Examples

### Example 1: Create and Execute a Run

```
1. Client: POST /api/parallel/runs/ticket-123
2. Server: create_parallel_run()
3. Server: Run starts (or queues if no slots)
4. Server: emit_execution_update() to workspace:ws-1
5. Client: WebSocket receives execution_update event
6. Client: Updates UI with new run
7. Client: All subscribed clients see update in <100ms
```

### Example 2: Queue Promotion

```
1. Server: Run completes
2. Server: on_run_complete() called
3. Server: emit_run_completed() to workspace:ws-1
4. Server: promote_from_queue() moves run from queue
5. Server: emit_queue_promoted() to workspace:ws-1
6. Server: emit_execution_update() with new state
7. Client: Receives 3 events in sequence
8. Client: Updates UI progressively
```

### Example 3: Conflict Detection

```
1. Server: Merge dry-run encounters conflicts
2. Server: detect_conflicts() analyzes them
3. Server: emit_conflict_detected() to worktree:wt-1
4. Client: WebSocket receives conflict event
5. Client: Shows conflict warning component
6. User: Can resolve or abort
7. Server: emit_conflict_resolved() when done
8. Client: Conflict warning clears
```

## Handling Missing WebSocket

All event emissions are wrapped in try/except blocks and check if WebSocket is initialized:

```python
def emit_execution_update(...):
    ws = get_ws_server()
    if not ws:
        return  # WebSocket not initialized, skip
    
    try:
        ws.broadcast_execution_update(...)
    except Exception as e:
        logger.warning(f'Failed to emit: {e}')
        # Continue without WebSocket (polling still works)
```

This means:
- ✅ If WebSocket is not initialized, system continues to work
- ✅ Polling clients still get updates via REST endpoints
- ✅ WebSocket clients get real-time events when available
- ✅ No breaking changes to existing functionality

## Testing the Integration

### Unit Tests

Test that events are emitted at the right time:

```python
def test_emit_execution_update_on_queue(mocker):
    # Mock the emit function
    mock_emit = mocker.patch('services.websocket_events.emit_execution_update')
    
    # Queue a run
    run = queue_service.queue_run(agent_run)
    
    # Assert event was emitted
    mock_emit.assert_called_once()
    call_args = mock_emit.call_args
    assert call_args[1]['workspace_id'] == 'ws-1'
```

### Integration Tests

Test the full flow with WebSocket client:

```python
def test_execution_update_received_on_client():
    # Setup WebSocket client
    client = socketio_client.Client()
    client.connect(server_url)
    client.emit('join_workspace', {'workspaceId': 'ws-1'})
    
    # Create a run
    requests.post(f'{api_url}/runs/ticket-123')
    
    # Verify event received
    events = client.get_received()
    assert any(e['args'][0] == 'execution_update' for e in events)
```

## Deployment Notes

1. **Gradual Rollout**: Deploy WebSocket alongside REST API
2. **Monitoring**: Track event emission latency and errors
3. **Fallback**: Keep polling available for backward compatibility
4. **Scaling**: WebSocket rooms scale with user count, not request rate
5. **Testing**: Run load tests with multiple concurrent clients

## Rollback Plan

If WebSocket issues arise:
1. Clients automatically fallback to polling after timeout
2. All functionality continues via REST API
3. No data loss or system failure
4. Simply disable SocketIO in Flask app to remove WebSocket

## Migration Path

1. Deploy Phase 2.1 infrastructure (done ✓)
2. Add event emissions to services (this guide)
3. Monitor metrics and fix issues
4. Gradually migrate clients to use WS hooks
5. Keep polling as fallback indefinitely
6. Optional: sunset polling in 6+ months if stable
