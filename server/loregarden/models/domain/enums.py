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


class CompatibilityPosture(str, Enum):
    """How much freedom an agent has to change existing interfaces and tests.

    Agents used to be told, unconditionally, to "maintain backward compatibility"
    — so they defended consumers that do not exist and contorted code around tests
    that encoded the wrong behaviour, rather than fixing the design. This makes the
    obligation an explicit, per-work-item decision instead of a hardcoded default.
    """

    GREENFIELD = "greenfield"
    INTERNAL = "internal"
    PUBLIC = "public"


DEFAULT_COMPATIBILITY_POSTURE = CompatibilityPosture.INTERNAL

# Agent-facing contract for each posture. This text is injected verbatim into the run
# context, so it is the operative instruction — keep it imperative and unambiguous.
COMPATIBILITY_POSTURE_CONTRACT: dict[CompatibilityPosture, str] = {
    CompatibilityPosture.GREENFIELD: (
        "This work has no consumers outside this repository, and nothing depends on its "
        "current interfaces.\n"
        "- Delete, rename and reshape freely. Prefer the correct design over the compatible one.\n"
        "- Do NOT add compatibility shims, deprecation windows, aliases, or dual code paths.\n"
        "- Do NOT preserve an interface merely because it exists.\n"
        "- Tests: an existing test has no special authority here. If a test encodes behaviour "
        "the spec no longer wants, change or delete it and say so — do not contort the "
        "implementation to satisfy it."
    ),
    CompatibilityPosture.INTERNAL: (
        "This work has consumers, but every one of them lives in this repository.\n"
        "- Break interfaces freely when the design is better for it — but migrate EVERY caller "
        "in the same change. Leave nothing behind.\n"
        "- Do NOT add compatibility shims, deprecation windows, or dual code paths to avoid "
        "updating a caller. Update the caller.\n"
        "- Tests: update every test the change affects, in the same change. If a test encodes "
        "behaviour the spec no longer wants, change it and say so — do not contort the "
        "implementation to satisfy it.\n"
        "- A change that leaves a caller or a test broken is incomplete, not compatible."
    ),
    CompatibilityPosture.PUBLIC: (
        "This work has consumers outside this repository that you cannot update.\n"
        "- Preserve existing behaviour and interfaces. Deprecate before removing.\n"
        "- Compatibility shims are appropriate here.\n"
        "- Tests: existing tests encode the contract those consumers rely on. Do not weaken or "
        "delete them to make a change pass; if one genuinely must change, call it out explicitly "
        "as a breaking change."
    ),
}


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


class QueueOperationType(str, Enum):
    """Types of queue operations that can be reviewed."""

    BULK_CANCEL = "bulk_cancel"
    BULK_PAUSE = "bulk_pause"
    BULK_REORDER = "bulk_reorder"
    RETRY = "retry"
    RETRY_ALL = "retry_all"
    SKIP_FAILED = "skip_failed"
    RESTORE = "restore"


class TicketStudioSessionStatus(str, Enum):
    DRAFT = "draft"
    COMMITTED = "committed"


class CIStatus(str, Enum):
    PENDING = "pending"
    PASSING = "passing"
    FAILING = "failing"
    PARTIAL = "partial"
    SKIPPED = "skipped"


class AutoFixStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class WorktreeState(str, Enum):
    ACTIVE = "active"
    MERGED = "merged"
    FAILED = "failed"
    CLEANUP = "cleanup"


class QueuePosition(str, Enum):
    QUEUED = "queued"
    SCHEDULED = "scheduled"
    PROMOTED = "promoted"
    STARTED = "started"
    ACTIVE = "active"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    FAILED = "failed"
    SKIPPED = "skipped"
