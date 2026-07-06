# Phase 1B: Parallel Agent Execution - Task Breakdown

**Timeline**: Weeks 3-6 (12-14 weeks)  
**Goal**: Enable 3-5 simultaneous features in isolated worktrees with automatic conflict detection

---

## Overview

Phase 1B enables **concurrent agent execution** across multiple features:

```
Before:  [Feature 1] → [Feature 2] → [Feature 3]  (20 days sequentially)
After:   [Feature 1]
         [Feature 2]  (All running in parallel, 6 days total)
         [Feature 3]
```

**Key Concepts:**
- **Worktree**: Isolated git working directory (git 2.15+)
- **Parallelism**: 3-5 simultaneous agents (configurable)
- **Conflict Detection**: Warns on file conflicts before merge
- **Queue**: Remaining features queue and auto-start when slots free

---

## Backend Implementation Tasks

### Task 2.1: Git Worktree Service (3 days)

**Deliverable**: Service for creating, managing, and cleaning up git worktrees

```python
# server/loregarden/services/worktree_service.py
class WorktreeService:
    """Manage isolated git worktrees for parallel features."""
    
    async def create_worktree(
        self,
        repo_path: str,
        feature_name: str,
        base_branch: str = "main",
    ) -> WorktreeState:
        """
        Create isolated worktree for a feature.
        
        Location: <repo_path>/.loregarden/worktrees/<feature_name>
        Base: <base_branch> (usually main or develop)
        """
        # 1. git worktree add <path> <base_branch>
        # 2. Create WorktreeState record
        # 3. Return path + metadata
        pass
    
    async def get_worktree_status(self, worktree_id: str) -> dict:
        """Get current worktree state (branch, files modified, conflicts)."""
        pass
    
    async def detect_conflicts(
        self,
        worktree_id: str,
        target_branch: str = "main",
    ) -> dict:
        """
        Detect file conflicts before merge.
        
        Returns: {
            "has_conflicts": bool,
            "conflicting_files": ["src/auth.ts", "src/api.ts"],
            "conflict_summary": "3 files have merge conflicts",
        }
        """
        pass
    
    async def merge_worktree(
        self,
        worktree_id: str,
        target_branch: str = "main",
    ) -> bool:
        """
        Merge worktree back to main.
        
        1. Detect conflicts (warn if any)
        2. Create merge commit
        3. Clean up worktree
        
        Returns: True if merge successful
        """
        pass
    
    async def cleanup_worktree(self, worktree_id: str) -> None:
        """Delete worktree and cleanup files."""
        pass
    
    async def list_worktrees(self, workspace_id: str) -> list[WorktreeState]:
        """List all active worktrees for workspace."""
        pass
```

**Database:**
```sql
CREATE TABLE worktree_states (
    id TEXT PRIMARY KEY,
    run_id TEXT FOREIGN KEY,
    workspace_id TEXT FOREIGN KEY,
    feature_name TEXT NOT NULL,
    worktree_path TEXT NOT NULL,
    base_branch TEXT DEFAULT 'main',
    status TEXT DEFAULT 'active',  -- active, merged, failed, cleaned
    git_branch TEXT,  -- Current branch in worktree
    files_modified INT,  -- Number of modified files
    created_at TIMESTAMP,
    merged_at TIMESTAMP,
    cleaned_at TIMESTAMP
);
```

**Dependencies**: None (git CLI)

**Acceptance Criteria:**
- [x] Worktree created at `.loregarden/worktrees/<feature>`
- [x] Conflicts detected before merge
- [x] Merge successful to main
- [x] Cleanup removes all files
- [x] Multiple concurrent worktrees work

---

### Task 2.2: Parallel Execution Queue (2 days)

**Deliverable**: Queue system for managing concurrent agent runs

```python
# server/loregarden/services/parallel_queue.py
class ParallelQueue:
    """Manage queue of features waiting for parallel execution slots."""
    
    def __init__(self, max_concurrent: int = 3):
        self.max_concurrent = max_concurrent  # 3-5 default
    
    async def queue_feature(self, ticket_id: str) -> QueuePosition:
        """
        Add feature to execution queue.
        
        Returns: {
            "position": 1,  # Current position in queue
            "will_start_in": "5 minutes (est)",
            "ticket_id": "...",
        }
        """
        pass
    
    async def get_active_runs(self, workspace_id: str) -> list[Run]:
        """Get currently executing runs."""
        pass
    
    async def get_queued_runs(self, workspace_id: str) -> list[Run]:
        """Get runs waiting in queue."""
        pass
    
    async def promote_from_queue(self) -> Optional[Run]:
        """
        Check if queue has items and slots available.
        If yes, promote next queued run to active.
        """
        pass
    
    async def on_run_complete(self, run_id: str) -> None:
        """
        Called when a run completes.
        Frees up a slot and promotes from queue.
        """
        pass
```

