# Phase 1A: CI Integration & Auto-Fix Loop - Task Breakdown

**Timeline**: Weeks 1-4 (approx 20 engineering days)  
**Goal**: Detect CI failures, automatically trigger fix-it agent, enable approval gates to check CI status

---

## Backend Implementation Tasks

### Task 1.1: Database Models for CI Integration (2 days)
**Deliverable**: CI-related SQLModel tables in domain.py

```python
# New enums
class CIStatus(str, Enum):
    PENDING = "pending"      # Not run yet
    PASSING = "passing"      # All checks passed
    FAILING = "failing"      # Tests/checks failed
    PARTIAL = "partial"      # Some checks passed
    SKIPPED = "skipped"      # CI skipped (e.g., docs-only)

# New models
class CIRunResult(SQLModel, table=True):
    id: str = Field(primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    ticket_id: str = Field(foreign_key="work_items.id", index=True)
    status: CIStatus
    provider: str  # "github_actions", "gitlab_ci", "generic_webhook"
    external_run_id: Optional[str]  # GitHub run ID, etc
    logs_url: Optional[str]
    failure_summary: Optional[str]  # Truncated error message
    full_logs: Optional[str]  # Complete output for inspection
    created_at: datetime
    updated_at: datetime

class AutoFixAttempt(SQLModel, table=True):
    id: str = Field(primary_key=True)
    ci_run_result_id: str = Field(foreign_key="ci_run_results.id", index=True)
    attempt_number: int  # 1, 2, 3
    run_id: Optional[str] = Field(foreign_key="runs.id")  # Link to fix-it agent run
    status: str  # "pending", "running", "succeeded", "failed"
    result_summary: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]
```

**Files to modify**:
- `server/loregarden/models/domain.py` — add CIStatus enum + models

**Acceptance criteria**:
- [ ] CIRunResult table created with all fields
- [ ] AutoFixAttempt table created
- [ ] DB migration runs without errors
- [ ] Models can be serialized/deserialized

---

### Task 1.2: CI Service - Core Logic (3 days)
**Deliverable**: `ci_service.py` with core operations

```python
# server/loregarden/services/ci_service.py
class CIService:
    """Manage CI integration, failure detection, auto-fix triggering."""
    
    async def process_webhook(
        self,
        workspace_id: str,
        provider: str,
        payload: dict,
    ) -> CIRunResult:
        """Process incoming CI webhook (GitHub Actions, GitLab CI, generic)."""
        # Parse payload based on provider
        # Determine if passed/failed/partial
        # Find associated ticket
        # Store result in DB
        # Trigger auto-fix if failed
        pass
    
    async def get_latest_ci_status(
        self,
        ticket_id: str,
    ) -> Optional[CIRunResult]:
        """Fetch latest CI result for a ticket."""
        pass
    
    async def trigger_auto_fix(
        self,
        ci_result: CIRunResult,
        max_attempts: int = 3,
    ) -> Optional[Run]:
        """Create auto-fix run: parse failure logs, trigger implementer agent."""
        # Check retry count
        # Extract error summary from logs
        # Create "fix-it" work item (child of original ticket)
        # Create run with pre-filled context (error logs)
        # Return created run
        pass
    
    async def get_auto_fix_history(
        self,
        ticket_id: str,
    ) -> list[AutoFixAttempt]:
        """Get all auto-fix attempts for a ticket."""
        pass
    
    async def skip_ci_check(self, ticket_id: str) -> None:
        """Manually skip CI gate (admin override)."""
        pass
```

**Files to create**:
- `server/loregarden/services/ci_service.py`

**Dependencies**:
- Task 1.1 (models)
- Existing `run_service.py`, `ticket_service.py`

**Acceptance criteria**:
- [ ] process_webhook parses GitHub Actions JSON correctly
- [ ] Auto-fix creates child work item + run
- [ ] Retry logic respects max_attempts config
- [ ] Error logs parsed and included in context
- [ ] All methods unit testable

