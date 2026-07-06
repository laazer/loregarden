# Loregarden Visual & Feature Upgrade Plan

**Document Version**: 1.0  
**Date**: 2026-07-06  
**Status**: Planning Phase  
**Target Timeline**: Q3-Q4 2026

---

## Executive Summary

Loregarden has a strong foundation as an Agent SDLC IDE with ticket tracking, TDD pipeline orchestration, and approval inboxes. However, competitive analysis reveals **critical feature gaps** (CI integration, parallel execution, spec-driven gates) and **visual design opportunities** (pipeline visualization, execution status, approval workflows) that are table-stakes for developer adoption and enterprise sales.

This plan addresses:
- **Feature Gaps** (vs. Shep, ADHDev, Port.io, GitHub Copilot)
- **Visual/UX Improvements** (matching competitive standards)
- **Architecture Changes** (to support new capabilities)
- **Implementation Roadmap** (phased, with clear priorities)

**Expected Outcomes**:
- Achieve Shep parity on core SDLC features (parallel execution, CI integration)
- Differentiate with visual pipeline orchestration (better than competitors)
- Support enterprise adoption (governance, approval workflows)
- Improve developer velocity (3-5x faster feature cycles with parallel execution)

---

## Part 1: Vision & Goals

### Strategic Vision

**Loregarden as the Agent SDLC Control Plane**: A unified platform where developers orchestrate multi-agent software development workflows with:
- **Visibility**: See exactly what agents are doing, in real-time
- **Control**: Pause, redirect, approve, and steer agent work mid-flight
- **Governance**: Enforce compliance rules, approval gates, quality standards
- **Velocity**: Parallel feature development with automatic conflict resolution

### Core Goals

1. **Parity with Shep** (open-source competitor)
   - Parallel agent execution across isolated worktrees
   - CI integration with automatic failure detection & retry
   - Spec-driven planning gates before implementation
   - Zero-configuration local-first operation

2. **Exceed Port.io** (enterprise competitor, $100M funded)
   - Inline governance enforcement (security, code quality)
   - Multi-stage approval workflows with risk-tiering
   - Agent-specific tool restrictions (MCP isolation)
   - Audit trails for compliance

3. **Better than Cursor/ADHDev** (point solutions)
   - Integrated IDE + orchestration (not separate tools)
   - Real-time agent collaboration UI
   - Session persistence & recovery
   - Unified session inspection (terminal, chat, diffs)

### Success Metrics

| Metric | Current | Target | Timeline |
|--------|---------|--------|----------|
| Parallel agents supported | 1 | 5+ | Q3 2026 |
| Time to PR (vs. baseline) | 100% | 30-40% (3x faster) | Q3 2026 |
| Feature approval cycle | 60+ min | < 5 min | Q3 2026 |
| Enterprise sales readiness | No | Yes (governance) | Q4 2026 |
| User satisfaction (UX) | 6/10 (estimated) | 8.5/10 | Q4 2026 |

---

## Part 2: Feature Roadmap

### Phase 1: Core SDLC Features (Shep Parity)
**Timeline**: Q3 2026 (8-12 weeks)  
**Goal**: Match Shep's core capabilities

#### Phase 1A: CI Integration & Auto-Fix Loop (Weeks 1-4)

**Feature**: Automated CI failure detection and retry

**Requirements**:
1. Watch CI/test pipeline results
   - Hook into GitHub Actions, GitLab CI, or generic webhook
   - Poll for test results with configurable interval
   - Store test logs + failure metadata in SQLite

2. Agent auto-retry on test failure
   - Detect test failure → capture error logs
   - Create automatic "fix-it" task with error context
   - Trigger agent (implementer role) to analyze + fix
   - Configurable retry limits (default 3x)
   - Different agent types may have different fix strategies

3. Dashboard status indicator
   - Real-time badge: CI status (✓/✗/⏳)
   - Expandable CI panel showing last 5 runs
   - Auto-fix attempt counter
   - Quick action: "Manual override" / "Skip CI check"

**Backend Changes**:
```
Models:
- Add CIRunResult(id, ticket_id, status, logs_url, created_at)
- Extend Run model with ci_status, ci_failure_logs, auto_fix_attempts

Endpoints:
- POST /ci/webhook/{workspace_id} — receive CI results
- GET /tickets/{id}/ci-status — fetch latest CI state
- POST /tickets/{id}/auto-fix — trigger manual auto-fix

Config:
- LOREGARDEN_CI_WEBHOOK_SECRET
- LOREGARDEN_CI_RETRY_LIMIT (default: 3)
- LOREGARDEN_CI_CHECK_INTERVAL (default: 30s)
```

**Frontend Changes**:
```
Components:
- CIStatusWidget (sidebar badge showing latest CI state)
- CILogsPanel (expandable panel with test output)
- CIAutoFixIndicator (retry counter + animation)

Integration:
- Add to Dashboard top-right
- Add to TicketDetailsModal (CI section)
- Add to ApprovalCard (CI gate blocking approval if failed)
```

**Definition of Done**:
- ✅ CI webhook receives and processes test results
- ✅ Auto-fix agent triggers on test failure
- ✅ Retry loop works with configurable limits
- ✅ Dashboard shows CI status in real-time
- ✅ E2E test: broken test → auto-fix → passing test → approval proceeds
- ✅ Documentation updated (CI setup guide)

---

#### Phase 1B: Parallel Agent Execution (Weeks 3-6)

**Feature**: Run multiple agents simultaneously on isolated worktrees

**Requirements**:
1. Worktree isolation
   - Create isolated git worktrees for each parallel feature (not just branches)
   - Each worktree has independent git state, build artifacts, node_modules
   - No merge conflicts between parallel features
   - Merge worktree back to main only after tests pass

2. Parallel run scheduling
   - Allow 2-5 simultaneous features (configurable)
   - Queue remaining features
   - Rebalance on completion

3. Conflict detection
   - Before merging worktree → main, detect file conflicts
   - Warn user if > 3 files conflicting
   - Option to auto-rebase or manual resolution