**Integration Points:**
- When run completes → `on_run_complete()` → auto-promote from queue
- When new run requested → check slots, queue or start
- Dashboard shows queue position

**Acceptance Criteria:**
- [x] Queue fills when all slots taken
- [x] Queue empties as slots free
- [x] Auto-promotion works
- [x] Position tracking accurate

---

### Task 2.3: Parallel Run Orchestration (4 days)

**Deliverable**: Enhanced orchestration for parallel execution

```python
# Modify server/loregarden/services/orchestration.py

async def create_parallel_run(
    self,
    ticket_id: str,
    workspace_id: str,
    pipeline: list[str],  # ["plan", "spec", "implement", ...]
) -> Union[Run, QueuePosition]:
    """
    Create a new run, potentially queuing if no slots available.
    
    Returns:
    - Run if created immediately
    - QueuePosition if queued
    """
    queue = ParallelQueue(max_concurrent=settings.LOREGARDEN_MAX_PARALLEL_AGENTS)
    
    active = await queue.get_active_runs(workspace_id)
    if len(active) < queue.max_concurrent:
        # Create worktree + run immediately
        return await self._create_run_in_worktree(ticket_id, workspace_id, pipeline)
    else:
        # Queue the feature
        return await queue.queue_feature(ticket_id)

async def _create_run_in_worktree(
    self,
    ticket_id: str,
    workspace_id: str,
    pipeline: list[str],
) -> Run:
    """Create a run in an isolated worktree."""
    # 1. Create worktree
    worktree = await worktree_service.create_worktree(...)
    
    # 2. Create run (linked to worktree)
    run = Run(
        ticket_id=ticket_id,
        workspace_id=workspace_id,
        worktree_id=worktree.id,
        stages=pipeline,
    )
    
    # 3. Start execution
    return await self.run_stage(run, run.stages[0])

async def on_run_complete(self, run_id: str) -> None:
    """Called when a run stage completes."""
    run = self.session.exec(select(Run).where(Run.id == run_id)).first()
    
    # Check if this was last stage
    if is_last_stage(run):
        # Merge worktree
        await worktree_service.merge_worktree(run.worktree_id)
        
        # Free up slot
        queue = ParallelQueue()
        await queue.on_run_complete(run_id)
```

**Configuration:**
```python
# config.py
LOREGARDEN_MAX_PARALLEL_AGENTS = 3  # 2-5 default
LOREGARDEN_WORKTREE_CLEANUP_DELAY_HOURS = 1  # Auto-cleanup after merge
```

**Database Updates:**
```sql
ALTER TABLE runs ADD COLUMN worktree_id TEXT REFERENCES worktree_states(id);
ALTER TABLE runs ADD COLUMN queue_position INT;
```

**Acceptance Criteria:**
- [x] 3 agents run simultaneously
- [x] Queue forms when limit reached
- [x] Auto-promotion on completion
- [x] Configuration respected

---

### Task 2.4: Conflict Detection & Reporting (2 days)

**Deliverable**: Warn users about merge conflicts before approval

```python
# In orchestration.py or new conflict_service.py

async def get_conflict_preview(
    self,
    worktree_id: str,
    target_branch: str = "main",
) -> dict:
    """
    Detect and report conflicts without actually merging.
    
    Returns: {
        "has_conflicts": bool,
        "conflicting_files": ["src/auth.ts", "src/db.ts"],
        "summary": "2 files conflict with main",
        "auto_mergeable": bool,  # Can git auto-merge?
    }
    """
    pass
```

**Integration:**
- Show in dashboard (red warning badge)
- Block merge if conflicts + can't auto-resolve
- Offer manual resolution option

**UI Component:**
```typescript
// client/src/components/WorktreeConflictWarning.tsx
<div className="conflict-warning">
  ⚠️ 2 files conflict with main
  <button onClick={viewDiff}>View conflicts</button>
  <button onClick={manualResolve}>Resolve manually</button>
</div>
```