---

### Task 1.3: Webhook API Endpoint (2 days)
**Deliverable**: `POST /api/ci/webhook/{workspace_id}` endpoint

```python
# In server/loregarden/api/ci.py (new file)
from fastapi import APIRouter, Request, Header
from loregarden.services.ci_service import CIService

router = APIRouter(prefix="/ci", tags=["ci"])

@router.post("/webhook/{workspace_id}")
async def receive_ci_webhook(
    workspace_id: str,
    request: Request,
    x_github_event: Optional[str] = Header(None),
    x_github_signature_256: Optional[str] = Header(None),
):
    """
    Receive CI results from GitHub Actions, GitLab CI, or generic webhook.
    
    GitHub Actions: X-GitHub-Event: workflow_run
    GitLab CI: X-Gitlab-Event: pipeline
    Generic: POST with JSON body
    """
    # Validate webhook signature (GitHub HMAC)
    # Detect provider from headers
    # Parse payload
    # Call ci_service.process_webhook()
    # Return success
    pass

@router.get("/status/{ticket_id}")
async def get_ci_status(ticket_id: str):
    """Get latest CI status + auto-fix history for ticket."""
    # Return CIRunResult + AutoFixAttempt history
    pass

@router.post("/manual-override/{ticket_id}")
async def skip_ci_check(ticket_id: str):
    """Admin: skip CI gate and proceed to approval."""
    pass
```

**Files to create**:
- `server/loregarden/api/ci.py`

**Files to modify**:
- `server/loregarden/main.py` — add CI router

**Dependencies**:
- Task 1.2 (ci_service)

**Acceptance criteria**:
- [ ] Webhook endpoint accepts GitHub Actions payload
- [ ] HMAC signature validation works
- [ ] Non-matching signature returns 403
- [ ] Endpoint returns 200 on success
- [ ] auto_fix trigger called for failures
- [ ] Status endpoint returns latest result

---

### Task 1.4: CI Auto-Fix Logic Integration (3 days)
**Deliverable**: Auto-fix agent trigger + context enrichment

**Requirements**:
1. Parse failure logs to extract key errors
2. Create child "fix-it" work item
3. Create run with error context in stage context
4. Call implementer agent with error logs + reproduction context
5. Track retry attempts in AutoFixAttempt table

**Implementation**:
```python
# In ci_service.py: trigger_auto_fix method

async def trigger_auto_fix(self, ci_result: CIRunResult, max_attempts: int = 3) -> Optional[Run]:
    # 1. Check if already at max retries
    existing_attempts = await self.get_auto_fix_history(ci_result.ticket_id)
    if len(existing_attempts) >= max_attempts:
        logger.info(f"CI: Max auto-fix attempts reached for {ci_result.ticket_id}")
        return None
    
    # 2. Parse failure summary from logs
    error_context = self._extract_error_context(ci_result.full_logs)
    
    # 3. Create child "fix-it" work item
    fix_it_item = WorkItem(
        workspace_id=ci_result.workspace_id,
        parent_id=ci_result.ticket_id,
        title=f"CI Fix: {error_context['error_type']}",
        work_item_type=WorkItemType.TASK,
        state=TicketState.IN_PROGRESS,
    )
    # Save to DB
    
    # 4. Create stage context with error logs
    stage_context = {
        "ci_failure": {
            "error_summary": error_context["summary"],
            "full_logs": ci_result.full_logs,
            "failing_tests": error_context["failing_tests"],
            "error_type": error_context["error_type"],
        }
    }
    
    # 5. Create run for implementer agent
    run = Run(
        ticket_id=fix_it_item.id,
        workspace_id=ci_result.workspace_id,
        stage_id=implementer_stage_id,
        stage_context_json=json.dumps(stage_context),
    )
    # Save and start execution
    
    # 6. Track attempt
    attempt = AutoFixAttempt(
        ci_run_result_id=ci_result.id,
        attempt_number=len(existing_attempts) + 1,
        run_id=run.id,
        status="running",
    )
    # Save to DB
    
    return run

def _extract_error_context(self, logs: str) -> dict:
    """Parse test failure logs to extract key info."""
    # Regex patterns for common test frameworks
    # Extract: failing tests, error message, stack trace
    # Return structured dict
    pass
```