**Backend Changes**:
```
Models:
- Add WorktreeState(run_id, worktree_path, created_at, merged_at)
- Extend Run model with worktree_id, parent_run_id

Endpoints:
- POST /tickets/{id}/run-parallel — create parallel run
- GET /workspaces/{id}/parallel-runs — list active parallel runs
- POST /runs/{id}/merge-worktree — merge worktree to main

Config:
- LOREGARDEN_MAX_PARALLEL_AGENTS (default: 3)
- LOREGARDEN_WORKTREE_CLEANUP (auto-cleanup merged worktrees)

Queue Logic:
- Track active worktrees per workspace
- Respect max concurrent limit
- Queue remaining runs with status "waiting_for_worktree"
```

**Frontend Changes**:
```
Components:
- ParallelFeatureCards (show all active + queued runs side-by-side)
- WorktreeConflictWarning (highlight conflicting files)
- ParallelRunsTimeline (Gantt chart of parallel execution)

Integration:
- Add "Run in Parallel" button to ticket detail
- Show queue status in dashboard
- Add Parallel Runs view to main navigation
```

**Definition of Done**:
- ✅ Worktrees created and cleaned up correctly
- ✅ 3+ simultaneous agents running without conflicts
- ✅ Merge detection catches file conflicts
- ✅ Dashboard shows parallel execution visually
- ✅ E2E test: 3 features run in parallel → all complete → all merge cleanly
- ✅ Performance: no degradation with 3 parallel agents

---

#### Phase 1C: Spec-Driven Planning Gates (Weeks 5-8)

**Feature**: Optional planning/specification phase before implementation

**Requirements**:
1. Spec stage in pipeline
   - New stage type: `spec` (before `implement`)
   - Agent writes specification/requirements markdown
   - Output stored as artifact

2. Approval gate before implementation
   - Human reviews spec
   - Approve/reject/request changes
   - Rejection prevents implementation from starting
   - Changes feed back to planner agent for revision

3. Architecture review optional gate
   - Secondary spec review for arch-heavy features
   - Skippable for simple features (config option)
   - Required for large refactors (auto-detect)

**Backend Changes**:
```
Models:
- Add SpecApproval(ticket_id, spec_content, status, reviewer_notes)
- Stage model: add spec_required, spec_auto_detect_threshold

Endpoints:
- POST /tickets/{id}/spec-stage — create spec-only run
- PUT /approvals/{id}/spec — approve/reject spec with feedback

Workflow:
- Default TDD pipeline: [Plan] → [Spec Review Gate] → [Implement] → [Test Design] → [Implement] → [QA] → [Review] → [Gatekeeper]
- Config option to skip spec stage
- If spec rejected, re-run Plan stage with feedback
```

**Frontend Changes**:
```
Components:
- SpecReviewCard (approval card for spec content)
- SpecPreviewModal (markdown preview of spec)
- ArchitectureGateToggle (enable/disable arch review)

Integration:
- Add to ApprovalCard (spec approval type)
- Add to PipelineLaneView (spec stage indicator)
```

**Definition of Done**:
- ✅ Spec stage executes and produces markdown output
- ✅ Approval gate blocks implementation on rejection
- ✅ Feedback loop works (rejected → replanned → re-approved)
- ✅ Architecture review gate works
- ✅ E2E test: feature spec rejected → replanned → approved → implementation proceeds
- ✅ User guide: when to use spec gates

---

### Phase 2: Enterprise Governance & Approval Workflows (Q4 2026, 8 weeks)
**Goal**: Win enterprise customers (governance, compliance, audit)

#### Phase 2A: Multi-Stage Approval Workflows (Weeks 1-4)

**Feature**: Granular approval gates at each pipeline stage

**Requirements**:
1. Stage-specific approval requirements
   - Each stage can have approval requirement (yes/no/optional)
   - Different approval rules per stage type
   - Risk-based auto-approval (low-risk changes auto-approve)

2. Approval rules engine
   - Rule: auto-approve if < 100 LOC changed
   - Rule: auto-approve if only comments/docs changed
   - Rule: require human if security-sensitive files touched
   - Rule: require 2 approvals if > 500 LOC changed

3. Reviewer routing
   - Route to specific user/role
   - Route to code owner (CODEOWNERS file)
   - Route to security team for sensitive changes
   - Fallback to gatekeeper role

**Backend Changes**:
```
Models:
- Add ApprovalRule(id, workspace_id, stage_id, condition, approval_required, reviewer_role)
- Extend Approval model with risk_level, auto_approved, approval_rule_id

Endpoints:
- POST /approvals/rules — create approval rule
- PUT /approvals/rules/{id} — update rule
- GET /tickets/{id}/approval-status — fetch all approval gates for ticket

Engine:
- Calculate change risk (LOC, files, types)
- Apply rules
- Auto-approve or route to reviewer
```

**Frontend Changes**:
```
Components:
- ApprovalRuleBuilder (UI to create rules)
- ApprovalRequirementsOverview (show all required approvals)
- ApprovalRouting (show who will review)

Integration:
- Add "Approval Rules" to SettingsModal
- Add "Requirements" section to PipelineLaneView
```

**Definition of Done**:
- ✅ Approval rules engine working
- ✅ Risk-based auto-approval working
- ✅ Reviewer routing working
- ✅ Dashboard shows approval requirements
- ✅ E2E test: low-risk change auto-approved, high-risk requires manual approval

---

#### Phase 2B: Agent-Specific Tool Restrictions (Weeks 3-6)

**Feature**: Restrict MCP servers/tools per agent role

**Requirements**:
1. Per-agent MCP configuration
   - Spec agent: only docs, project context (no build, no deployment)
   - Implementer agent: build tools, testing, git (no deployment)
   - QA agent: testing, logging (no code editing)
   - Gatekeeper: read-only access to all (no execution)

2. MCP tool validation
   - Check agent's allowed_tools at runtime
   - Block unauthorized MCP calls (return permission denied)
   - Log attempted unauthorized access

