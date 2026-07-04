from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from sqlmodel import Field, SQLModel
from sqlalchemy import Column, Enum as SAEnum


def _str_enum_column(enum_cls: type[Enum], default: Enum, *, index: bool = False) -> Column:
    return Column(
        SAEnum(enum_cls, values_callable=lambda choices: [c.value for c in choices]),
        nullable=False,
        default=default.value,
        index=index,
    )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TicketState(str, Enum):
    BACKLOG = "backlog"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    WONT_DO = "wont_do"


class WorkItemType(str, Enum):
    """Hierarchy types — matches lllm-charge convention."""
    MILESTONE = "milestone"
    FEATURE = "feature"
    CAPABILITY = "capability"
    TASK = "task"
    BUG = "bug"


VALID_HIERARCHY: dict[WorkItemType, list[WorkItemType]] = {
    WorkItemType.MILESTONE: [WorkItemType.FEATURE],
    WorkItemType.FEATURE: [WorkItemType.CAPABILITY],
    WorkItemType.CAPABILITY: [WorkItemType.TASK, WorkItemType.BUG],
    WorkItemType.TASK: [],
    WorkItemType.BUG: [],
}


class CycleStatus(str, Enum):
    PLANNED = "planned"
    ACTIVE = "active"
    COMPLETED = "completed"


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    AWAITING = "awaiting"
    DONE = "done"
    WONT_DO = "wont_do"


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OrchestrationDriver(str, Enum):
    BUILTIN_AUTOPILOT = "builtin_autopilot"
    EXTERNAL_MCP = "external_mcp"
    MANUAL_STAGE = "manual_stage"


class OrchestrationRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class EventType(str, Enum):
    TICKET_CREATED = "TicketCreated"
    TICKET_STATE_CHANGED = "TicketStateChanged"
    WORKFLOW_STARTED = "WorkflowStarted"
    STAGE_STARTED = "StageStarted"
    STAGE_COMPLETED = "StageCompleted"
    AGENT_RUN_STARTED = "AgentRunStarted"
    AGENT_RUN_COMPLETED = "AgentRunCompleted"
    ORCHESTRATION_RUN_STARTED = "OrchestrationRunStarted"
    ORCHESTRATION_RUN_COMPLETED = "OrchestrationRunCompleted"
    ARTIFACT_CREATED = "ArtifactCreated"
    APPROVAL_REQUESTED = "ApprovalRequested"
    APPROVAL_RESOLVED = "ApprovalResolved"


class Workspace(SQLModel, table=True):
    __tablename__ = "workspaces"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    slug: str = Field(index=True, unique=True)
    name: str
    repo_path: str = ""
    workflow_template_id: Optional[str] = Field(default=None, foreign_key="workflow_templates.id")
    workflow_override_json: str = "{}"
    orchestration_profile_slug: str = ""
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
    parent_ticket_id: Optional[str] = Field(default=None, foreign_key="tickets.id", index=True)
    cycle_id: Optional[str] = Field(default=None, foreign_key="cycles.id", index=True)
    acceptance_criteria_json: str = "[]"
    workflow_stage_key: str = ""
    workflow_stage_status: StageStatus = Field(default=StageStatus.PENDING)
    revision: int = Field(default=0)
    last_updated_by: str = ""
    next_agent: str = ""
    next_status: str = "Proceed"
    blocking_issues: str = ""
    state_locked: bool = Field(default=False)
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
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)


class AgentRun(SQLModel, table=True):
    __tablename__ = "agent_runs"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    run_code: str = Field(index=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    orchestration_run_id: Optional[str] = Field(
        default=None, foreign_key="orchestration_runs.id", index=True
    )
    agent_id: str
    skill_name: str = ""
    stage_key: str = ""
    status: RunStatus = Field(default=RunStatus.QUEUED)
    command: str = ""
    stdout: str = ""
    stderr: str = ""
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)


class Artifact(SQLModel, table=True):
    __tablename__ = "artifacts"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    run_id: Optional[str] = Field(default=None, foreign_key="agent_runs.id")
    kind: str = Field(index=True)  # diff | log | test | context
    title: str = ""
    content_json: str = "{}"
    created_at: datetime = Field(default_factory=utcnow)


class Approval(SQLModel, table=True):
    __tablename__ = "approvals"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    title: str
    level: str = "medium"
    stage_key: str = ""
    impact: str = ""
    status: ApprovalStatus = Field(default=ApprovalStatus.PENDING)
    created_at: datetime = Field(default_factory=utcnow)
    resolved_at: Optional[datetime] = None