**Files to modify**:
- `server/loregarden/services/ci_service.py`

**Dependencies**:
- Task 1.2, 1.3
- Existing `run_service.py`, `ticket_service.py`

**Acceptance criteria**:
- [ ] Auto-fix work item created as child of original
- [ ] Run created with error context
- [ ] Error logs parsed and included in stage context
- [ ] AutoFixAttempt record created
- [ ] Agent receives error context in prompt
- [ ] Retry counter prevents infinite loops

---

### Task 1.5: Configuration & Environment Variables (1 day)
**Deliverable**: CI configuration in settings

```python
# In server/loregarden/config.py
class Settings(BaseSettings):
    # CI Integration
    LOREGARDEN_CI_WEBHOOK_SECRET: str = ""  # GitHub webhook secret
    LOREGARDEN_CI_RETRY_LIMIT: int = 3  # Max auto-fix attempts
    LOREGARDEN_CI_ENABLED: bool = True  # Feature flag
    LOREGARDEN_CI_LOG_RETENTION_DAYS: int = 30  # How long to keep logs
    LOREGARDEN_CI_AUTO_FIX_TIMEOUT: int = 600  # 10 min timeout for fix agent
```

**Files to modify**:
- `server/loregarden/config.py`

**Documentation**:
- `docs/ci-setup.md` — GitHub Actions webhook setup guide

**Acceptance criteria**:
- [ ] Environment variables read from .env
- [ ] Webhook secret validated
- [ ] Defaults sensible

---

## Frontend Implementation Tasks

### Task 1.6: CI Status Data Fetching (2 days)
**Deliverable**: React hooks for CI data

```typescript
// client/src/hooks/useCIStatus.ts
export function useCIStatus(ticketId: string) {
  const [ciStatus, setCIStatus] = useState<CIRunResult | null>(null);
  const [autoFixHistory, setAutoFixHistory] = useState<AutoFixAttempt[]>([]);
  const [loading, setLoading] = useState(false);

  // Poll for CI status updates (every 10s while failing)
  useEffect(() => {
    const interval = setInterval(async () => {
      const result = await api.getCIStatus(ticketId);
      setCIStatus(result);
      const history = await api.getAutoFixHistory(ticketId);
      setAutoFixHistory(history);
    }, 10_000);
    return () => clearInterval(interval);
  }, [ticketId]);

  return { ciStatus, autoFixHistory, loading };
}

// client/src/hooks/useAutoFix.ts
export function useAutoFix(ticketId: string) {
  const [isFixing, setIsFixing] = useState(false);

  const triggerManualFix = async () => {
    setIsFixing(true);
    try {
      await api.triggerManualAutoFix(ticketId);
      // Poll for result
    } finally {
      setIsFixing(false);
    }
  };

  return { triggerManualFix, isFixing };
}
```

**Files to create**:
- `client/src/hooks/useCIStatus.ts`
- `client/src/hooks/useAutoFix.ts`

**Files to modify**:
- `client/src/api/client.ts` — add CI endpoints

**Acceptance criteria**:
- [ ] useCIStatus polls API
- [ ] Auto-fix history fetched
- [ ] Manual trigger works
- [ ] Polling stops when CI passes

---

### Task 1.7: CI Status Widget Component (2 days)
**Deliverable**: Dashboard CI status badge + expandable panel