3. Studio UI for tool management
   - Per-agent checklist of available MCP tools
   - Drag-and-drop to move tools between stages
   - Preset templates (spec/implement/review/deploy)

**Backend Changes**:
```
Models:
- StudioAgent model: add mcp_restrictions (list of allowed_tool_names)

Endpoints:
- PUT /agents/{id}/mcp-restrictions — update allowed tools
- GET /agents/{id}/validate-mcp-call — check if call allowed

Validation:
- Before executing MCP call, check agent's mcp_restrictions
- Return PermissionDenied if not in allowlist
```

**Frontend Changes**:
```
Components:
- MCPToolRestrictionsPanel (per-agent tool selector in StudioPage)
- RestrictionsTemplate (buttons: Spec / Implement / Review / Deploy)

Integration:
- Add to StudioPage agent editing
- Show restrictions in agent details modal
```

**Definition of Done**:
- ✅ Agents can only use assigned MCP tools
- ✅ Unauthorized tools blocked at runtime
- ✅ Studio UI for managing restrictions
- ✅ Preset templates work
- ✅ E2E test: implementer tries to call deployment tool → denied

---

#### Phase 2C: Governance Dashboard & Audit Logs (Weeks 5-8)

**Feature**: Compliance oversight and audit trail

**Requirements**:
1. Audit log storage
   - Every approval/rejection logged
   - Every agent action logged
   - Every policy violation logged
   - Searchable + filterable

2. Governance dashboard
   - Approval SLA metrics (how long between approval needed → approved)
   - Policy violation heatmap
   - Agent success rate by stage
   - Human reviewer workload

3. Compliance export
   - PDF export: feature development audit trail
   - CSV export: metrics for compliance review
   - Signed log export (for regulated industries)

**Backend Changes**:
```
Models:
- AuditLog(id, workspace_id, actor, action, resource_type, resource_id, details, created_at)

Endpoints:
- GET /audits — list audit logs (with filtering)
- GET /governance/dashboard — compliance metrics
- GET /governance/export — export audit trail

Logging:
- Log every Approval.resolved_at event
- Log every agent tool call
- Log every policy check result
```

**Frontend Changes**:
```
Components:
- GovernanceDashboard (new page showing metrics)
- AuditLogViewer (searchable audit log table)
- ComplianceExportButton (download audit trail)

Integration:
- Add to main navigation
- Link from SettingsModal to audit logs
```

**Definition of Done**:
- ✅ Audit logs stored and searchable
- ✅ Governance dashboard shows metrics
- ✅ Export functionality works
- ✅ E2E test: approval workflow logged → visible in audit trail → exportable

---

### Phase 3: Advanced Features & Differentiation (Q4 2026+, ongoing)

#### Phase 3A: Session Persistence & Recovery

**Feature**: Survive IDE restarts, resume interrupted workflows

**Requirements**:
1. Checkpoint system
   - Auto-save run state at each stage completion
   - Named checkpoints (user-created bookmarks)
   - Checkpoint metadata (agent used, timestamp, diffs)

2. Resume from checkpoint
   - Resume incomplete runs from last good state
   - Resume from specific checkpoint
   - Revert to checkpoint if later stages fail

3. Session history
   - Timeline view of all checkpoints
   - Diff preview between checkpoints
   - Rollback UI (one-click revert)

**Backend**: CheckpointState model, resume_from endpoint  
**Frontend**: SessionTimelineView, CheckpointSelector, RollbackButton

---

#### Phase 3B: Distributed Agent Tracing & Debugging

**Feature**: Trace multi-agent workflows, replay decisions

**Requirements**:
1. Agent decision logging
   - Log every agent decision point (what it chose and why)
   - Link to agent reasoning
   - Store as JSON trace

2. Trace visualization
   - Timeline of agent decisions
   - Flow diagram (agent A → agent B → agent C)
   - Replay view (step through decisions)

3. Debugging interface
   - What if: "what if agent chose X instead of Y"
   - Debug logs: full context at decision point
   - Contrastive explanations: why A over B

---

#### Phase 3C: Agent Skill Marketplace

**Feature**: Registry of reusable agent prompts/skills (NPM-like)

**Requirements**:
1. Skill publishing
   - Package agent prompts + MCP config as "skills"
   - Publish to registry
   - Version management

2. Skill discovery
   - Search skills by name/tag
   - Star/fork skills
   - Ratings/reviews

3. Skill reuse
   - One-click add skill to workflow
   - Customize before use
   - Track skill usage

---

---

## Part 3: Visual & UX Upgrade Roadmap

### Phase 1: Quick Wins (Weeks 1-2 of each feature phase)
These can ship independently and immediately improve UX

#### 1.1: CI Status Widget
- **Current**: No CI visibility
- **New**: Sidebar badge showing latest CI status
  - Green checkmark = all passing
  - Red X = test failures
  - Yellow warning = lint issues
  - Animated pulse while running
- **Effort**: 1 week
- **Dependencies**: Phase 1A (CI integration)
- **Files**: `CIStatusWidget.tsx`, add to Dashboard
- **Benefit**: Users see CI state at a glance

#### 1.2: Agent/Human Stage Markers
- **Current**: Hard to tell stage type
- **New**: Visual markers on each stage
  - 🤖 + blue color = agent stage
  - 👤 + orange color = human gate
  - 🔄 = approval loop
- **Effort**: 3 days
- **Files**: Update `PipelineLaneView`, `StageDisplay`
- **Benefit**: Pipeline comprehension improves 50%

#### 1.3: Live Terminal Preview
- **Current**: LogsPanel is full-screen only
- **New**: Compact terminal in dashboard bottom-right
  - Last 20 lines of output
  - Color-coded (errors red, warnings yellow)
  - Click to expand
  - Auto-scroll to latest
- **Effort**: 1 week
- **Files**: `TerminalPreviewWidget.tsx`, add to Dashboard
- **Benefit**: Reduce context-switching