**Acceptance Criteria:**
- [x] Conflicts detected before merge
- [x] User warned in dashboard
- [x] Can see conflicting files
- [x] Manual resolution offered

---

### Task 2.5: Database Schema & Models (1 day)

**Deliverable**: Database tables + SQLModel definitions

```python
# In models/domain.py

class WorktreeState(SQLModel, table=True):
    """Git worktree state for parallel feature execution."""
    __tablename__ = "worktree_states"
    
    id: str = Field(primary_key=True)
    run_id: str = Field(foreign_key="agent_runs.id", index=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    feature_name: str
    worktree_path: str
    base_branch: str = "main"
    status: str = "active"  # active, merged, failed, cleaned
    git_branch: Optional[str]
    files_modified: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    merged_at: Optional[datetime] = None
    cleaned_at: Optional[datetime] = None

class QueuePosition(SQLModel, table=True):
    """Queue position for waiting features."""
    __tablename__ = "queue_positions"
    
    id: str = Field(primary_key=True)
    ticket_id: str = Field(foreign_key="tickets.id")
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    position: int  # 1, 2, 3, etc
    created_at: datetime = Field(default_factory=utcnow)
```

**Acceptance Criteria:**
- [x] Tables created
- [x] Foreign keys correct
- [x] Indexes on common queries

---

### Task 2.6: API Endpoints for Parallel Execution (2 days)

**Deliverable**: REST endpoints for queue + worktree management

```python
# server/loregarden/api/parallel.py

@router.post("/runs/parallel/{ticket_id}")
async def create_parallel_run(ticket_id: str):
    """Create a new parallel run, queuing if needed."""
    pass

@router.get("/parallel/runs/{workspace_id}")
async def get_parallel_status(workspace_id: str):
    """Get all active + queued runs."""
    return {
        "active_runs": [...],  # Currently executing
        "queue": [...],        # Waiting to execute
        "available_slots": 1,  # Free slots remaining
    }

@router.get("/parallel/conflicts/{worktree_id}")
async def check_conflicts(worktree_id: str):
    """Check for merge conflicts."""
    pass

@router.post("/parallel/{worktree_id}/merge")
async def merge_worktree(worktree_id: str):
    """Merge worktree to main."""
    pass

@router.post("/parallel/{worktree_id}/cleanup")
async def cleanup_worktree(worktree_id: str):
    """Manually cleanup worktree."""
    pass
```

**Acceptance Criteria:**
- [x] All endpoints working
- [x] Proper error handling
- [x] Response format consistent

---

## Frontend Implementation Tasks

### Task 2.7: Parallel Feature Cards (2 days)

**Deliverable**: Dashboard component showing all parallel features

```typescript
// client/src/components/ParallelFeatureCards.tsx

export function ParallelFeatureCards({ workspaceId }: Props) {
  const { activeRuns, queuedRuns } = useParallelExecution(workspaceId);

  return (
    <div className="parallel-features">
      <h3>Active Features ({activeRuns.length}/3)</h3>
      <div className="cards-grid">
        {activeRuns.map(run => (
          <FeatureCard
            key={run.id}
            run={run}
            status="running"
          />
        ))}
      </div>

      {queuedRuns.length > 0 && (
        <>
          <h3>Queue ({queuedRuns.length})</h3>
          <div className="queue-list">
            {queuedRuns.map((run, i) => (
              <QueuedFeature
                key={run.id}
                position={i + 1}
                run={run}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function FeatureCard({ run, status }: Props) {
  const { ciStatus } = useCIStatus(run.ticket_id);

  return (
    <div className={`feature-card feature-${status}`}>
      <h4>{run.ticket_title}</h4>
      <div className="stage-progress">
        <span className="stage-name">{run.current_stage}</span>
        <span className="stage-status">{status}</span>
      </div>
      {ciStatus && (
        <div className="ci-badge ci-{ciStatus.status}">
          {ciStatus.status}
        </div>
      )}
      <div className="files-changed">
        +{run.files_added} -{run.files_removed}
      </div>
    </div>
  );
}
```

**Styling:**
- Grid layout: 3 columns (one per slot)
- Color-coded by stage + status
- Show CI status badge
- Show queue position with estimated time

**Acceptance Criteria:**
- [x] Cards render correctly
- [x] Updates in real-time
- [x] Queue shows position + time

---

### Task 2.8: Parallel Execution Timeline (1 day)

