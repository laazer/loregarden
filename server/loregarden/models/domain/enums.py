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
    #: Waiting on a person, and deliberately not holding anything up.
    #:
    #: BLOCKED means "this run stopped and someone should look", and a parent
    #: waits for it — correctly, because a blocked child usually means the work
    #: cannot proceed. PARKED means "a person owes this, carry on without it":
    #: the subtree steps over it and keeps dispatching siblings, while the
    #: ticket stays outstanding and its parent stays incomplete.
    #:
    #: The distinction is the whole point. On blobert milestone 14, ticket 22
    #: reported `blocked` for GPU timings a headless agent cannot capture —
    #: exactly as the stage-report contract prescribes — and took milestone 14,
    #: feature 15, capability 21 and 30 unrelated backlog tickets down with it
    #: (lg-workflow-integrity-449).
    PARKED = "parked"
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


class ControlPlaneTransport(str, Enum):
    """How a run actually reaches Loregarden — the channel its prompt must describe.

    Not a preference and not a pin: it is resolved from the wiring the control
    plane performed for that run. ``MCP`` means this process attached its MCP
    server to the agent's session, so native tools exist. ``CLI`` means it did
    not, and the agent's way in is ``scripts/loregarden-cli.sh mcp call``, which
    runs the same tools in-process against the database.

    A prompt rendered for the wrong one is not merely verbose — it instructs the
    agent in a protocol it does not have.
    """

    MCP = "mcp"
    CLI = "cli"


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
    #: The stage-report contract reaches the agent's prompt empty, so every
    #: stage in the workspace fails on a report it was never told how to write.
    STAGE_REPORT_CONTRACT = "stage_report_contract"
    #: A toolchain the execution tree declares it needs but has not installed —
    #: a `package.json` with no `node_modules`, and the like.
    TOOLCHAIN_INSTALLED = "toolchain_installed"
    #: The git directory the run must write to is not writable, so the agent
    #: produces work it can never stage.
    GIT_WRITABLE = "git_writable"
    #: A configured transition-gate command that does not resolve from the
    #: directory the agent will run it in.
    GATE_COMMANDS_RESOLVE = "gate_commands_resolve"


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


class StageBudgetArtifactKind(StrEnum):
    """The `artifacts.kind` values the stage retry breaker owns.

    Dedicated kinds so none of them ever collides with a diff/log/test/evidence
    artifact, and separate from each other so the audit rows never inflate or
    deflate the counter itself. See `services.stage_retry_budget`.
    """

    #: The counter. One row per dispatch pass.
    DISPATCH = "stage_dispatch"
    #: One row per dispatch forced past an exhausted budget, with attribution.
    DISPATCH_OVERRIDE = "stage_dispatch_override"
    #: One row per free dispatch the scope-denial reroute exemption granted.
    DISPATCH_REROUTE = "stage_dispatch_reroute"
    #: The structural mark that this breaker is why the ticket is blocked.
    RETRY_BLOCK = "stage_retry_block"


class GateFaultAttribution(StrEnum):
    """Whose problem a failing transition gate describes.

    The orchestrator's only vocabulary for a stage that did not advance was
    "the agent needs rework", which sent eight of nine faults on the blobert
    milestone 14 run to the participant least able to fix them. The clearest
    case: a worktree-scoped gate failed on a file belonging to a different
    ticket that had never been committed, and the implementer was rerouted
    three times to explain it could not act.

    UNKNOWN is not a failure of the classifier, it is the honest answer most of
    the time — 91% of succeeded runs record no changed paths, so the ticket's
    own side of the comparison is frequently empty. It routes exactly as today,
    which keeps a failure to classify costing nothing beyond the status quo.
    """

    #: The gate named at least one path this ticket's runs touched.
    TICKET = "ticket"
    #: The gate named paths, none of them this ticket's. Somebody else's code.
    FOREIGN = "foreign"
    #: Not enough information to say — no recorded paths for the ticket, or no
    #: paths extractable from the gate output.
    UNKNOWN = "unknown"


