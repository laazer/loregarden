"""SQLModel table definitions (the persisted schema)."""

import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from loregarden.models.domain.enums import (
    DEFAULT_COMPATIBILITY_POSTURE,
    ApprovalKind,
    ApprovalStatus,
    AutoFixStatus,
    CIStatus,
    CycleStatus,
    EventType,
    OrchestrationDriver,
    OrchestrationRunStatus,
    QueueOperationType,
    QueuePosition,
    RunStatus,
    StageStatus,
    TicketState,
    TicketStudioSessionStatus,
    WorkItemType,
    WorktreeState,
    _str_enum_column,
    utcnow,
)
from pydantic import model_validator
from sqlmodel import Field, SQLModel


class Workspace(SQLModel, table=True):
    __tablename__ = "workspaces"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    slug: str = Field(index=True, unique=True)
    name: str
    repo_path: str = ""
    workflow_template_id: str | None = Field(default=None, foreign_key="workflow_templates.id")
    workflow_override_json: str = "{}"
    orchestration_profile_slug: str = ""
    cli_adapter: str = ""
    claude_model: str = ""
    cursor_model: str = ""
    lmstudio_base_url: str = ""
    lmstudio_model: str = ""
    permission_allowlist_json: str = "[]"
    # Workspace-wide default; a ticket or any of its ancestors may override it.
    compatibility_posture: str = DEFAULT_COMPATIBILITY_POSTURE.value
    created_at: datetime = Field(default_factory=utcnow)


class Cycle(SQLModel, table=True):
    __tablename__ = "cycles"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    name: str
    status: CycleStatus = Field(
        default=CycleStatus.PLANNED,
        sa_column=_str_enum_column(CycleStatus, CycleStatus.PLANNED),
    )
    goal: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class WorkflowTemplate(SQLModel, table=True):
    __tablename__ = "workflow_templates"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    slug: str = Field(index=True, unique=True)
    name: str
    description: str = ""
    # JSON list of stage definitions
    stages_json: str = "[]"
    transitions_json: str = "[]"
    source_path: str = ""
    # Current head version; every edit/publish bumps this and appends a
    # WorkflowTemplateVersion snapshot.
    version: int = Field(default=1)
    # True for seeded (YAML-origin) templates; a provenance badge, not an edit gate.
    built_in: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow)