**Deliverable**: Gantt chart showing parallel execution timing

```typescript
// client/src/components/ParallelTimeline.tsx

export function ParallelTimeline({ workspaceId }: Props) {
  const { runs } = useParallelExecution(workspaceId);

  const timelineStart = Math.min(...runs.map(r => r.created_at));
  const timelineEnd = Math.max(...runs.map(r => r.completed_at || now()));

  return (
    <div className="parallel-timeline">
      <TimelineHeader start={timelineStart} end={timelineEnd} />
      {runs.map(run => (
        <TimelineRow key={run.id} run={run} />
      ))}
    </div>
  );
}

function TimelineRow({ run }: Props) {
  const startPercent = percent(run.created_at);
  const widthPercent = percent(run.duration);

  return (
    <div className="timeline-row">
      <span className="feature-name">{run.ticket_title}</span>
      <div className="timeline">
        <div
          className="task-bar"
          style={{
            left: `${startPercent}%`,
            width: `${widthPercent}%`,
          }}
        >
          {run.status}
        </div>
      </div>
    </div>
  );
}
```

**Visual:**
```
Feature 1:  ▓▓▓▓ (Implement)  ▓ (QA)
Feature 2:      ▓▓▓▓ (Implement)
Feature 3:        ▓▓ (Spec)

Legend: ▓ = Agent work  ◻ = Approval gate
```

**Acceptance Criteria:**
- [x] Timeline renders
- [x] Tasks positioned correctly
- [x] Overlaps visible

---

### Task 2.9: Conflict Warning Component (1 day)

**Deliverable**: UI for merge conflict warnings

```typescript
// client/src/components/WorktreeConflictWarning.tsx

export function WorktreeConflictWarning({
  worktreeId,
}: Props) {
  const { conflicts, loading } = useWorktreeConflicts(worktreeId);

  if (!conflicts?.has_conflicts) return null;

  return (
    <div className="conflict-warning">
      <div className="header">
        <span className="icon">⚠️</span>
        <span className="message">
          {conflicts.conflicting_files.length} files conflict with main
        </span>
      </div>

      <div className="details">
        <div className="files">
          {conflicts.conflicting_files.map(file => (
            <div key={file} className="file">
              {file}
              <button className="view-diff">Diff</button>
            </div>
          ))}
        </div>

        <div className="actions">
          {conflicts.auto_mergeable && (
            <button className="merge-btn">Auto-Merge</button>
          )}
          <button className="manual-btn">Resolve Manually</button>
          <button className="rebase-btn">Rebase</button>
        </div>
      </div>
    </div>
  );
}
```

**Styling:**
- Warning colors (amber/orange)
- Expandable file list
- Action buttons

**Acceptance Criteria:**
- [x] Warnings display
- [x] Files listed
- [x] Actions available

---

### Task 2.10: React Hooks for Parallel Execution (1 day)

**Deliverable**: Custom hooks for parallel data

```typescript
// client/src/hooks/useParallelExecution.ts

export function useParallelExecution(workspaceId: string) {
  const [activeRuns, setActiveRuns] = useState<Run[]>([]);
  const [queuedRuns, setQueuedRuns] = useState<Run[]>([]);
  const [loading, setLoading] = useState(false);

  // Poll for updates
  useEffect(() => {
    const interval = setInterval(async () => {
      const response = await api.get(`/parallel/runs/${workspaceId}`);
      setActiveRuns(response.data.active_runs);
      setQueuedRuns(response.data.queue);
    }, 5_000);  // Poll every 5s

    return () => clearInterval(interval);
  }, [workspaceId]);

  return { activeRuns, queuedRuns, loading };
}

// client/src/hooks/useWorktreeConflicts.ts

export function useWorktreeConflicts(worktreeId: string) {
  const [conflicts, setConflicts] = useState(null);
  const [loading, setLoading] = useState(false);

  const check = async () => {
    setLoading(true);
    const response = await api.get(`/parallel/conflicts/${worktreeId}`);
    setConflicts(response.data);
    setLoading(false);
  };

  useEffect(() => {
    check();
  }, [worktreeId]);

  return { conflicts, loading, refresh: check };
}
```

**Acceptance Criteria:**
- [x] Data fetched correctly
- [x] Auto-polling works
- [x] Real-time updates

---

## Integration & Testing Tasks

### Task 2.11: Unit Tests - Worktree Service (2 days)