class ReworkStopReason(StrEnum):
    """Why a rework loop stopped, when it did.

    A bool said only "stop", so the human got the count's explanation whatever
    the actual reason. The two are different situations and want different
    next actions: a loop that ran out of budget may still have been converging,
    while one that repeated itself was never going to.
    """

    #: Keep going — the loop has budget left and is still changing.
    NONE = "none"
    #: `MAX_REWORK_REROUTES` reroutes to this stage already happened.
    BUDGET = "budget"
    #: The same finding, against the same tree, twice running.
    STUCK = "stuck"


class ReworkArtifactKind(StrEnum):
    """The `artifacts.kind` values the rework-feedback ledger owns.

    Its own kind rather than `context`, which it shared until migration 0103.
    Sharing made the ledger unqueryable: the count of reroutes for a target
    stage *is* the loop metric `MAX_REWORK_REROUTES` caps, and asking for it
    returned either zero (`kind='rework_feedback'`) or sixteen hundred rows of
    unrelated run context. See `services.rework_feedback`.
    """

    #: One row per reroute, carrying that round's full fix direction.
    FEEDBACK = "rework_feedback"


class DispatchSurface(StrEnum):
    """Where a stage dispatch was asked for.

    Recorded on the audit row a forced dispatch writes (`stage_retry_budget`),
    so a human click past an exhausted retry budget and an agent's own
    `loregarden_start_stage` are not byte-identical after the fact.
    """

    #: A REST caller — the Dashboard's "Run stage" button, or an operator's curl.
    HTTP = "http"
    #: MCP `loregarden_start_stage`.
    MCP = "mcp"
    #: A queue lane promoting a parked stage entry.
    QUEUE = "queue"
    #: `POST /stage-fanout` — one deliberate decision, N attempts.
    FANOUT = "fanout"
    #: The automatic conflict-resolution re-dispatch.
    CONFLICT = "conflict"


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


class RunUsageStatus(str, Enum):
    """Why a run's token figures are, or are not, on the row.

    NULL token columns already say "not measured", which is what stops an
    unknown run being summed in as a free one. What they could not say is *why*,
    and two different facts shared that value: an adapter with no usage surface
    at all, and an adapter that should have reported and did not. The first is a
    known limitation, the second is a defect, and only one of them is worth
    chasing (lg-workflow-integrity-496).

    UNKNOWN is the empty string, so every row written before this column existed
    reads back as "nobody recorded a reason" rather than as a measurement.
    """

    UNKNOWN = ""
    #: Figures were read from the adapter's output and are on the row.
    MEASURED = "measured"
    #: The run finished and its output carried no usage the parser could read.
    UNAVAILABLE = "unavailable"
    #: This adapter has no usage surface, so nothing was expected.
    UNSUPPORTED = "unsupported"


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
    STAGE_SKIPPED = "StageSkipped"
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


class ReferenceCacheOutcome(StrEnum):
    """Where a reference payload's body came from.

    Present on *every* payload the fetch-through cache returns, failures
    included: without it a served-stale copy — a body and an error together —
    cannot be told apart from a success, and ``STALE_ERROR`` is the single
    deliberate case where both are non-empty.
    """

    #: Fetched over the network this call, or fetched and failed with nothing
    #: cached to fall back on. ``error`` discriminates the two.
    MISS = "miss"
    #: Served from a row still inside the TTL; no request was made.
    HIT = "hit"
    #: A conditional GET answered 304; the stored body stands, its age is reset.
    REVALIDATED = "revalidated"
    #: The refresh failed and a stale row was served instead of nothing.
    STALE_ERROR = "stale_error"


class ReferenceFetchError(StrEnum):
    """Why a reference fetch produced no fresh body.

    A payload's ``error`` contains exactly one of these values plus whatever
    detail the reason carries, so a caller can classify a failure without
    parsing prose — and so a string naming every kind at once classifies
    nothing and fails its assertion.
    """

    #: The URL, or a redirect hop, failed the SSRF guard.
    BLOCKED = "blocked"
    UNSUPPORTED_CONTENT_TYPE = "unsupported_content_type"
    TOO_LARGE = "too_large"
    #: The body arrived but extraction produced nothing; never cached, because
    #: caching an empty page would serve emptiness for a whole TTL.
    EXTRACTION_FAILED = "extraction_failed"
    TOO_MANY_REDIRECTS = "too_many_redirects"
    #: Transport failure, timeout, or an HTTP error status.
    FETCH_ERROR = "fetch_error"
    #: An exception no path in the cache anticipated — a bug here, or an
    #: environment failure (the database refusing a commit) rather than the
    #: remote misbehaving. Kept distinct from ``FETCH_ERROR`` deliberately:
    #: flattening the two tells a caller to retry the URL when the thing that
    #: broke was us.
    INTERNAL_ERROR = "internal_error"