class WorkflowTemplateVersion(SQLModel, table=True):
    """Append-only snapshot of a workflow template at each edit/publish. History
    is never mutated; a restore appends a new version equal to an old snapshot."""

    __tablename__ = "workflow_template_versions"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    template_id: str = Field(foreign_key="workflow_templates.id", index=True)
    version: int
    snapshot_json: str = "{}"
    created_by: str = ""  # seed | studio-ui | api
    change_note: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class Ticket(SQLModel, table=True):
    __tablename__ = "tickets"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    external_id: str = Field(index=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    title: str
    description: str = ""
    state: TicketState = Field(default=TicketState.BACKLOG)
    priority: int = Field(default=3, ge=1, le=3)
    branch: str = ""
    milestone: str = ""
    work_item_type: WorkItemType = Field(
        default=WorkItemType.TASK,
        sa_column=_str_enum_column(WorkItemType, WorkItemType.TASK, index=True),
    )
    parent_ticket_id: str | None = Field(default=None, foreign_key="tickets.id", index=True)
    cycle_id: str | None = Field(default=None, foreign_key="cycles.id", index=True)
    acceptance_criteria_json: str = "[]"
    workflow_stage_key: str = ""
    workflow_stage_status: StageStatus = Field(default=StageStatus.PENDING)
    revision: int = Field(default=0)
    last_updated_by: str = ""
    next_agent: str = ""
    next_status: str = "Proceed"
    blocking_issues: str = ""
    state_locked: bool = Field(default=False)
    workflow_disabled: bool = Field(default=False)
    triage_runtime_json: str = "{}"
    orchestration_runtime_json: str = "{}"
    permission_allowlist_json: str = "[]"
    # Blank = inherit from the nearest ancestor that sets one, else the workspace.
    # Milestones are tickets, so this one column covers milestone- and ticket-level.
    compatibility_posture: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class WorkflowInstance(SQLModel, table=True):
    __tablename__ = "workflow_instances"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    template_id: str = Field(foreign_key="workflow_templates.id")
    # Template version this ticket is pinned to. Stage definitions resolve from
    # this version's snapshot, so editing the template does not mutate an
    # in-flight ticket. Null on rows that predate versioning (fall back to head).
    template_version: int | None = Field(default=None)
    current_stage_key: str = ""
    stages_json: str = "[]"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class OrchestrationRun(SQLModel, table=True):
    __tablename__ = "orchestration_runs"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    run_code: str = Field(index=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    driver: OrchestrationDriver = Field(
        default=OrchestrationDriver.BUILTIN_AUTOPILOT,
        sa_column=_str_enum_column(
            OrchestrationDriver, OrchestrationDriver.BUILTIN_AUTOPILOT, index=True
        ),
    )
    profile_slug: str = ""
    status: OrchestrationRunStatus = Field(
        default=OrchestrationRunStatus.QUEUED,
        sa_column=_str_enum_column(OrchestrationRunStatus, OrchestrationRunStatus.QUEUED),
    )
    current_stage_key: str = ""
    error_message: str = ""
    auto_approve: bool = Field(default=False)
    stop_at_stage_key: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)


class AgentRun(SQLModel, table=True):
    __tablename__ = "agent_runs"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    run_code: str = Field(index=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    orchestration_run_id: str | None = Field(
        default=None, foreign_key="orchestration_runs.id", index=True
    )
    agent_id: str
    # Version of the agent definition this run executed under (null pre-versioning).
    agent_version: int | None = Field(default=None)
    skill_name: str = ""
    stage_key: str = ""
    status: RunStatus = Field(default=RunStatus.QUEUED)
    command: str = ""
    # Paths this run left dirty, so its commit can be scoped to its own work
    # instead of sweeping unrelated edits out of the workspace.
    changed_paths_json: str = "[]"
    stdout: str = ""
    stderr: str = ""
    auto_approve: bool = Field(default=False)
    timeout_override_seconds: int | None = Field(default=None)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Artifact(SQLModel, table=True):
    __tablename__ = "artifacts"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    run_id: str | None = Field(default=None, foreign_key="agent_runs.id")
    kind: str = Field(index=True)  # diff | log | test | context | evidence
    title: str = ""
    content_json: str = "{}"
    # What an `evidence` artifact proves, and the commit it proves it against.
    # Evidence regenerated before the last source edit is stale, so the sha is
    # what lets a verifier tell proof from a leftover.
    evidence_kind: str = ""
    commit_sha: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class Approval(SQLModel, table=True):
    __tablename__ = "approvals"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    run_id: str | None = Field(default=None, foreign_key="agent_runs.id", index=True)
    kind: ApprovalKind = Field(
        default=ApprovalKind.WORKFLOW_GATE,
        sa_column=_str_enum_column(ApprovalKind, ApprovalKind.WORKFLOW_GATE),
    )
    title: str
    level: str = "medium"
    stage_key: str = ""
    impact: str = ""
    checklist_json: str = "[]"
    permission_request_id: str = ""
    tool_name: str = ""
    tool_input_json: str = "{}"
    cli_adapter: str = ""
    cli_session_id: str = ""
    response_json: str = "{}"
    status: ApprovalStatus = Field(default=ApprovalStatus.PENDING)
    created_at: datetime = Field(default_factory=utcnow)
    resolved_at: datetime | None = None


class TriageMessage(SQLModel, table=True):
    __tablename__ = "triage_messages"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    role: str = Field(index=True)  # user | assistant | system
    content: str = ""
    run_id: str | None = Field(default=None, foreign_key="agent_runs.id")
    created_at: datetime = Field(default_factory=utcnow)


class DomainEvent(SQLModel, table=True):
    __tablename__ = "domain_events"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    type: EventType
    workspace_id: str | None = Field(default=None, foreign_key="workspaces.id")
    ticket_id: str | None = Field(default=None, foreign_key="tickets.id")
    run_id: str | None = Field(default=None, foreign_key="agent_runs.id")
    artifact_id: str | None = Field(default=None, foreign_key="artifacts.id")
    payload_json: str = "{}"
    created_at: datetime = Field(default_factory=utcnow)


class StudioAgent(SQLModel, table=True):
    __tablename__ = "studio_agents"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    slug: str = Field(index=True, unique=True)
    name: str
    description: str = ""
    role_body: str = ""
    adapter: str = "claude"
    default_model: str = ""
    timeout: int = Field(default=600, ge=30)
    default_skill: str = ""
    mcp_enabled: bool = Field(default=True)
    mcp_tools_json: str = "[]"
    gate_checks_json: str = "[]"
    handoff_checks_json: str = "[]"
    # Current head version; every edit bumps this and appends a StudioAgentVersion.
    version: int = Field(default=1)
    # True for seeded (registry-origin) agents; a provenance badge, not an edit gate.
    built_in: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class StudioAgentVersion(SQLModel, table=True):
    """Append-only snapshot of a studio agent at each edit. History is never
    mutated; a restore appends a new version equal to an old snapshot."""

    __tablename__ = "studio_agent_versions"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    agent_id: str = Field(foreign_key="studio_agents.id", index=True)
    version: int
    snapshot_json: str = "{}"
    created_by: str = ""  # seed | studio-ui | api
    change_note: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class StudioWorkflow(SQLModel, table=True):
    __tablename__ = "studio_workflows"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    slug: str = Field(index=True, unique=True)
    name: str
    description: str = ""
    stages_json: str = "[]"
    transitions_json: str = "[]"
    published_template_id: str | None = Field(default=None, foreign_key="workflow_templates.id")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class QueueOperation(SQLModel, table=True):
    """Tracks queue operations for diff review and approval."""

    __tablename__ = "queue_operations"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    operation_type: QueueOperationType = Field(
        default=QueueOperationType.BULK_CANCEL,
        sa_column=_str_enum_column(QueueOperationType, QueueOperationType.BULK_CANCEL),
    )
    description: str = ""
    before_state_json: str
    after_state_json: str
    diff_json: str = ""
    affected_run_ids: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    created_by: str = ""
    approved: bool = Field(default=False, index=True)
    approved_at: datetime | None = None
    approved_by: str = ""
    executed: bool = Field(default=False, index=True)


class QueueOperationComment(SQLModel, table=True):
    """Comments on queue operations (GitHub-style inline review)."""

    __tablename__ = "queue_operation_comments"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    operation_id: str = Field(foreign_key="queue_operations.id", index=True)
    line_number: int | None = None
    run_id: str | None = None
    content: str
    resolved: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow)
    created_by: str = ""
    updated_at: datetime = Field(default_factory=utcnow)


class RunOutputReview(SQLModel, table=True):
    """Line-by-line review of run output (stdout/stderr)."""

    __tablename__ = "run_output_reviews"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    run_id: str = Field(foreign_key="agent_runs.id", index=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    output_type: str = Field(index=True)
    output_content: str
    comments_json: str = ""
    approved: bool = Field(default=False)
    approved_by: str = ""
    approved_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class TicketDiffComment(SQLModel, table=True):
    """Inline code review comment anchored to a line in a ticket's git diff."""

    __tablename__ = "ticket_diff_comments"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    file_path: str = Field(index=True)
    line_index: int = Field(index=True)
    line_kind: str = Field(default="c")
    content: str
    resolved: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow)
    created_by: str = ""
    updated_at: datetime = Field(default_factory=utcnow)


class BranchDiffComment(SQLModel, table=True):
    """Inline code review comment anchored to a line in a branch diff."""

    __tablename__ = "branch_diff_comments"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    branch: str = Field(index=True)
    file_path: str = Field(index=True)
    line_index: int = Field(index=True)
    line_kind: str = Field(default="c")
    content: str
    resolved: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow)
    created_by: str = ""
    updated_at: datetime = Field(default_factory=utcnow)