```typescript
// client/src/components/CIStatusWidget.tsx
export function CIStatusWidget({ ticketId }: { ticketId: string }) {
  const { ciStatus, autoFixHistory } = useCIStatus(ticketId);
  const [expanded, setExpanded] = useState(false);

  if (!ciStatus) return null;

  const statusIcon = {
    passing: "✓",    // Green checkmark
    failing: "✗",    // Red X
    partial: "⚠️",   // Warning
    pending: "⏳",   // Hourglass
  }[ciStatus.status];

  return (
    <div className="ci-widget">
      <button
        className={`ci-badge ci-${ciStatus.status}`}
        onClick={() => setExpanded(!expanded)}
      >
        <span className="icon">{statusIcon}</span>
        <span className="label">CI {ciStatus.status}</span>
        <span className="time">5m ago</span>
      </button>

      {expanded && (
        <CILogsPanel
          logs={ciStatus.full_logs}
          failureSummary={ciStatus.failure_summary}
          autoFixHistory={autoFixHistory}
          ticketId={ticketId}
        />
      )}
    </div>
  );
}

// client/src/components/CILogsPanel.tsx
function CILogsPanel({ logs, failureSummary, autoFixHistory, ticketId }) {
  return (
    <div className="ci-logs-panel">
      <div className="logs-header">
        <h3>CI Logs</h3>
        <button className="expand-btn">Full logs →</button>
      </div>

      {failureSummary && (
        <div className="failure-summary">
          <strong>Error:</strong> {failureSummary}
        </div>
      )}

      <div className="logs-content">
        <pre>{logs?.slice(-2000)}</pre> {/* Last 2000 chars */}
      </div>

      <div className="auto-fix-section">
        <h4>Auto-Fix History</h4>
        {autoFixHistory.map((attempt) => (
          <div key={attempt.id} className="fix-attempt">
            <span>Attempt {attempt.attempt_number}</span>
            <span className={`status-${attempt.status}`}>{attempt.status}</span>
          </div>
        ))}
        <button className="manual-fix-btn">Retry Auto-Fix</button>
      </div>
    </div>
  );
}
```

**Files to create**:
- `client/src/components/CIStatusWidget.tsx`
- `client/src/components/CILogsPanel.tsx`

**Files to modify**:
- `client/src/pages/Dashboard.tsx` — add widget to top-right
- `client/src/components/TicketDetailsModal.tsx` — add CI section

**Styling**:
- Add to `client/src/App.css` or new `client/src/components/CI.css`

**Acceptance criteria**:
- [ ] Badge shows correct status (color + icon)
- [ ] Expandable panel shows logs
- [ ] Auto-fix history displayed
- [ ] Manual trigger button works
- [ ] Real-time updates as CI runs

---

### Task 1.8: Approval Gate CI Check (1 day)
**Deliverable**: Block approval if CI failing

```typescript
// In ApprovalCard component or new ApprovalGateChecks component
<div className="approval-gate-checks">
  {/* Existing approvals */}

  {/* CI Check */}
  {ciStatus && ciStatus.status === "failing" && (
    <div className="check failing">
      <span className="icon">✗</span>
      <span className="label">CI Tests Failing</span>
      <span className="detail">{ciStatus.failure_summary}</span>
      <button className="action-btn">Auto-fix available →</button>
    </div>
  )}

  {ciStatus && ciStatus.status === "passing" && (
    <div className="check passed">
      <span className="icon">✓</span>
      <span className="label">CI Tests Passing</span>
    </div>
  )}
</div>

// Block approval button if CI failing
const canApprove = ciStatus?.status !== "failing";
<button disabled={!canApprove} onClick={onApprove}>
  {canApprove ? "Approve" : "Wait for CI"}
</button>
```

**Files to modify**:
- `client/src/components/ApprovalCard.tsx`

**Acceptance criteria**:
- [ ] Approval blocked if CI failing
- [ ] CI status shown in approval card
- [ ] Auto-fix link visible

---