class DevDocsError(StrEnum):
    """Why a DevDocs search produced no results.

    Separate from ``ReferenceFetchError`` because these are the search's own
    failures, not the cache's: a docset that does not resolve is not a fetch
    problem, and flattening the two would tell a caller to retry a URL when the
    thing that failed was their argument. A transport failure underneath is
    still reported here, as ``*_UNAVAILABLE``, with the cache's own reason
    carried in the message.
    """

    #: No docset given, and the query alone cannot pick one.
    DOCSET_REQUIRED = "docset_required"
    #: A docset was named and matched nothing.
    DOCSET_UNRESOLVED = "docset_unresolved"
    #: A name matched several docsets; the caller has to choose.
    DOCSET_AMBIGUOUS = "docset_ambiguous"
    #: The catalog or index could not be fetched — the cache said why.
    CATALOG_UNAVAILABLE = "catalog_unavailable"
    INDEX_UNAVAILABLE = "index_unavailable"
    #: The body arrived and was not the shape DevDocs documents. Never cached,
    #: for the same reason an empty page is not: a bad body kept for a TTL is a
    #: broken docset for a TTL.
    CATALOG_INVALID = "catalog_invalid"
    INDEX_INVALID = "index_invalid"


class MemoryBriefingOutcome(str, Enum):
    """What one inherited-wisdom assembly actually did.

    The distinction the whole briefing-telemetry ticket turns on is
    ``STORE_ERROR``/``NO_STORE`` versus ``EMPTY``: a vault that would not open
    and a vault that opened and held nothing produce the same empty prompt
    section, and collapsing them is how retrieval stayed dead for a month
    without anyone noticing.
    """

    BUILT = "built"
    EMPTY = "empty"
    STORE_ERROR = "store_error"
    NO_STORE = "no_store"
    SKIPPED = "skipped"


class MemoryStoreKind(str, Enum):
    """The stores a briefing reads, plus the factory that builds them.

    ``CHECKPOINTS``, ``VAULT`` and ``GRAPH`` are real stores and are the only
    keys ever present in ``store_states_json``. ``SERVICE`` is the store-factory
    pseudo-store: it appears only in ``store_errors``, when
    ``AgentMemoryService.from_settings()`` itself raised and no store was ever
    reached.
    """

    CHECKPOINTS = "checkpoints"
    VAULT = "vault"
    GRAPH = "graph"
    SERVICE = "service"


class MemoryStoreState(str, Enum):
    """What one store *was* during an assembly — never what its row count implies.

    ``NOT_QUERIED`` is load-bearing. ``recall_related`` returns before touching
    either recall store when the query tokenises to no terms, so neither READ
    (nothing was read) nor UNCONFIGURED (the store exists) is true of it. It
    never contributes to ``STORE_ERROR`` and never counts as READ.
    """

    READ = "read"
    UNCONFIGURED = "unconfigured"
    ERRORED = "errored"
    NOT_QUERIED = "not_queried"


class MemoryBriefingAssembly(str, Enum):
    """Which prompt-assembly path built a briefing.

    ``DISPATCH`` is supervised dispatch; ``RENDER`` is ``render_stage_prompt``,
    which the terminal handoff also goes through. Two assemblies for one run is
    a live path, and without this a legitimate pair is indistinguishable from a
    double write.
    """

    DISPATCH = "dispatch"
    RENDER = "render"


class ChatSurface(StrEnum):
    """An operator-facing chat rail.

    Home and ticket triage are the same agent answering in two places; the
    surface is what tells a shared prompt block which one it is rendering for.
    Branch triage and Ticket Studio are declared but not yet routed through the
    shared blocks — they adopt them by passing their own member.
    """

    HOME = "home"
    TICKET_TRIAGE = "ticket_triage"
    BRANCH_TRIAGE = "branch_triage"
    TICKET_STUDIO = "ticket_studio"


