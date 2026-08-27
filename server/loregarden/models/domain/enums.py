"""Enums and shared helpers for the domain models."""

from datetime import datetime, timezone
from enum import Enum, StrEnum

from sqlalchemy import Column
from sqlalchemy import Enum as SAEnum


def _str_enum_column(
    enum_cls: type[Enum],
    default: Enum | None = None,
    *,
    index: bool = False,
    nullable: bool = False,
) -> Column:
    """Store the enum's *value*, not its member name.

    SQLAlchemy defaults to persisting names, which leaves a schema where one column
    reads ``blocked`` and its neighbour ``BLOCKED``. Every enum column here goes
    through this helper so the convention is uniform — a mixed schema is what let a
    single hand-written row take down every endpoint that listed tickets.

    ``default`` is omitted for columns the caller must always supply. ``nullable``
    is for a column whose absence is itself the fact — a run with no external
    harness ran on the control plane's own agents.
    """
    return Column(
        SAEnum(enum_cls, values_callable=lambda choices: [c.value for c in choices]),
        nullable=nullable,
        default=default.value if default is not None else None,
        index=index,
    )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def comparable_utc(value: datetime) -> datetime:
    """One instant for ordering, whether SQLite returned naive or aware.

    Naive values are UTC: that is how ``utcnow`` writes, and how SQLite
    round-trips an aware datetime by dropping tzinfo. Comparing the raw
    column raises TypeError once a ticket mixes both.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class TicketState(str, Enum):
    BACKLOG = "backlog"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    WONT_DO = "wont_do"


class TicketActivity(StrEnum):
    """What is executing on a ticket right now — derived, never a column.

    ``TicketState.IN_PROGRESS`` means started-and-unfinished, which most of the
    board is; it does not mean an agent is on it. This is the orthogonal axis,
    computed from the run tables by ``services.ticket_activity``.
    """

    RUNNING = "running"
    AWAITING = "awaiting"
    QUEUED = "queued"
    IDLE = "idle"


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


class CliAdapter(str, Enum):
    """Which CLI (or in-process runner) executes an agent turn.

    ``DEFAULT`` is not an executor — it is the "inherit the next tier down"
    sentinel that workspace and ticket pins store, and ``resolve_effective_adapter``
    never returns it.
    """

    DEFAULT = "default"
    LOCAL = "local"
    CLAUDE = "claude"
    CURSOR = "cursor"
    CODEX = "codex"
    LMSTUDIO = "lmstudio"
    OPENCODE = "opencode"


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


class DoctorStatus(str, Enum):
    """A doctor check's outcome. WARN exists because several of these matter only
    in a running dev loop, and failing a dispatch over one would be worse than
    the condition it reports."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class DoctorCheck(str, Enum):
    """The environment traps this control plane hits repeatedly.

    Each one has cost a real run and each is currently prevented by an agent
    remembering it, which is the thing worth replacing. See `services.doctor`.
    """

    #: `core.bare` true in a working checkout — every work-tree git operation
    #: then fails with a misleading exit-128 checkout error.
    GIT_CORE_BARE = "git_core_bare"
    #: GIT_DIR / GIT_WORK_TREE in the ambient environment, where they beat `cwd`
    #: and point work at the wrong repository.
    GIT_ENV_LEAK = "git_env_leak"
    #: The database resolved relative to a worktree, which answers every ticket
    #: query with a silent zero instead of an error.
    DB_RESOLUTION = "db_resolution"
    #: Backend `.py` edits newer than the reload sentinel, so a running dev
    #: server is still serving the code the fix replaced.
    BACKEND_RELOAD_SENTINEL = "backend_reload_sentinel"
    #: No usable credential for the configured agent CLI, which reports "not
    #: logged in" in a way that reads as a code bug.
    CLI_CREDENTIALS = "cli_credentials"
    #: Where the branch stands against its remote.
    GIT_PORTABILITY = "git_portability"
    #: A repository with no commit at all, which several git helpers assume away.
    REPO_HAS_COMMIT = "repo_has_commit"


class PortabilityState(str, Enum):
    """Where a branch stands against its remote, reported rather than judged —
    PUSH_REQUIRED is the normal state in the middle of a ticket."""

    #: No remote, or no upstream for this branch.
    LOCAL_ONLY = "local_only"
    #: Ahead of upstream: work exists only on this machine.
    PUSH_REQUIRED = "push_required"
    #: Ahead and behind. Landing this needs a decision, not a push.
    REMOTE_DIVERGED = "remote_diverged"
    #: In sync with upstream.
    REMOTE_READY = "remote_ready"


class ClaimCertainty(str, Enum):
    """How much weight a handoff checklist item's claim carries.

    Items used to carry a free-text `evidence` string, and the met-counter
    treated any non-empty string as proof. "ran the suite, all green" satisfied
    it exactly as well as an attached test artifact did, which is how a stage
    claims a suite nobody ran.

    Three levels, not AHP+'s six. STALE is missing on purpose: it is not a claim
    anyone makes, it is something that happens to a claim when the code moves
    underneath it, and it is derived at read time (see
    `services.handoff_certainty.ClaimStanding`). CONFLICTED needs two claims to
    compare and nothing here writes a second one yet.
    """

    #: An evidence artifact on this ticket backs it.
    VERIFIED = "verified"
    #: A human approved it. Not artifact-backed, but not the agent's own word.
    USER_CONFIRMED = "user_confirmed"
    #: The agent believes it and has no artifact. The default, and deliberately
    #: the weak claim — an omitted certainty must never read as proof.
    INFERRED = "inferred"


