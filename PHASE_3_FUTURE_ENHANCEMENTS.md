# Phase 3.3a: Future Enhancements

## Overview

Future enhancements to the Enhanced Queue Visualization system, including notifications, advanced controls, and historical analytics. These components provide solo developers with better visibility, control, and insights into parallel execution.

## Components

### 1. Queue Notifications (`QueueNotifications.tsx`)

Real-time toast-style notifications for queue events.

**Features:**
- Toast notifications for run completion, promotion, failure
- Server-Sent Events (SSE) integration for event streaming
- Auto-dismiss with configurable duration
- Multiple notification types: success, error, info, warning
- Dismiss button for persistent notifications
- Action buttons for quick interactions

**Events Monitored:**
```
run_completed: {run_id, ticket_id}
run_promoted: {run_id, ticket_id, slot_number}
run_failed: {run_id, ticket_id, error}
reorder_failed: {run_id, message}
```

**Implementation:**
```typescript
<QueueNotifications workspaceId="ws-1" />
```

**Styling:**
- Fixed position (bottom-right)
- Slide-in/slide-out animations
- Dark mode support
- Responsive design (mobile-friendly)
- Color-coded by notification type

**Performance:**
- <50ms notification display
- Zero impact on main thread (async event handling)
- ~2KB per notification in memory

### 2. Advanced Queue Controls (`QueueAdvancedControls.tsx`)

Manual controls for queue operations and run management.

**Features:**
- Per-run action buttons (pause, resume, cancel, promote)
- Bulk actions on selected runs
- Expandable control panels
- Run selection with checkboxes
- Error handling and feedback
- Separate sections for active and queued runs

**Supported Actions:**
```
Active Runs:
  - pause:   Pause execution (returns slot for next run)
  - cancel:  Cancel and remove from execution
  - resume:  Resume paused run

Queued Runs:
  - promote: Manually promote to available slot
  - cancel:  Cancel and remove from queue

Bulk:
  - cancel:  Cancel all selected runs
  - clear:   Deselect all
```

**Implementation:**
```typescript
<QueueAdvancedControls
  workspaceId="ws-1"
  activeRuns={activeRuns}
  queuedRuns={queuedRuns}
  onRunControl={async (action, runId) => {
    // Custom handler if needed
  }}
/>
```

**API Endpoints Required:**
```
POST /api/parallel/queue/{run_id}/pause
POST /api/parallel/queue/{run_id}/resume
POST /api/parallel/queue/{run_id}/cancel
POST /api/parallel/queue/{run_id}/promote
```

**Styling:**
- Card-based layout for each run
- Color-coded by run status
- Disabled states for unavailable actions
- Hover animations
- Responsive layout

### 3. Historical Analytics (`QueueHistoricalAnalytics.tsx`)

Performance tracking and analytics dashboard.

**Features:**
- Per-ticket-type performance metrics
- Time range selector (7d, 30d, 90d)
- Success rate tracking with color coding
- Duration statistics (min, max, average)
- Trend visualization via success bars
- Summary statistics

**Metrics Tracked:**
```typescript
interface RunMetrics {
  ticket_type: string;
  count: number;
  avg_duration_seconds: number;
  min_duration_seconds: number;
  max_duration_seconds: number;
  success_rate: number;
  last_7_days_count: number;
  last_7_days_success_rate: number;
}
```

**Implementation:**
```typescript
<QueueHistoricalAnalytics workspaceId="ws-1" />
```

**API Endpoint Required:**
```
GET /api/parallel/workspace/{workspace_id}/analytics?range=7d|30d|90d

Response:
{
  "metrics": [
    {
      "ticket_type": "feature-branch",
      "count": 42,
      "avg_duration_seconds": 245,
      "min_duration_seconds": 120,
      "max_duration_seconds": 420,
      "success_rate": 0.95,
      "last_7_days_count": 8,
      "last_7_days_success_rate": 0.98
    }
  ]
}
```

**Insights:**
- Fast execution (<2 min) indicators
- High reliability (>95% success rate) badges
- Success rate warnings (<85%)
- Visual success bars with gradient fills

**Styling:**
- Grid layout (auto-fill columns)
- Color gradients for success indicators
- Responsive design

## Integration Guide

### Adding to Dashboard

```typescript
import { ParallelQueueVisualization } from './ParallelQueueVisualization';
import { QueueNotifications } from './QueueNotifications';
import { QueueAdvancedControls } from './QueueAdvancedControls';
import { QueueHistoricalAnalytics } from './QueueHistoricalAnalytics';

export function QueueDashboard({ workspaceId }: { workspaceId: string }) {
  const { activeRuns, queuedRuns } = useParallelExecutionWS(workspaceId);

  return (
    <div className="queue-dashboard">
      <QueueNotifications workspaceId={workspaceId} />
      
      <ParallelQueueVisualization workspaceId={workspaceId} />
      
      <QueueAdvancedControls
        workspaceId={workspaceId}
        activeRuns={activeRuns || []}
        queuedRuns={queuedRuns || []}
      />
      
      <QueueHistoricalAnalytics workspaceId={workspaceId} />
    </div>
  );
}
```

## Backend Requirements

### Server-Sent Events for Notifications