class CliTool(StrEnum):
    """A CLI agent's built-in tools, spelled exactly as the CLI reports them.

    Parallel to ``loregarden.mcp.tool_ids``, which owns the Loregarden MCP tool
    names. These are the ones named in a Claude ``--allowedTools`` list and in a
    permission prompt's ``tool_name``. Policy groupings over this vocabulary
    live in ``loregarden.agents.cli_tool_ids``; the names themselves live here
    because API schemas reference them and models must not import agents.
    """

    READ = "Read"
    WRITE = "Write"
    EDIT = "Edit"
    BASH = "Bash"
    GLOB = "Glob"
    GREP = "Grep"
    WEB_FETCH = "WebFetch"
    WEB_SEARCH = "WebSearch"
    TASK = "Task"
    TODO_WRITE = "TodoWrite"
    ASK_USER_QUESTION = "AskUserQuestion"
    NOTEBOOK_EDIT = "NotebookEdit"


class ToolPosture(StrEnum):
    """How an agent's tool access is decided.

    ``INHERIT`` is the default and means "whatever the runtime offers" — the
    behaviour every agent had before grants existed, so an un-configured agent
    is unaffected. ``ALLOWLIST`` narrows to a derived set; ``UNRESTRICTED`` is
    an explicit opt-out that reads differently from a default in the UI.
    """

    INHERIT = "inherit"
    ALLOWLIST = "allowlist"
    UNRESTRICTED = "unrestricted"


class ToolGrantWarningCode(StrEnum):
    """Why an agent's configured tool grants may not do what they look like.

    A grant that quietly has no effect is the failure this vocabulary exists to
    prevent: every code here names a case where the Studio control would
    otherwise accept a setting and change nothing.
    """

    ADAPTER_IGNORES_GRANTS = "adapter_ignores_grants"
    AUTO_APPROVED_EXCLUDED = "auto_approved_excluded"
    ALL_MCP_EXCLUDED = "all_mcp_excluded"
    EMPTY_ALLOWLIST = "empty_allowlist"
    UNKNOWN_MCP_SERVER = "unknown_mcp_server"


class ChatMode(StrEnum):
    """Whether a chat rail's next turn can change anything.

    The operator-facing counterpart of ``TurnIntent``: the same two states, but
    published on a snapshot so the UI can say which one it is *before* a message
    is sent, rather than after the reply comes back read-only.
    """

    ACT = "act"
    ADVISORY = "advisory"


class ChatAdvisoryCause(StrEnum):
    """Why a rail cannot act. One member per real cause, not a catch-all.

    A single "advisory" bit told an operator that something was wrong and
    nothing about what — and two of these causes were decided per turn and never
    reached the snapshot at all, so the UI could promise a rail could act and
    then run it read-only. Each member carries a distinct remediation; see
    ``services.chat_mode``.
    """

    #: The resolved adapter has neither a permission bridge nor a writable
    #: oneshot path (opencode, local, or an unrecognised id).
    ADAPTER_CANNOT_EXECUTE = "adapter_cannot_execute"
    #: The adapter has a write path but only reaches it with permission bypass
    #: on. Distinct from ADAPTER_CANNOT_EXECUTE: the tool is capable, the
    #: configuration is not letting it, and those need different advice.
    ADAPTER_NEEDS_PERMISSION_BYPASS = "adapter_needs_permission_bypass"
    #: Branch triage on a branch with no worktree. Writes need somewhere to land.
    BRANCH_NOT_CHECKED_OUT = "branch_not_checked_out"
    #: A bridge-capable turn with no AgentRun to hang approvals on. Internal —
    #: the operator cannot fix this one, so the UI says so rather than
    #: suggesting a knob that will not help.
    NO_RUN_FOR_APPROVALS = "no_run_for_approvals"
    #: A surface that is read-only by construction rather than by capability:
    #: diff review and the branch-triage message path both answer from the
    #: record. Not a fault, and not something to fix.
    SURFACE_IS_READ_ONLY = "surface_is_read_only"
    #: A BTW aside — answered by an observer reading the run's log. Read-only by
    #: design, and the one advisory state that is working as intended.
    ASIDE_OBSERVER = "aside_observer"