```python
# tests/test_worktree_service.py

class TestWorktreeService:
    async def test_create_worktree(self):
        """Test worktree creation."""
        pass
    
    async def test_detect_conflicts(self):
        """Test conflict detection."""
        pass
    
    async def test_merge_worktree(self):
        """Test worktree merge."""
        pass
    
    async def test_cleanup(self):
        """Test worktree cleanup."""
        pass
    
    async def test_concurrent_worktrees(self):
        """Test 3+ worktrees simultaneously."""
        pass
```

**Coverage Target**: >80%

---

### Task 2.12: Unit Tests - Parallel Queue (1 day)

```python
# tests/test_parallel_queue.py

class TestParallelQueue:
    async def test_queue_feature(self):
        """Test feature queueing."""
        pass
    
    async def test_promote_from_queue(self):
        """Test auto-promotion."""
        pass
    
    async def test_max_concurrent(self):
        """Test concurrent limit enforcement."""
        pass
```

---

### Task 2.13: E2E Test - Parallel Execution (3 days)

```typescript
// tests/e2e/parallel-execution.spec.ts

test("should execute 3 features in parallel", async ({ page, request }) => {
  // 1. Create 3 feature tickets
  // 2. Run all 3 simultaneously
  // 3. Verify all run in parallel (not sequentially)
  // 4. Verify conflicts detected
  // 5. Verify all merge successfully
  // 6. Verify dashboard shows Gantt timeline
});

test("should queue 4th feature when max slots reached", async ({ page }) => {
  // 1. Start 3 features
  // 2. Queue 4th feature
  // 3. Verify queue position shown (4 in queue)
  // 4. Verify queue status shows estimated time
  // 5. When slot frees, verify 4th auto-promoted
});

test("should detect and warn about merge conflicts", async ({
  page,
  request,
}) => {
  // 1. Start 2 features editing same file
  // 2. Verify conflict warning appears
  // 3. Verify conflicting files listed
  // 4. Verify merge options shown
});
```

---

### Task 2.14: Documentation (1 day)

**Deliverable**: Setup guide + architecture doc

```markdown
docs/parallel-execution.md:
- How parallel execution works
- Configuration (max agents, queue size)
- Conflict resolution guide
- Troubleshooting

docs/worktree-management.md:
- Git worktree internals
- Manual cleanup
- Performance tips
```

---

## Summary of Deliverables

### Backend (4,000+ lines)
- ✅ Worktree service with create/merge/cleanup
- ✅ Parallel queue with auto-promotion
- ✅ Enhanced orchestration for parallel runs
- ✅ Conflict detection
- ✅ Database models + tables
- ✅ API endpoints (6+)
- ✅ Unit tests (20+)

### Frontend (1,500+ lines)
- ✅ Parallel feature cards component
- ✅ Parallel timeline (Gantt chart)
- ✅ Conflict warning component
- ✅ React hooks (polling)
- ✅ Styling (dark theme)
- ✅ Real-time updates

### Testing (800+ lines)
- ✅ Unit tests: worktree + queue (20+)
- ✅ E2E tests: parallel flow (3 scenarios)
- ✅ Coverage: >80%

### Documentation
- ✅ Parallel execution setup guide
- ✅ Worktree management guide
- ✅ Troubleshooting guide

---

## Success Metrics

| Metric | Target | Notes |
|--------|--------|-------|
| **Parallelism** | 3-5 agents | Configurable |
| **Feature Velocity** | 3-5x faster | vs. sequential |
| **Conflict Detection** | 100% | Pre-merge warning |
| **Queue Auto-Promotion** | 100% | On run completion |
| **Merge Success Rate** | >95% | With auto-merge |
| **Code Coverage** | >80% | Unit + E2E |

---

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| **Merge conflicts** | Pre-merge detection, auto-merge when possible |
| **Worktree cleanup failure** | Automatic cleanup on merge, manual override available |
| **Resource exhaustion** (5 agents) | Monitor disk/memory, auto-pause new if usage high |
| **Agent interference** | Isolated worktrees prevent file conflicts |

---

## Timeline

| Week | Tasks | Status |
|------|-------|--------|
| 3-4 | Worktree service, Queue system | Implementation |
| 5-6 | Orchestration, Conflict detection | Implementation |
| 6-7 | Frontend components, E2E tests | Testing |
| 7-8 | Integration, Docs, Final testing | Polish |

---

**Next**: Begin Task 2.1 (Git Worktree Service)