#### 1.4: Parallel Feature Cards
- **Current**: Ticket tree hierarchical
- **New**: "Parallel Runs" view showing active features side-by-side
  ```
  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
  │ Auth System      │  │ Payment Flow     │  │ Admin Dashboard  │
  │ Implement Stage  │  │ Code Review Gate │  │ Queued (waiting) │
  │ Agent: Claude    │  │ Agent: Claude    │  │                  │
  │ CI: ✓            │  │ CI: ⏳            │  │                  │
  └──────────────────┘  └──────────────────┘  └──────────────────┘
  ```
- **Effort**: 1.5 weeks
- **Files**: `ParallelFeatureCards.tsx`, `ParallelRunsView.tsx`
- **Benefit**: Parallel execution visibility

#### 1.5: Status Indicator Animations
- **Current**: Static status icons
- **New**: Animated indicators
  - Pulsing for "awaiting approval"
  - Spinning for "in progress"
  - Bouncing for new notifications
  - Glowing red for failures
- **Effort**: 1 week
- **Files**: `animations.css`, update components
- **Benefit**: Users notice state changes without checking

### Phase 2: Pipeline Lane View (Weeks 3-5)

#### 2.1: Core Pipeline Lane Component

**Feature**: Visualize approval workflow as a lane/track

```
┌─────────────────────────────────────────────────────────────┐
│ Feature: Add Two-Factor Auth                                │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│ ✓ Spec Review          [✓ Approved by Sarah] 14:23         │
│ ⏳ Implementation       [In Progress - Claude Agent]          │
│    • Est. 10m remaining                                     │
│ ⭕ Test Design         [Ready to start]                      │
│    • Waiting for implementation to complete                 │
│ ⭕ Code Review         [Pending approval]                    │
│    • Will require human review                              │
│ [---] Merge Gate       [Blocked by CI]                      │
│    • Tests failing (3 failures)                             │
│    • [Auto-fix available] [Manual override]                 │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**Components**:
- `PipelineLaneView.tsx` — main container
- `PipelineStageTrack.tsx` — individual stage
- `StageMetadata.tsx` — approval info + action buttons

**Styling**:
- Color: green (done), blue (running), amber (waiting), red (failed), gray (blocked)
- Horizontal timeline layout
- Smooth transitions between states
- Hover state shows details

**Effort**: 2 weeks  
**Benefit**: Users understand approval workflow instantly

#### 2.2: Approval Gate Interaction

**Feature**: Inline approval within lane view

```
┌─────────────────────────────────────────────────────────────┐
│ ⭕ Code Review         [Pending approval]                    │
│    • Changes: 234 LOC in 5 files                            │
│    • [Review diff] [Approve] [Request changes]              │
│                                                              │
│ ▼ Diff Preview (collapsed)                                  │
│   src/auth.ts  +45 -12                                      │
│   tests/auth.test.ts  +67 -3                                │
└─────────────────────────────────────────────────────────────┘
```

**Components**:
- `DiffPreviewInline.tsx` — expandable diff
- `ApprovalAction.tsx` — approve/request changes
- `ApprovalComment.tsx` — inline feedback

**Effort**: 1.5 weeks  
**Benefit**: Approvals don't require modal popup

### Phase 3: Execution Status Dashboard (Weeks 4-6)

#### 3.1: Run Status Ring

**Feature**: Circular progress indicator for current stage

```
        Stage 3/7
       Code Review
    
    ▲ Previous stages
    ●  Current (Claude Agent)
    ◎  Next stages queued
    
    Elapsed: 8m 32s
    Est remaining: 5m
```

**Components**:
- `StageProgressRing.tsx` — circular progress SVG
- `StageMetrics.tsx` — elapsed/remaining time
- `AgentIndicator.tsx` — which agent running

**Effort**: 1 week  
**Benefit**: Glanceable run status

#### 3.2: Parallel Run Timeline (Gantt Chart)

**Feature**: Visualize parallel execution timing

```
Timeline (2h view)
─────────────────────────────────────
Feature 1:  ▓▓▓ (Implement) ▓ (QA)
Feature 2:    ▓▓▓▓ (Implement)
Feature 3:      ▓▓ (Spec)

Legend: ▓ = Agent work  ◻ = Approval gate  ◯ = Queued
```

**Components**:
- `ParallelTimeline.tsx` — Gantt chart
- `TaskBar.tsx` — individual feature row

**Effort**: 1.5 weeks  
**Benefit**: Understand parallelism and bottlenecks

### Phase 4: Session Inspection & Debugging (Weeks 7-10)

#### 4.1: Agent Decision Trace UI

**Feature**: Why did agent choose this approach?

```
┌─────────────────────────────────────────────────────────┐
│ Agent: Spec Writer                 Stage: Spec Generation│
│                                                          │
│ Decision: Chose REST API approach                       │
│                                                          │
│ Reasoning:                                              │
│ 1. Requirements specify "simple to integrate"           │
│ 2. REST is most widely supported (highest compatibility)│
│ 3. GraphQL adds 20% complexity vs 80% compatibility    │
│ 4. Caching strategy: Redis for popular queries          │
│                                                          │
│ [Learn More] [View Trace] [View Prompt] [Disagree →]   │
└─────────────────────────────────────────────────────────┘
```

**Components**:
- `DecisionTracePanel.tsx` — reasoning display
- `DecisionChain.tsx` — flow of decisions
- `TraceExpandable.tsx` — drill-down details

**Effort**: 2 weeks  
**Benefit**: Trust + learning from agent decisions

#### 4.2: Session Timeline

**Feature**: Jump between checkpoints, see progression

```
Timeline
────────────────────
14:23  ✓ Spec approved        [Resume] [Compare to now]
14:24  ✓ Implementation started
14:35  ⚠ Test failure
14:36  ⏳ Auto-fix running     [View logs]
14:39  ✓ Tests passing
14:40  👤 Code review needed   [Approve] [Request changes]
  ← Current point in workflow