class BranchTriageMessage(SQLModel, table=True):
    """Triage chat message scoped to a workspace git branch."""

    __tablename__ = "branch_triage_messages"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    branch: str = Field(index=True)
    role: str = Field(index=True)  # user | assistant | system
    content: str = ""
    # pending | complete | failed. An assistant row is written as `pending` before the
    # turn runs and settled by the background worker, so an interrupted turn is
    # recoverable from the database rather than lost with the request.
    status: str = Field(default="complete", index=True)
    created_at: datetime = Field(default_factory=utcnow)


class TicketStudioSession(SQLModel, table=True):
    __tablename__ = "ticket_studio_sessions"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    title: str = ""
    brief: str = ""
    parent_ticket_id: str | None = Field(default=None, foreign_key="tickets.id")
    status: TicketStudioSessionStatus = Field(
        default=TicketStudioSessionStatus.DRAFT,
        sa_column=_str_enum_column(
            TicketStudioSessionStatus, TicketStudioSessionStatus.DRAFT, index=True
        ),
    )
    draft_json: str = "[]"
    summary: str = ""
    clarifying_questions_json: str = "[]"
    clarifying_answers_json: str = "[]"
    runtime_json: str = "{}"
    is_preview: bool = Field(default=False)
    imported_tickets_json: str = "[]"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class TicketStudioMessage(SQLModel, table=True):
    __tablename__ = "ticket_studio_messages"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    session_id: str = Field(foreign_key="ticket_studio_sessions.id", index=True)
    role: str = Field(index=True)  # user | assistant | system
    content: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class CIRunResult(SQLModel, table=True):
    __tablename__ = "ci_run_results"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    status: CIStatus = Field(
        default=CIStatus.PENDING,
        sa_column=_str_enum_column(CIStatus, CIStatus.PENDING, index=True),
    )
    provider: str = ""
    external_run_id: str | None = None
    logs_url: str | None = None
    failure_summary: str | None = None
    full_logs: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class AutoFixAttempt(SQLModel, table=True):
    __tablename__ = "auto_fix_attempts"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    ci_run_result_id: str = Field(foreign_key="ci_run_results.id", index=True)
    attempt_number: int = 1
    run_id: str | None = Field(default=None, foreign_key="agent_runs.id")
    status: AutoFixStatus = Field(
        default=AutoFixStatus.PENDING,
        sa_column=_str_enum_column(AutoFixStatus, AutoFixStatus.PENDING, index=True),
    )
    result_summary: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None