class BoundaryVerdict(str, Enum):
    """How the tree a stage is about to run on compares to the one its
    predecessor attested against. See `services.handoff_boundary`.

    Three of these proceed and three do not, but none of them mean "broken" on
    their own — a mismatch is far more often a human working in the same
    checkout than a damaged ticket.
    """

    #: Same checkout, same branch, same commit.
    MATCH = "match"
    #: One side recorded no boundary: a handoff written before boundaries
    #: existed, or a repo that could not be read. Not the same claim as a
    #: mismatch, and the reason enforcement can be switched on at all.
    UNKNOWN = "unknown"
    #: Same checkout and branch, receiver's HEAD descends from the sender's.
    #: The ordinary case between stages, since the orchestrator commits.
    ADVANCED = "advanced"
    #: Same branch, receiver's HEAD is not a descendant — a force-push, a reset,
    #: or a squash-merge that landed underneath the ticket.
    DIVERGED = "diverged"
    #: A different branch than the sender attested against.
    BRANCH_CHANGED = "branch_changed"
    #: A different checkout entirely, or one that no longer contains the
    #: sender's commit — the worktree case, in both directions.
    REPO_CHANGED = "repo_changed"


class StageFanoutGroupStatus(str, Enum):
    OPEN = "open"
    SETTLING = "settling"
    SETTLED = "settled"
    CANCELLED = "cancelled"
    FAILED = "failed"


class StageFanoutOutcome(str, Enum):
    PENDING = "pending"
    PROMOTED = "promoted"
    DECLINED = "declined"
    CANCELLED = "cancelled"
    FAILED = "failed"


class StageFanoutAttemptStatus(str, Enum):
    PLANNED = "planned"
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_PERMISSION = "awaiting_permission"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DECLINED = "declined"
    PROMOTED = "promoted"


class ApprovalKind(str, Enum):
    WORKFLOW_GATE = "workflow_gate"
    CLI_PERMISSION = "cli_permission"
    CLI_QUESTION = "cli_question"


class OrchestrationDriver(str, Enum):
    BUILTIN_AUTOPILOT = "builtin_autopilot"
    EXTERNAL_MCP = "external_mcp"
    MANUAL_STAGE = "manual_stage"


class ExternalHarness(str, Enum):
    """A coding harness outside this control plane that drove a run.

    Stamped on the orchestration run and on every agent run it opens, so a
    ticket executed by a pasted prompt in someone's own Claude Code or Codex
    session is comparable against the same ticket run by loregarden's agents
    rather than indistinguishable from one.
    """

    CLAUDE_CODE = "claude_code"
    CODEX = "codex"
    CURSOR = "cursor"
    OTHER = "other"


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
    GATE_EVALUATED = "GateEvaluated"


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


class GateOutcome(str, Enum):
    """How a transition-gate evaluation ended.

    ``UNAVAILABLE`` is the one that earns its keep. A gate that timed out or
    whose command is not on PATH used to be reported as ``FAILED``, which is
    what the orchestrator hands to the stage's own agent to fix — so a hung
    `npx` in a worktree with no node_modules cost 300s, then an autofix pass,
    then a whole agent re-run of a stage that had already passed, and round
    again. No agent can install a toolchain it cannot see. "Could not run" is a
    fact about the machine and goes straight to a human.
    """

    PASSED = "passed"
    SKIPPED = "skipped"
    DISABLED = "disabled"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


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


class BtwStatus(str, Enum):
    """Lifecycle of an aside asked while a run is working.

    Escalation is deliberately not a state here: a question can be put to the
    working agent before or after the observer answers it, so it is an
    independent fact (``escalated_at``) rather than a point on this line.
    """

    PENDING = "pending"
    ANSWERED = "answered"
    FAILED = "failed"


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


class ViewKind(str, Enum):
    """Which arrangement a view's layout carries.

    The tag is what selects the layout body — a flex grid has a recursive split
    tree, a canvas a flat positioned list — so it is a closed vocabulary, not a
    label.
    """

    FLEX_GRID = "flex_grid"
    CANVAS = "canvas"


class ContainerKind(str, Enum):
    """What a container hosts. The panel's *primitive* is a further vocabulary,
    owned by the frontend registry, and is not this enum."""

    TERMINAL = "terminal"
    PANEL = "panel"
    WEB_EMBED = "web_embed"


class SplitOrientation(str, Enum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"


class SidebarEntryKind(str, Enum):
    """The two things that share the sidebar's one ordered list."""

    VIEW = "view"
    PAGE = "page"


class ReferencePageKind(str, Enum):
    """What a cached reference document is, from the fetcher's point of view.

    Raw DevDocs JSON rows ride the same cache as rendered pages; the kind is
    how a later reader tells them apart without sniffing the body.
    """

    PAGE = "page"
    INDEX = "index"
    CATALOG = "catalog"