[Previous checkpoints...] [View full history]
```

**Components**:
- `SessionTimeline.tsx` — vertical timeline
- `CheckpointCard.tsx` — individual checkpoint
- `TimelineCompare.tsx` — diff between checkpoints

**Effort**: 1.5 weeks  
**Benefit**: Session recovery and understanding

### Layout Changes (Weeks 6-8)

#### New Dashboard Layout

**Current** (3 pane):
```
[Workspaces/Tickets] | [Main Content] | [Workflow]
```

**New** (4 zone):
```
┌─────────────────────────────────────────────┐
│  Top Bar (Search, Approvals, Settings)      │
├──────────┬──────────────────┬───────────────┤
│ Tickets  │  Main Content    │ Workflow      │
│          │  (PipelineLane,  │ Stages,       │
│          │   Approval Gate, │ Details       │
│          │   Diffs)         │               │
├──────────┴──────────────────┴───────────────┤
│ Terminal Preview / CI Pulse / Agent Trace    │
└─────────────────────────────────────────────┘
```

**Changes**:
- Terminal always visible (bottom)
- CI status in top-right corner
- Pipeline lane view main focus (center)
- Workflow stages sidebar (right)
- Better use of real estate

**Effort**: 1.5 weeks  
**Files**: Update `Dashboard.tsx`, CSS layout changes

---

## Part 4: Architecture Changes

### Database Schema Updates

#### New Tables

```sql
-- CI/Test Integration
CREATE TABLE ci_run_result (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  ticket_id TEXT NOT NULL,
  status TEXT NOT NULL,  -- 'passing', 'failing', 'partial'
  logs_url TEXT,
  failure_summary TEXT,
  created_at TIMESTAMP NOT NULL,
  FOREIGN KEY(ticket_id) REFERENCES work_item(id)
);

-- Parallel Execution
CREATE TABLE worktree_state (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  worktree_path TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL,
  merged_at TIMESTAMP,
  FOREIGN KEY(run_id) REFERENCES run(id)
);

-- Governance/Audit
CREATE TABLE approval_rule (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  stage_id TEXT NOT NULL,
  condition_type TEXT NOT NULL,  -- 'always', 'auto_if_low_risk', 'require_on_sensitive'
  approval_required BOOLEAN,
  reviewer_role TEXT,  -- 'gatekeeper', 'code_owner', 'security'
  created_at TIMESTAMP NOT NULL
);

CREATE TABLE audit_log (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  actor TEXT NOT NULL,  -- agent name or user email
  action TEXT NOT NULL,  -- 'approved', 'rejected', 'tool_called', 'policy_violated'
  resource_type TEXT,  -- 'ticket', 'approval', 'mcp_call'
  resource_id TEXT,
  details JSONB,
  created_at TIMESTAMP NOT NULL
);

-- Sessions & Checkpoints
CREATE TABLE session_checkpoint (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  stage_id TEXT NOT NULL,
  checkpoint_name TEXT,
  checkpoint_data JSONB,  -- git state, agent state, etc
  created_at TIMESTAMP NOT NULL
);

-- Agent Tool Restrictions
ALTER TABLE studio_agent ADD COLUMN mcp_allowed_tools TEXT[];  -- JSON list of tool names
```

#### Extended Tables

```sql
ALTER TABLE run ADD COLUMN (
  ci_status TEXT,  -- 'not_run', 'passing', 'failing'
  ci_failure_logs TEXT,
  auto_fix_attempts INT DEFAULT 0,
  worktree_id TEXT REFERENCES worktree_state(id)
);

ALTER TABLE approval ADD COLUMN (
  risk_level TEXT,  -- 'low', 'medium', 'high'
  auto_approved BOOLEAN,
  approval_rule_id TEXT REFERENCES approval_rule(id),
  locd_changed INT  -- lines of code changed
);

ALTER TABLE studio_agent ADD COLUMN (
  mcp_allowed_tools TEXT[],  -- JSON array of allowed tool names
  tool_restrictions_updated_at TIMESTAMP
);
```

### API Endpoints

#### CI Integration
```
POST   /ci/webhook/{workspace_id}        — receive CI results
GET    /tickets/{id}/ci-status           — get CI state for ticket
POST   /tickets/{id}/auto-fix            — trigger manual fix attempt
GET    /workspaces/{id}/ci-dashboard     — governance view
```

#### Parallel Execution
```
POST   /tickets/{id}/run-parallel        — create parallel run
GET    /workspaces/{id}/parallel-runs    — list active parallel
POST   /runs/{id}/merge-worktree         — merge worktree to main
GET    /runs/{id}/conflicts              — detect merge conflicts
```

#### Governance & Approval Rules
```
POST   /approval-rules                   — create approval rule
PUT    /approval-rules/{id}              — update rule
DELETE /approval-rules/{id}              — delete rule
GET    /tickets/{id}/approval-status     — get all required approvals
GET    /audits                           — search audit logs
GET    /governance/dashboard             — compliance metrics
```

#### Sessions & Checkpoints
```
POST   /runs/{id}/checkpoint             — create checkpoint
GET    /runs/{id}/checkpoints            — list checkpoints
POST   /runs/{id}/resume-from/{checkpoint_id}  — resume workflow
GET    /runs/{id}/trace                  — agent decision trace
```

#### Agent Tool Restrictions
```
PUT    /agents/{id}/mcp-restrictions    — set allowed tools
GET    /agents/{id}/validate-mcp-call   — check if tool allowed
POST   /agents/{id}/apply-template      — apply preset (spec/implement/review)
```

### Backend Classes & Services

```python
# models/ci_integration.py
class CIRunResult(SQLModel, table=True):
    id: str = Field(primary_key=True)
    workspace_id: str
    ticket_id: str
    status: str  # 'passing', 'failing', 'partial'
    logs_url: Optional[str]
    failure_summary: Optional[str]
    created_at: datetime

# services/ci_service.py
class CIService:
    async def process_ci_webhook(self, workspace_id: str, payload: dict) -> CIRunResult
    async def detect_failure(self, run_id: str) -> Optional[CIRunResult]
    async def trigger_auto_fix(self, run_id: str, logs: str) -> Run
    async def get_ci_dashboard(self, workspace_id: str) -> dict

