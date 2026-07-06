# Phase 2.1: WebSocket Integration Plan

## Overview

Replace 5-second polling with real-time WebSocket updates for:
- Parallel execution status (active/queued runs)
- Conflict detection
- Queue management
- Run completion/promotion

## Architecture

### Server (Backend)

**Technology**: Flask-SocketIO (async WebSocket library)

**Events Flow**:
```
Client connects -> join workspace/worktree room
Server detects state change -> emit event to room subscribers
Client receives event -> update React state immediately
Client action (cancel/resolve) -> emit action event to server
Server processes -> broadcasts result to room
```

**Rooms/Namespaces**:
- `/parallel`: Main namespace
  - Room: `workspace:{workspaceId}` - execution status updates
  - Room: `worktree:{worktreeId}` - conflict updates

**Server Events** (Server → Client):
- `execution_update`: Active/queued runs changed
- `conflict_detected`: Merge conflict found
- `conflict_resolved`: Conflict cleared
- `queue_promoted`: Run moved from queue to active
- `run_completed`: Run finished
- `error`: Server-side error

**Client Events** (Client → Server):
- `join_workspace`: Subscribe to execution updates
- `join_worktree`: Subscribe to conflict updates
- `cancel_run`: Cancel queued/active run
- `resolve_conflicts`: Mark conflicts as resolved
- `leave_workspace`: Unsubscribe

### Client (Frontend)

**Hooks** (New):
- `useParallelExecutionWS()`: WebSocket version of useParallelExecution
- `useWorktreeConflictsWS()`: WebSocket version of useWorktreeConflicts

**Features**:
- Auto-reconnection with exponential backoff (1s, 2s, 4s, 8s, max 30s)
- Message deduplication (ignore duplicate updates)
- Optimistic UI updates for actions
- Fallback to polling if WebSocket unavailable
- Connection state display
- Graceful degradation

**Connection States**:
```
DISCONNECTED -> CONNECTING -> CONNECTED -> SUBSCRIBED
                    ↓
                  ERROR -> RECONNECTING
```

## Implementation Tasks

### Backend (Phase 2.1-Backend)

**Task 2.1-B1: WebSocket Server Setup**
- `server/loregarden/websocket.py` (NEW - 300 LOC)
  - SocketIO configuration
  - Namespace and room management
  - Event handlers for join/leave
  - Error handling and logging

**Task 2.1-B2: Execution Status Events**
- Update `server/loregarden/api/parallel.py`
  - Hook into run state changes
  - Emit `execution_update` events
  - Broadcast to `workspace:{workspaceId}` room

**Task 2.1-B3: Conflict Detection Events**
- Update `server/loregarden/services/conflict_detector.py`
  - Emit `conflict_detected` on new conflict
  - Emit `conflict_resolved` on clear
  - Broadcast to `worktree:{worktreeId}` room

**Task 2.1-B4: Queue Promotion Events**
- Update `server/loregarden/services/parallel_queue.py`
  - Emit `queue_promoted` when run moves to slot
  - Emit `run_completed` when run finishes
  - Broadcast to `workspace:{workspaceId}` room

### Frontend (Phase 2.1-Frontend)

**Task 2.1-F1: WebSocket Client Setup**
- `client/src/services/websocket.ts` (NEW - 250 LOC)
  - Socket.IO client initialization
  - Connection management
  - Event emitter pattern
  - Auto-reconnection logic

**Task 2.1-F2: Execution WebSocket Hook**
- `client/src/hooks/useParallelExecutionWS.ts` (NEW - 200 LOC)
  - Subscribes to `execution_update` events
  - Maintains same interface as useParallelExecution
  - Fallback to polling on error
  - Message deduplication

**Task 2.1-F3: Conflict WebSocket Hook**
- `client/src/hooks/useWorktreeConflictsWS.ts` (NEW - 180 LOC)
  - Subscribes to `conflict_*` events
  - Same interface as useWorktreeConflicts
  - Fallback to polling on error

**Task 2.1-F4: Component Updates (Optional)**
- Update components to use WS hooks (backward compatible)
- Add connection status indicator
- Add manual refresh button

**Task 2.1-F5: Hook Tests**
- `client/src/hooks/__tests__/useParallelExecutionWS.test.ts` (NEW - 300 LOC)
- `client/src/hooks/__tests__/useWorktreeConflictsWS.test.ts` (NEW - 280 LOC)

**Task 2.1-F6: Integration Tests**
- `client/src/__tests__/WebSocketIntegration.test.tsx` (NEW - 350 LOC)

## API Specification

### Server → Client Events

#### `execution_update`
```json
{
  "type": "execution_update",
  "workspaceId": "ws-123",
  "timestamp": "2026-07-06T10:30:00Z",
  "data": {
    "activeRuns": [ActiveRun],
    "queuedRuns": [QueuedRun],
    "stats": ParallelStats
  }
}
```