```python
# server/loregarden/api/notifications.py
@router.get("/workspace/{workspace_id}/notifications")
async def get_notifications(workspace_id: str):
    """Server-Sent Events endpoint for queue notifications."""
    async def event_generator():
        # Subscribe to workspace events
        await ws_emit.subscribe(f'workspace:{workspace_id}')
        
        while True:
            event = await ws_emit.get_event(f'workspace:{workspace_id}')
            if event:
                yield f"event: {event['type']}\n"
                yield f"data: {json.dumps(event['data'])}\n\n"
            await asyncio.sleep(0.1)
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

### Analytics Endpoint

```python
# server/loregarden/api/analytics.py
@router.get("/workspace/{workspace_id}/analytics")
async def get_analytics(workspace_id: str, range: str = "7d", session: Session = get_session()):
    """Get historical run performance metrics."""
    # Query run history from database
    # Calculate metrics per ticket_type
    # Return aggregated performance data
```

## Testing Strategy

### Component Tests

```typescript
describe('QueueNotifications', () => {
  test('displays toast on run_completed event');
  test('auto-dismisses after timeout');
  test('persists if duration=0');
  test('SSE connection failure falls back gracefully');
  test('multiple notifications stack vertically');
});

describe('QueueAdvancedControls', () => {
  test('displays active and queued runs separately');
  test('shows action buttons on expand');
  test('disables actions while processing');
  test('bulk select and cancel');
  test('error message displayed on failure');
});

describe('QueueHistoricalAnalytics', () => {
  test('fetches and displays metrics');
  test('time range selector changes data');
  test('success rate color coding');
  test('handles empty history gracefully');
  test('summary statistics calculated correctly');
});
```

### Integration Tests

```typescript
describe('Queue Dashboard Integration', () => {
  test('all components render together');
  test('advanced controls update after reorder');
  test('notifications appear during queue operations');
  test('analytics reflect recent runs');
  test('responsive layout at different breakpoints');
});
```

## Performance Considerations

### Memory
- **Notifications**: ~1KB per toast (cleared after dismiss)
- **Advanced Controls**: ~5KB for full run list
- **Analytics**: ~10KB for 30-day metrics

### Network
- **Notifications**: SSE stream (event-driven, no polling)
- **Analytics**: Single API call on mount + time range change
- **Total impact**: <1KB/min steady state

### Rendering
- **Notifications**: O(N) where N = active notifications (<10 typical)
- **Advanced Controls**: O(M) where M = active+queued runs
- **Analytics**: O(T) where T = tracked ticket types (<20 typical)

## Accessibility

### Keyboard Navigation
- Tab through all controls
- Space/Enter to toggle selections
- Arrow keys to navigate (future enhancement)
- Esc to close expanded controls

### Screen Readers
- ARIA labels on all buttons
- aria-live regions for notifications
- Semantic HTML structure
- Form labels for inputs

### Visual
- Color coding with text fallbacks
- Sufficient contrast ratios
- Large enough touch targets (44px minimum)

## File Structure

```
client/src/components/
├── QueueNotifications.tsx (150 LOC)
├── QueueNotifications.css (200 LOC)
├── QueueAdvancedControls.tsx (220 LOC)
├── QueueAdvancedControls.css (350 LOC)
├── QueueHistoricalAnalytics.tsx (210 LOC)
├── QueueHistoricalAnalytics.css (320 LOC)
└── __tests__/
    ├── QueueNotifications.test.tsx (180 LOC)
    ├── QueueAdvancedControls.test.tsx (220 LOC)
    └── QueueHistoricalAnalytics.test.tsx (200 LOC)

server/loregarden/api/
├── notifications.py (NEW - 100 LOC)
└── analytics.py (NEW - 150 LOC)

server/tests/
├── test_notifications.py (NEW - 120 LOC)
└── test_analytics.py (NEW - 180 LOC)
```

## Rollout Plan

### Phase 1: Notifications (Week 1)
1. Implement QueueNotifications component
2. Add SSE endpoint
3. Wire into existing queue events
4. Deploy and monitor

### Phase 2: Advanced Controls (Week 2)
1. Implement QueueAdvancedControls component
2. Add backend control endpoints
3. Add bulk operation support
4. Deploy and gather feedback

### Phase 3: Analytics (Week 3)
1. Implement data collection (already in place via event system)
2. Add QueueHistoricalAnalytics component
3. Implement analytics API endpoint
4. Deploy and measure usage

## Future Roadmap

### Phase 3.3b: Advanced Timeline View
- Gantt-style horizontal timeline
- Estimated completion times
- Visual queue clear estimator

### Phase 3.3c: Integrations
- Slack notifications on run completion
- Webhook support for external systems
- Performance alerts

### Phase 3.3d: ML-Based Insights
- Anomaly detection for failing runs
- Duration predictions
- Smart auto-prioritization recommendations

## Success Metrics

- **Notification engagement**: >80% notification dismissal rate
- **Control usage**: >40% users attempt manual controls
- **Analytics adoption**: >50% users review performance trends
- **Performance**: No degradation in WebSocket update latency
- **Accessibility**: 100% WCAG 2.1 AA compliance

---

**Status**: 🎯 Ready for Implementation

**Next Steps**:
1. Review components and styling
2. Implement backend endpoints
3. Add comprehensive tests
4. Deploy to staging
5. Gather user feedback