# services/parallel_execution.py
class ParallelExecutionService:
    async def create_worktree(self, run_id: str) -> WorktreeState
    async def merge_worktree(self, run_id: str) -> bool
    async def detect_conflicts(self, run_id: str) -> List[str]  # conflicting files
    async def cleanup_worktree(self, worktree_id: str) -> None

# services/approval_rules.py
class ApprovalRulesEngine:
    async def get_rules(self, stage_id: str) -> List[ApprovalRule]
    async def evaluate_rule(self, rule: ApprovalRule, context: dict) -> bool
    async def route_approval(self, ticket_id: str, stage_id: str) -> str  # reviewer
    async def auto_approve(self, approval_id: str) -> None

# services/audit.py
class AuditService:
    async def log_action(self, action: str, resource_type: str, resource_id: str, details: dict) -> AuditLog
    async def search_logs(self, workspace_id: str, filters: dict) -> List[AuditLog]
    async def export_trail(self, workspace_id: str, ticket_id: str) -> bytes  # PDF

# services/mcp_restrictions.py
class MCPRestrictionService:
    async def validate_tool_call(self, agent_id: str, tool_name: str) -> bool
    async def set_restrictions(self, agent_id: str, allowed_tools: List[str]) -> None
    async def apply_template(self, agent_id: str, template: str) -> None  # 'spec', 'implement', etc
```

### Frontend State Management

```typescript
// state/executionStore.ts
type ExecutionStore = {
  currentRun: Run | null
  parallelRuns: Run[]
  ciStatus: Record<string, CIRunResult>
  checkpoints: Checkpoint[]
  agentTrace: AgentDecision[]
  
  setCurrentRun: (run: Run) => void
  addParallelRun: (run: Run) => void
  setCIStatus: (runId: string, status: CIRunResult) => void
  addCheckpoint: (checkpoint: Checkpoint) => void
  updateAgentTrace: (decisions: AgentDecision[]) => void
}

// hooks/useParallelExecution.ts
export function useParallelExecution(workspaceId: string) {
  const [activeRuns, setActiveRuns] = useState<Run[]>([])
  const [queuedRuns, setQueuedRuns] = useState<Run[]>([])
  
  // auto-poll for new runs
  // manage worktree cleanup
  // detect conflicts
}