#### `conflict_detected`
```json
{
  "type": "conflict_detected",
  "worktreeId": "wt-123",
  "runId": "run-123",
  "timestamp": "2026-07-06T10:30:00Z",
  "data": {
    "conflicts": [ConflictFile],
    "preview": ConflictPreview,
    "severity": "medium"
  }
}
```

#### `queue_promoted`
```json
{
  "type": "queue_promoted",
  "workspaceId": "ws-123",
  "runId": "run-123",
  "slotNumber": 2,
  "timestamp": "2026-07-06T10:30:00Z"
}
```

#### `run_completed`
```json
{
  "type": "run_completed",
  "workspaceId": "ws-123",
  "runId": "run-123",
  "status": "success|failed",
  "timestamp": "2026-07-06T10:30:00Z"
}
```

#### `error`
```json
{
  "type": "error",
  "message": "Failed to fetch conflicts",
  "code": "CONFLICT_FETCH_ERROR",
  "timestamp": "2026-07-06T10:30:00Z"
}
```

### Client → Server Events

#### `join_workspace`
```json
{
  "workspaceId": "ws-123",
  "userId": "user-123",
  "timestamp": "2026-07-06T10:30:00Z"
}
```

#### `join_worktree`
```json
{
  "worktreeId": "wt-123",
  "runId": "run-123",
  "timestamp": "2026-07-06T10:30:00Z"
}
```

#### `cancel_run`
```json
{
  "runId": "run-123",
  "reason": "user_request",
  "timestamp": "2026-07-06T10:30:00Z"
}
```

## Backward Compatibility

**Polling Fallback**:
- If WebSocket connection fails, automatically fall back to REST polling
- Hooks return same interface for both (seamless)
- User doesn't notice the switch
- Can configure fallback timeout (30s by default)

**REST API Remains**:
- All existing endpoints stay active
- Useful for mobile/offline scenarios
- Can coexist with WebSocket

## Performance Impact

### Before (Polling)
- **Requests**: 2 per 5 seconds = 24/minute per user
- **Latency**: 0-5 seconds (average 2.5s)
- **Server Load**: High (many concurrent polls)
- **Bandwidth**: Constant ~1-2KB per request

### After (WebSocket)
- **Requests**: 1 initial connection + event-driven updates
- **Latency**: <100ms (near real-time)
- **Server Load**: Low (single connection per user)
- **Bandwidth**: Only when state changes

### Savings (100 concurrent users)
- **Polling**: 2,400 requests/min = 2.4 million/day
- **WebSocket**: 100 connections + events = ~1% of requests
- **Bandwidth**: 95% reduction when idle
- **Server CPU**: 70% reduction (estimated)

## Rollout Strategy

1. **Phase 1**: Deploy WebSocket server alongside REST API
2. **Phase 2**: Gradually roll out WS hooks to frontend
3. **Phase 3**: Monitor metrics, keep polling as fallback
4. **Phase 4**: Optional: sunset polling after 2-3 months

## Testing Strategy

### Unit Tests
- WebSocket client behavior (connect, disconnect, reconnect)
- Event parsing and deduplication
- Fallback triggering
- Hook state management

### Integration Tests
- Client ↔ Server event flow
- Multiple rooms and subscriptions
- Reconnection scenarios
- Error handling

### E2E Tests
- Full dashboard with WebSocket
- Real-time updates visible
- Fallback to polling on error
- Performance under load

## File Summary

| Phase | File | LOC | Purpose |
|-------|------|-----|---------|
| 2.1-B1 | websocket.py | 300 | SocketIO server setup |
| 2.1-B2-4 | Updates to api/*.py, services/*.py | 200 | Event emissions |
| 2.1-F1 | websocket.ts | 250 | SocketIO client |
| 2.1-F2 | useParallelExecutionWS.ts | 200 | Execution hook |
| 2.1-F3 | useWorktreeConflictsWS.ts | 180 | Conflict hook |
| 2.1-F4 | Component updates | 50 | Connection indicator |
| 2.1-F5 | Hook tests | 580 | Test coverage |
| 2.1-F6 | Integration tests | 350 | E2E scenarios |
| **Total** | | **2,110** | **Phase 2.1** |

## Next Steps

1. Implement WebSocket server setup (2.1-B1)
2. Add event emissions to existing services (2.1-B2-4)
3. Create WebSocket client service (2.1-F1)
4. Implement WS hooks (2.1-F2-3)
5. Update components and add status indicator (2.1-F4)
6. Comprehensive testing (2.1-F5-6)
7. Performance testing and monitoring

## Success Criteria

✅ Real-time updates within 100ms of state change
✅ Graceful fallback to polling on error
✅ 95% reduction in polling requests
✅ Zero breaking changes to component API
✅ All tests passing (unit + integration + E2E)
✅ Connection status visible to user
✅ Manual reconnect button available
✅ Monitored metrics show improvement