class Worktree(SQLModel, table=True):
    __tablename__ = "worktrees"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    agent_run_id: str = Field(foreign_key="agent_runs.id", index=True)
    parent_branch: str = "main"
    worktree_path: str = ""
    state: WorktreeState = Field(
        default=WorktreeState.ACTIVE,
        sa_column=_str_enum_column(WorktreeState, WorktreeState.ACTIVE, index=True),
    )
    merge_base: str | None = None
    has_conflicts: bool = False
    conflict_files_json: str = "[]"
    conflict_summary: str | None = None
    merged_at: datetime | None = None

    @property
    def conflict_files(self) -> list[str]:
        try:
            return json.loads(self.conflict_files_json or "[]")
        except json.JSONDecodeError:
            return []

    @conflict_files.setter
    def conflict_files(self, value: list[str]) -> None:
        self.conflict_files_json = json.dumps(value)

    @model_validator(mode="before")
    @classmethod
    def _coerce_conflict_files(cls, data: Any) -> Any:
        if isinstance(data, dict) and "conflict_files" in data:
            data = dict(data)
            files = data.pop("conflict_files")
            data["conflict_files_json"] = json.dumps(files)
        return data


class ConflictReport(SQLModel, table=True):
    __tablename__ = "conflict_reports"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    worktree_id: str = Field(foreign_key="worktrees.id", index=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    merge_attempt_number: int = 1
    conflict_type: str = "merge_conflict"
    conflicting_files_json: str = "[]"
    conflict_details: str = ""
    resolution_attempted: bool = False
    created_at: datetime = Field(default_factory=utcnow)

    @property
    def conflicting_files(self) -> list[str]:
        try:
            return json.loads(self.conflicting_files_json or "[]")
        except json.JSONDecodeError:
            return []

    @conflicting_files.setter
    def conflicting_files(self, value: list[str]) -> None:
        self.conflicting_files_json = json.dumps(value)

    @model_validator(mode="before")
    @classmethod
    def _coerce_conflicting_files(cls, data: Any) -> Any:
        if isinstance(data, dict) and "conflicting_files" in data:
            data = dict(data)
            files = data.pop("conflicting_files")
            data["conflicting_files_json"] = json.dumps(files)
        return data


class AgentSlot(SQLModel, table=True):
    __tablename__ = "agent_slots"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    slot_number: int = 1
    is_available: bool = True
    current_run_id: str | None = Field(default=None, foreign_key="agent_runs.id")
    assigned_at: datetime | None = None
    released_at: datetime | None = None


class QueuedRun(SQLModel, table=True):
    __tablename__ = "queued_runs"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    run_id: str = Field(foreign_key="agent_runs.id", index=True)
    position: int = 0
    status: QueuePosition = Field(
        default=QueuePosition.QUEUED,
        sa_column=_str_enum_column(QueuePosition, QueuePosition.QUEUED, index=True),
    )
    retry_count: int = 0
    max_retries: int = 3
    estimated_start_at: datetime | None = None
    promoted_at: datetime | None = None
    started_at: datetime | None = None
    failure_reason: str = ""
    last_failed_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)


class QueueSnapshot(SQLModel, table=True):
    __tablename__ = "queue_snapshots"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    name: str = ""
    description: str = ""
    queue_state_json: str = "[]"
    stats_json: str = "{}"
    tags: str = ""
    created_by: str = ""
    created_at: datetime = Field(default_factory=utcnow)