// hooks/useCIStatus.ts
export function useCIStatus(ticketId: string) {
  const [ciStatus, setCIStatus] = useState<CIRunResult | null>(null)
  const [autoFixing, setAutoFixing] = useState(false)
  
  // poll CI webhook results
  // trigger auto-fix
  // retry counter
}
```

---

## Part 5: Implementation Timeline & Phases

### Phase 1: Shep Parity (Q3 2026)
**Duration**: 12 weeks  
**Team**: 2-3 engineers  
**Features**: CI integration, parallel execution, spec gates

| Week | Feature | Status | Deliverable |
|------|---------|--------|-------------|
| 1-2 | CI Integration backend | In progress | Webhook receiver, polling |
| 2-3 | CI Integration frontend | In progress | Status widget, logs panel |
| 3-4 | CI Auto-fix loop | Planned | Agent trigger, retry logic |
| 3-6 | Parallel execution backend | Planned | Worktree creation, merge, conflict detection |
| 5-6 | Parallel execution frontend | Planned | ParallelFeatureCards, timeline |
| 5-8 | Spec-driven gates backend | Planned | Stage orchestration, approval routing |
| 7-8 | Spec-driven gates frontend | Planned | ApprovalCard for spec review |
| 9-10 | Visual improvements (quick wins) | Planned | Animations, stage markers, terminal preview |
| 11-12 | Integration testing & docs | Planned | E2E tests, user guide, troubleshooting |

**Go-live criteria**:
- ✅ CI integration fully working
- ✅ 3+ agents run in parallel without conflicts
- ✅ Spec gates block implementation on rejection
- ✅ Visual indicators show status clearly
- ✅ E2E tests pass (CI auto-fix, parallel execution, spec approval)
- ✅ User documentation complete

### Phase 2: Enterprise Governance (Q4 2026)
**Duration**: 8 weeks  
**Team**: 2-3 engineers  
**Features**: Multi-stage approval, MCP restrictions, audit logs

| Week | Feature | Status |
|------|---------|--------|
| 1-4 | Approval rules engine | Planned |
| 3-6 | MCP tool restrictions | Planned |
| 5-8 | Audit logs & governance dashboard | Planned |

### Phase 3: Advanced Features (Q4 2026+)
**Duration**: Ongoing  
**Features**: Session persistence, agent tracing, skill marketplace

---

## Part 6: Success Metrics & Rollout

### Metrics to Track

| Metric | Baseline | Target | Measurement |
|--------|----------|--------|-------------|
| **Feature velocity** | 1 feature/week | 3-5 features/week | Time from spec to PR |
| **CI cycle time** | 20 min | 5 min (with auto-fix) | From failure to passing |
| **Approval SLA** | 60 min avg | < 5 min | Time to first approval |
| **Developer satisfaction** | 6/10 | 8.5/10 | NPS/survey |
| **Parallel utilization** | N/A | 60%+ | % time agents run in parallel |
| **Enterprise sales** | 0 | 3+ deals | New enterprise customers |

### Beta & Rollout Strategy

**Phase 1 Rollout** (CI + Parallel + Spec):
1. **Private beta** (2 weeks) — internal users, select customer
2. **Public beta** (2 weeks) — community feedback, bug fixes
3. **GA release** — announce feature parity with Shep

**Phase 2 Rollout** (Governance):
1. **Enterprise beta** (3 weeks) — select enterprise customers
2. **GA release** — announce compliance/governance ready

### Documentation Roadmap

| Doc | Purpose | Owner | Timeline |
|-----|---------|-------|----------|
| **CI Setup Guide** | How to connect CI/webhook | Engineering | Week 4 |
| **Parallel Execution Cookbook** | When/how to use parallel | Engineering | Week 8 |
| **Approval Rules Reference** | Configure approval gates | Engineering | Week 14 |
| **Governance for Compliance** | Meet audit/regulatory needs | Product | Week 16 |
| **Migration Guide** | From Shep/ADHDev | Product | Week 20 |
| **Architecture Deep Dive** | Technical design decisions | Engineering | Week 24 |

---

## Part 7: Risks & Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|-----------|
| **Worktree conflicts** on parallel merges | High | Medium | Comprehensive conflict detection, manual override option |
| **CI webhook timing** (missed results) | Medium | Low | Fallback polling, webhook retry logic, tests |
| **Approval rules complexity** overwhelming users | Medium | Medium | Preset templates, clear UI, good documentation |
| **Performance degradation** with 3+ agents | High | Low | Profile early, optimize worktree cleanup, monitoring |
| **Governance feature scope creep** | Medium | High | Strict Phase 2 boundaries, defer advanced features |
| **Breaking changes** to existing workflows | High | Low | Extensive migration testing, rollback plan |

**Mitigation Plan**:
- ✅ Feature flagging (all Phase 1/2 features behind flags)
- ✅ Extensive E2E test coverage before GA
- ✅ Staged rollout (beta → limited → full)
- ✅ Rollback procedure documented
- ✅ Customer success team trained pre-launch

---

## Part 8: Dependencies & Prerequisites

### External Dependencies
- **CI providers**: GitHub Actions, GitLab CI, or webhook API (configurable)
- **MCP ecosystem**: Continue to evolve alongside Claude ecosystem
- **Git**: Worktree support (available in Git 2.15+)

### Internal Dependencies
- Agent SDK: Updated to support parallel runs, session state
- Database: SQLite must support JSONB operations (SQLite 3.38+)
- Frontend: React 18+, TypeScript, React Query

### Team & Staffing

**Recommended allocation**:
- **Phase 1**: 2-3 engineers (12 weeks)
  - 1 backend (CI, parallel execution, DB)
  - 1 frontend (UI/UX, dashboard)
  - 0.5 product/QA (spec, test planning)

- **Phase 2**: 2 engineers (8 weeks)
  - 1 backend (governance, audit)
  - 1 frontend (dashboard, rules UI)

- **Phase 3**: 1-2 engineers (ongoing)
  - Features as prioritized

---

## Part 9: Success Criteria & Launch Checklist

### Phase 1 Go-Live Checklist
- [ ] CI webhook receiving results (> 99.5% uptime)
- [ ] Auto-fix agent triggering and succeeding > 80% of time
- [ ] Parallel execution: 3+ agents without merge conflicts
- [ ] Spec gates: blocking/allowing implementation based on approval
- [ ] Dashboard: CI status, parallel runs, stage progress visible
- [ ] E2E tests: all Phase 1 workflows tested
- [ ] Documentation: setup guides for CI, parallel execution, spec gates
- [ ] Performance: no degradation vs. baseline
- [ ] Security: audit logs for compliance
- [ ] User feedback: 3+ customers tested, 8+/10 satisfaction

### Phase 2 Go-Live Checklist
- [ ] Approval rules engine: > 95% accuracy on rule evaluation
- [ ] MCP restrictions: tools properly restricted per agent
- [ ] Governance dashboard: audit logs searchable, metrics accurate
- [ ] Export: PDF/CSV compliance exports working
- [ ] Enterprise features: SOC 2 readiness (if applicable)
- [ ] Documentation: governance setup, compliance guide
- [ ] Certification: audit log integrity verified
- [ ] Sales enablement: enterprise sales team trained
- [ ] Customer feedback: enterprise beta customers satisfied

---

## Part 10: Resource & Budget Estimates

### Engineering Effort
- **Phase 1**: 500-600 engineering hours (12 weeks)
- **Phase 2**: 300-350 engineering hours (8 weeks)
- **Phase 3**: TBD (ongoing)

### Infrastructure
- **Database**: Current SQLite sufficient (consider PostgreSQL for scale)
- **CI/CD**: GitHub Actions (existing)
- **Monitoring**: Add application tracing for agent execution

### Marketing & Sales
- **Messaging**: "From multi-agent to multi-feature SDLC"
- **Case study**: Feature development time reduction (3-5x)
- **Comparison**: vs. Shep (parity), vs. Port.io (compliance + ease)

---

## Part 11: Next Steps

### Immediate (This Week)
1. ✅ Socialize this plan with team
2. ✅ Get feedback on priorities & timeline
3. ✅ Determine staffing allocation
4. ✅ Create detailed task breakdown (Jira/GitHub Issues)

### Week 1-2
1. Design CI webhook schema & API
2. Design worktree isolation approach
3. Create mockups for PipelineLaneView
4. Start CI integration backend implementation

### Ongoing
- Track metrics weekly
- Communicate progress to stakeholders
- Adjust timeline based on learnings
- Maintain documentation as features ship

---

## Appendix A: Competitive Feature Matrix

| Feature | Loregarden (Now) | Loregarden (Phase 1) | Shep | ADHDev | Port.io | GitHub Copilot |
|---------|------------------|----------------------|------|--------|---------|----------------|
| **Parallel Execution** | ❌ | ✅ (3+) | ✅ (10) | ⚠️ (session) | ✅ | ✅ (8) |
| **CI Integration** | ❌ | ✅ (auto-fix) | ✅ | ❌ | ✅ | ⚠️ |
| **Spec Gates** | ❌ | ✅ | ✅ | ❌ | ⚠️ | ❌ |
| **Approval Workflows** | ⚠️ (basic) | ✅ (multi-stage) | ⚠️ (PRs only) | ❌ | ✅ (risk-tiered) | ✅ |
| **Governance/Audit** | ❌ | ✅ (Phase 2) | ❌ | ❌ | ✅ | ⚠️ |
| **MCP Support** | ✅ | ✅ (with restrictions) | ❌ | ✅ (35+) | ✅ | ✅ |
| **Local-First** | ✅ | ✅ | ✅ | ✅ | ❌ (cloud) | ❌ (cloud) |
| **Visual Orchestration** | ⚠️ | ✅ (lane view) | ⚠️ (graph) | ⚠️ | ✅ | ✅ |

---

## Appendix B: Visual Design System

### Color Palette (Dark Theme)
```css
--bg0: #08090b  /* Main background */
--bg1: #0d0e11  /* Secondary background */
--bg2: #131519  /* Tertiary background */
--bg3: #181b20  /* Hover background */