## Integration & Testing Tasks

### Task 1.9: Unit Tests - Backend (2 days)
**Deliverable**: Tests for all backend services

```python
# tests/test_ci_service.py
class TestCIService:
    async def test_process_webhook_github_actions(self):
        """Test GitHub Actions webhook parsing."""
        pass
    
    async def test_auto_fix_trigger(self):
        """Test auto-fix run creation."""
        pass
    
    async def test_retry_limit(self):
        """Test max attempts enforcement."""
        pass
    
    async def test_error_parsing(self):
        """Test error log extraction."""
        pass

# tests/test_ci_api.py
class TestCIAPI:
    async def test_webhook_signature_validation(self):
        """Test HMAC validation."""
        pass
    
    async def test_webhook_missing_signature(self):
        """Reject webhooks without valid signature."""
        pass
```

**Files to create**:
- `tests/test_ci_service.py`
- `tests/test_ci_api.py`

**Acceptance criteria**:
- [ ] All CI service methods have unit tests
- [ ] API endpoints tested
- [ ] Edge cases covered (missing signature, invalid payload)
- [ ] >80% code coverage

---

### Task 1.10: End-to-End Test (1 day)
**Deliverable**: Playwright E2E test for full CI flow

```typescript
// tests/e2e/ci-auto-fix.spec.ts
test.describe("CI Auto-Fix Flow", () => {
  test("should detect CI failure and trigger auto-fix", async ({ page }) => {
    // 1. Create feature ticket
    // 2. Run implementation stage
    // 3. Simulate CI webhook with failure
    // 4. Wait for auto-fix run to start
    // 5. Simulate auto-fix success
    // 6. Verify CI status shows passing
    // 7. Verify approval gate unblocked
  });
});
```

**Files to create**:
- `tests/e2e/ci-auto-fix.spec.ts`

**Acceptance criteria**:
- [ ] E2E test passes
- [ ] Full flow works: failure → auto-fix → passing → approval

---

### Task 1.11: Documentation (1 day)
**Deliverable**: Setup guides and reference docs

**Files to create**:
- `docs/ci-setup.md` — GitHub Actions webhook configuration
- `docs/ci-auto-fix-reference.md` — How auto-fix works, configuration

**Content**:
- GitHub Actions: how to add webhook
- GitLab CI: how to add webhook
- Generic webhook: JSON format
- Configuration: retry limits, timeouts
- Troubleshooting: webhook not firing, logs not appearing

**Acceptance criteria**:
- [ ] Setup guide covers GitHub Actions
- [ ] Setup guide covers GitLab CI
- [ ] Configuration options documented
- [ ] Troubleshooting guide helpful

---

## Summary of Deliverables

### Backend
- ✅ CIRunResult + AutoFixAttempt database models
- ✅ CIService with webhook processing, auto-fix logic
- ✅ CI API endpoints (webhook, status, manual override)
- ✅ Configuration for CI settings
- ✅ Unit + E2E tests
- ✅ Documentation

### Frontend
- ✅ useCIStatus hook (polling)
- ✅ CIStatusWidget + CILogsPanel components
- ✅ CI check in ApprovalCard
- ✅ Manual override + auto-fix trigger UI

### Acceptance Criteria (All Tasks)
- [ ] CI webhook receives GitHub Actions results
- [ ] Auto-fix agent triggers on test failure
- [ ] Dashboard shows CI status in real-time
- [ ] Approval gate blocks on failing CI
- [ ] Manual retry/skip options available
- [ ] Auto-fix attempt respects max retry limit
- [ ] E2E test: failure → auto-fix → passing → approval passes
- [ ] Documentation complete + clear
- [ ] >80% code coverage

---

## Next Phase Preparation
- After Phase 1A ships, Phase 1B (Parallel Execution) can begin
- No dependencies on parallel execution for CI integration
- CI auto-fix is foundation for later approval rules testing