class DomainEvent(SQLModel, table=True):
    __tablename__ = "domain_events"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    type: EventType
    workspace_id: Optional[str] = Field(default=None, foreign_key="workspaces.id")
    ticket_id: Optional[str] = Field(default=None, foreign_key="tickets.id")
    run_id: Optional[str] = Field(default=None, foreign_key="agent_runs.id")
    artifact_id: Optional[str] = Field(default=None, foreign_key="artifacts.id")
    payload_json: str = "{}"
    created_at: datetime = Field(default_factory=utcnow)


# --- API DTOs ---


class WorkflowStageDef(SQLModel):
    key: str
    name: str
    agent_id: str = ""
    skill_name: str = ""
    optional: bool = False
    order: int = 0


class WorkflowStageView(SQLModel):
    key: str
    name: str
    status: StageStatus
    agent_id: str = ""
    skill_name: str = ""
    optional: bool = False
    note: str = ""


class TicketSummary(SQLModel):
    id: str
    external_id: str
    title: str
    state: TicketState
    priority: int
    workspace_slug: str
    workflow_stage_key: str
    workflow_stage_status: StageStatus
    workflow_stage_name: str = ""
    branch: str = ""
    run_code: str = ""
    work_item_type: WorkItemType = WorkItemType.TASK
    parent_ticket_id: Optional[str] = None
    cycle_id: Optional[str] = None
    cycle_name: str = ""
    milestone: str = ""
    child_count: int = 0


class TicketTreeNode(SQLModel):
    id: str
    external_id: str
    title: str
    state: TicketState
    priority: int
    work_item_type: WorkItemType
    workflow_stage_name: str = ""
    child_count: int = 0
    children: list["TicketTreeNode"] = []


class TicketDetail(TicketSummary):
    description: str
    acceptance_criteria: list[str]
    revision: int
    last_updated_by: str
    next_agent: str
    next_status: str
    blocking_issues: str
    state_locked: bool = False
    stages: list[WorkflowStageView]
    artifacts: dict[str, Any]


class WorkspaceSummary(SQLModel):
    id: str
    slug: str
    name: str
    ticket_count: int
    blocked_count: int
    workflow_template_slug: str = ""


class WorkspaceCreate(SQLModel):
    slug: str
    name: str
    workflow_template_slug: str = "loregarden-tdd"
    repo_path: str = "."


class WorkspaceTemplateUpdate(SQLModel):
    workflow_template_slug: str


class ApprovalView(SQLModel):
    id: str
    title: str
    level: str
    workspace_slug: str
    stage_key: str
    stage_name: str
    impact: str
    ticket_id: str
    ticket_external_id: str


class EventView(SQLModel):
    id: str
    type: EventType
    ticket_id: Optional[str]
    workspace_id: Optional[str]
    payload: dict[str, Any]
    created_at: datetime


class StartRunRequest(SQLModel):
    stage_key: Optional[str] = None
    manual: bool = False


class StartOrchestrationRequest(SQLModel):
    driver: Optional[OrchestrationDriver] = None
    max_stages: Optional[int] = None


class CompleteStageRequest(SQLModel):
    stage_key: str
    next_agent: str = ""
    blocking_issues: str = ""


class StartStageRequest(SQLModel):
    stage_key: str
    agent_id: str = ""


class BlockTicketRequest(SQLModel):
    stage_key: str = ""
    message: str


class SkipStageRequest(SQLModel):
    stage_key: str
    reason: str = ""


class AttachArtifactRequest(SQLModel):
    kind: str = "log"
    title: str = ""
    content: dict[str, Any] = {}


class RequestApprovalRequest(SQLModel):
    stage_key: str
    title: str = ""
    impact: str = ""
    level: str = "medium"


class CompleteOrchestrationRequest(SQLModel):
    status: OrchestrationRunStatus = OrchestrationRunStatus.SUCCEEDED
    message: str = ""


class OrchestrationRunView(SQLModel):
    id: str
    run_code: str
    ticket_id: str
    driver: OrchestrationDriver
    profile_slug: str
    status: OrchestrationRunStatus
    current_stage_key: str
    error_message: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class OrchestrationProfileView(SQLModel):
    slug: str
    name: str
    driver: OrchestrationDriver
    workflow_template: str
    orchestrator_skill: str = ""
    gates_enabled: bool = False
    max_stages_per_run: int = 0


class AdvanceStageRequest(SQLModel):
  # backend decides transition; optional hint only for logging
    reason: str = ""


class UpdateTicketRequest(SQLModel):
    state: Optional[TicketState] = None
    workflow_stage_key: Optional[str] = None
    workflow_stage_status: Optional[StageStatus] = None
    stage_key: Optional[str] = None
    stage_status: Optional[StageStatus] = None
    stage_updates: Optional[dict[str, StageStatus]] = None
    auto_state: Optional[bool] = None


class ApprovalAction(SQLModel):
    action: str  # approve | reject


TicketTreeNode.model_rebuild()
