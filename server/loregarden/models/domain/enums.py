"""Enums and shared helpers for the domain models."""

from datetime import datetime, timezone
from enum import Enum, StrEnum

from sqlalchemy import Column
from sqlalchemy import Enum as SAEnum


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
    WorkItemType.MILESTONE: [WorkItemType.FEATURE, WorkItemType.BUG],
    WorkItemType.FEATURE: [WorkItemType.CAPABILITY, WorkItemType.BUG],
    WorkItemType.CAPABILITY: [WorkItemType.TASK, WorkItemType.BUG],
    WorkItemType.TASK: [],
    WorkItemType.BUG: [],
}

WORKFLOW_WORK_ITEM_TYPES = frozenset(WorkItemType)


class CycleStatus(str, Enum):
    PLANNED = "planned"
    ACTIVE = "active"
    COMPLETED = "completed"


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    AWAITING = "awaiting"
    DONE = "done"
    WONT_DO = "wont_do"


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_PERMISSION = "awaiting_permission"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ApprovalKind(str, Enum):
    WORKFLOW_GATE = "workflow_gate"
    CLI_PERMISSION = "cli_permission"
    CLI_QUESTION = "cli_question"


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