--tx:  #e7e9ec  /* Primary text */
--txm: #969ca6  /* Medium text */
--txl: #5c626c  /* Light text */

--ac:  #7d6cff  /* Accent (primary) */
--ac2: #9d90ff  /* Accent (light) */

--blue: #4493f8  /* Info state */
--grn:  #3fb950  /* Success state */
--red:  #f0603f  /* Error state */
--amb:  #d6a02b  /* Warning state */
```

### Stage Status Colors
- **Complete (green)**: #3fb950
- **Running (blue)**: #4493f8
- **Waiting (amber)**: #d6a02b
- **Failed (red)**: #f0603f
- **Blocked (gray)**: #5c626c

### Typography
- **Display (branding)**: Space Grotesk, 600-700 weight
- **UI (body, buttons)**: IBM Plex Sans, 400-600 weight
- **Monospace (code, logs)**: JetBrains Mono, 400-500 weight

### Components & Patterns

#### Status Indicator Patterns
```tsx
// Running state
<StageIndicator status="running" animate={true} />
// Shows pulsing blue circle

// Waiting for approval
<StageIndicator status="pending" animate={true} pulse="approval" />
// Shows pulsing amber ring

// Failed state
<StageIndicator status="failed" animate={false} glow={true} />
// Shows glowing red circle
```

#### Approval Gate Pattern
```tsx
<ApprovalGate
  stage="Code Review"
  status="pending"
  changes={{ files: 5, lines: 234 }}
  onApprove={() => {}}
  onRequest={() => {}}
/>
// Shows summary + action buttons
```

#### Timeline Pattern
```tsx
<SessionTimeline
  checkpoints={[
    { timestamp: 1, label: "Spec approved", status: "complete" },
    { timestamp: 2, label: "Implementation", status: "running" },
    { timestamp: 3, label: "Tests", status: "pending" },
  ]}
  onResume={(checkpoint) => {}}
/>
// Vertical timeline with resume buttons
```

---

## Appendix C: User Documentation Outline

### Getting Started
1. **CI Integration Setup**
   - GitHub Actions webhook configuration
   - GitLab CI configuration
   - Generic webhook format
   - Troubleshooting CI connections

2. **Parallel Feature Development**
   - When to use parallel execution
   - Worktree conflicts and resolution
   - Merge strategy for parallel features
   - Performance tips

3. **Spec-Driven Workflows**
   - Planning phase overview
   - Spec approval process
   - Feedback loops
   - Best practices

### Advanced Topics
4. **Approval Workflows & Governance**
   - Setting up approval rules
   - Risk-tiering strategies
   - Code owner integration
   - Audit trails for compliance

5. **Agent Configuration**
   - Restricting MCP tools
   - Tool templates (spec/implement/review)
   - Custom tool sets
   - Security considerations

6. **Session Management**
   - Creating checkpoints
   - Resuming from checkpoints
   - Comparing versions
   - Full session history

### Troubleshooting & FAQ
7. **Common Issues**
   - CI webhook not receiving results
   - Worktree merge conflicts
   - Approval gate blocking unexpectedly
   - Agent auto-fix failures
   - Performance with parallel agents

---

## Appendix D: Testing Strategy

### Unit Tests
- Approval rules engine (all rule types)
- Worktree isolation logic
- CI failure detection
- MCP tool validation
- Audit log storage/search

### Integration Tests
- CI webhook → auto-fix flow
- Parallel execution with conflict detection
- Spec approval → implementation handoff
- Approval rules applied to real tickets
- Governance dashboard metrics

### E2E Tests (Playwright)
1. **CI Auto-Fix Flow**
   - Create feature with failing tests
   - Detect failure via webhook
   - Auto-fix agent runs
   - Tests pass
   - Approval proceeds

2. **Parallel Execution**
   - Create 3 parallel features
   - All run simultaneously
   - No merge conflicts
   - All merge successfully

3. **Spec-Driven Approval**
   - Create feature with spec stage enabled
   - Spec agent writes requirements
   - Approve spec
   - Implementation proceeds
   - Reject spec
   - Planner re-runs with feedback

4. **Approval Rules**
   - Low-risk change (< 50 LOC) auto-approves
   - High-risk change (> 500 LOC) requires approval
   - Security-sensitive file triggers security review

5. **MCP Restrictions**
   - Spec agent cannot call build tools
   - Implementer cannot call deployment tools
   - QA agent can only call testing tools

### Load Testing
- 5+ agents running in parallel
- 10+ approval gates processing simultaneously
- 1000+ audit log entries searchable
- Performance: < 200ms response time for all queries

---

## Appendix E: Glossary

- **Worktree**: Isolated git working directory for parallel features
- **CI**: Continuous Integration (tests, linting, builds)
- **Stage**: Step in the TDD pipeline (plan, spec, implement, test, review, etc)
- **Approval Gate**: Human decision point in the workflow
- **Checkpoint**: Saved state of a workflow run
- **MCP**: Model Context Protocol (tool integration layer)
- **Spec Stage**: Planning phase producing requirements/architecture doc
- **Auto-fix**: Automatic agent retry on test failure
- **Risk-tiering**: Approval rules based on change magnitude/sensitivity
- **Worktree Conflict**: Files modified in multiple parallel features

---

**Document Owner**: Loregarden Product Team  
**Last Updated**: 2026-07-06  
**Next Review**: 2026-08-06
