"""SQLModel table definitions (the persisted schema)."""

from datetime import datetime
from uuid import uuid4

from loregarden.models.domain.enums import (
    ApprovalKind,
    ApprovalStatus,
    CycleStatus,
    EventType,
    OrchestrationDriver,
    OrchestrationRunStatus,
    QueueOperationType,
    RunStatus,
    StageStatus,
    TicketState,
    TicketStudioSessionStatus,
    WorkItemType,
    _str_enum_column,
    utcnow,
)
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
    permission_allowlist_json: str = "[]"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class WorkflowInstance(SQLModel, table=True):
    __tablename__ = "workflow_instances"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    template_id: str = Field(foreign_key="workflow_templates.id")
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
    skill_name: str = ""
    stage_key: str = ""
    status: RunStatus = Field(default=RunStatus.QUEUED)
    command: str = ""
    stdout: str = ""
    stderr: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Artifact(SQLModel, table=True):
    __tablename__ = "artifacts"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    run_id: str | None = Field(default=None, foreign_key="agent_runs.id")
    kind: str = Field(index=True)  # diff | log | test | context
    title: str = ""
    content_json: str = "{}"
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
    timeout: int = Field(default=600, ge=30)
    default_skill: str = ""
    mcp_enabled: bool = Field(default=True)
    mcp_tools_json: str = "[]"
    gate_checks_json: str = "[]"
    handoff_checks_json: str = "[]"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


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


class TicketStudioSession(SQLModel, table=True):
    __tablename__ = "ticket_studio_sessions"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    title: str = ""
    brief: str = ""
    parent_ticket_id: str | None = Field(default=None, foreign_key="tickets.id")
    status: TicketStudioSessionStatus = Field(
        default=TicketStudioSessionStatus.DRAFT,
        sa_column=_str_enum_column(TicketStudioSessionStatus, TicketStudioSessionStatus.DRAFT, index=True),
    )
    draft_json: str = "[]"
    summary: str = ""
    clarifying_questions_json: str = "[]"
    clarifying_answers_json: str = "[]"
    runtime_json: str = "{}"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class TicketStudioMessage(SQLModel, table=True):
    __tablename__ = "ticket_studio_messages"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    session_id: str = Field(foreign_key="ticket_studio_sessions.id", index=True)
    role: str = Field(index=True)  # user | assistant | system
    content: str = ""
    created_at: datetime = Field(default_factory=utcnow)
