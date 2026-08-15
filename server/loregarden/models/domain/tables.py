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
    BoundaryVerdict,
    BtwStatus,
    CIStatus,
    CycleStatus,
    EventType,
    ExternalHarness,
    OrchestrationDriver,
    OrchestrationRunStatus,
    QueueOperationType,
    QueuePosition,
    RunStatus,
    SidebarEntryKind,
    StageFanoutAttemptStatus,
    StageFanoutGroupStatus,
    StageFanoutOutcome,
    StageStatus,
    TicketState,
    TicketStudioSessionStatus,
    ViewKind,
    WorkItemType,
    WorktreeState,
    _str_enum_column,
    utcnow,
)
from pydantic import model_validator
from sqlalchemy import CheckConstraint, Index, UniqueConstraint
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
    codex_model: str = ""
    lmstudio_base_url: str = ""
    lmstudio_model: str = ""
    opencode_model: str = ""
    claude_effort: str = ""
    cursor_effort: str = ""
    lmstudio_effort: str = ""
    opencode_effort: str = ""
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
    state: TicketState = Field(
        default=TicketState.BACKLOG,
        sa_column=_str_enum_column(TicketState, TicketState.BACKLOG),
    )
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
    #: Free-form labels, a JSON array of strings (see services.ticket_tags for the
    #: one place they are normalized). Not a table: tags carry no attributes of
    #: their own and are only ever read alongside the ticket that owns them.
    tags_json: str = "[]"
    workflow_stage_key: str = ""
    workflow_stage_status: StageStatus = Field(
        default=StageStatus.PENDING,
        sa_column=_str_enum_column(StageStatus, StageStatus.PENDING),
    )
    revision: int = Field(default=0)
    last_updated_by: str = ""
    next_agent: str = ""
    next_status: str = "Proceed"
    blocking_issues: str = ""
    # Authoritative "run this agent next for the current stage" pin, set only when
    # a scoped implementer is denied a write onto a sibling implementer's subtree
    # (see agent_scope / permission_bridge). Outranks classify keyword-scoring in
    # resolve_stage_execution and is cleared the moment it is consumed at dispatch,
    # so it steers exactly one re-run and cannot become a sticky stale hint.
    scope_reroute_agent: str = ""
    # A synthesized "review that the parent's children integrate" work item. It is
    # childless (so it runs its own workflow, unlike an aggregator parent) and is
    # ordered to run last among its siblings (see child_sort_key). Added under
    # feature/milestone parents by the Ticket Studio draft repair and the backfill.
    is_integration_review: bool = Field(default=False)
    state_locked: bool = Field(default=False)
    workflow_disabled: bool = Field(default=False)
    # Per-ticket override of the workspace's git automation policy, as a JSON
    # object holding only the keys that differ (see GitAutomationConfig). Empty
    # means "inherit the profile" — which is not the same as "everything off",
    # so this cannot be a set of boolean columns defaulting to false.
    git_automation_json: str = ""
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
    # Set when a harness outside this control plane drove the run from a pasted
    # prompt (see services/external_harness.py). Null means loregarden's own
    # agents ran it. Indexed because comparing harnesses is the point of the
    # column — it is always read as a filter, never on its own.
    #: Renewed by any control-plane write naming this run. The liveness signal
    #: `status` could never be — only the owner moves a status, so an owner that
    #: walked away left the run claiming to be alive forever. Null means never
    #: renewed, which reads as the run's own start time.
    last_seen_at: datetime | None = None
    external_harness: ExternalHarness | None = Field(
        default=None,
        sa_column=_str_enum_column(ExternalHarness, index=True, nullable=True),
    )
    status: OrchestrationRunStatus = Field(
        default=OrchestrationRunStatus.QUEUED,
        sa_column=_str_enum_column(OrchestrationRunStatus, OrchestrationRunStatus.QUEUED),
    )
    current_stage_key: str = ""
    error_message: str = ""
    auto_approve: bool = Field(default=False)
    stop_at_stage_key: str = ""
    # Per-orchestration agent timeout for every stage run this orchestration
    # (and its child ticket orchestrations) starts. Null = each agent's default.
    timeout_override_seconds: int | None = Field(default=None)
    # Cooperative stop: set by the API, observed by BuiltinOrchestrator between
    # stages. Null means no cancel has been requested.
    cancel_requested_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)


class AgentRun(SQLModel, table=True):
    __tablename__ = "agent_runs"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    run_code: str = Field(index=True)
    # Null for workspace-scoped runs (Home Baxter chat), which answer about the
    # whole workspace rather than one work item.
    ticket_id: str | None = Field(default=None, foreign_key="tickets.id", index=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    orchestration_run_id: str | None = Field(
        default=None, foreign_key="orchestration_runs.id", index=True
    )
    agent_id: str
    # Version of the agent definition this run executed under (null pre-versioning).
    agent_version: int | None = Field(default=None)
    # The harness that executed this stage, when it was not this control plane's
    # own subprocess — inherited from the orchestration run that opened it.
    external_harness: ExternalHarness | None = Field(
        default=None,
        sa_column=_str_enum_column(ExternalHarness, index=True, nullable=True),
    )
    skill_name: str = ""
    stage_key: str = ""
    status: RunStatus = Field(
        default=RunStatus.QUEUED,
        sa_column=_str_enum_column(RunStatus, RunStatus.QUEUED),
    )
    command: str = ""
    # Paths this run left dirty, so its commit can be scoped to its own work
    # instead of sweeping unrelated edits out of the workspace.
    changed_paths_json: str = "[]"
    # The git boundary this run started from — see schemas.GitBoundary, which is
    # how these four are read and written. Recorded at dispatch, after the
    # execution root and branch are resolved, so it describes the tree the agent
    # actually saw. All empty means the boundary could not be read, which is
    # `unknown` rather than a mismatch.
    start_repo_path: str = ""
    start_branch: str = ""
    start_head_sha: str = ""
    start_dirty_paths_json: str = "[]"
    # How that boundary compared to the one the last handoff attested against.
    # Written for every dispatch, matching verdicts included, so the mismatch
    # rate is queryable — it is what decides when enforcement can move from
    # record-only to blocking.
    start_boundary_verdict: BoundaryVerdict = Field(
        default=BoundaryVerdict.UNKNOWN,
        sa_column=_str_enum_column(BoundaryVerdict, BoundaryVerdict.UNKNOWN),
    )
    # Doctor checks that failed before this run was dispatched, as a JSON array
    # of check ids. Only the failures: an empty array is a healthy environment,
    # and storing seven "fine"s per dispatch to record the one case anyone
    # queries is not worth the rows.
    start_preflight_failures_json: str = "[]"
    stdout: str = ""
    stderr: str = ""
    auto_approve: bool = Field(default=False)
    timeout_override_seconds: int | None = Field(default=None)
    # Terminal-handoff liveness. A handoff run is created RUNNING before any
    # process exists — the pasted command's check-in records when (and as which
    # shell pid) it actually started, so a handoff that was never pasted, or
    # whose terminal died, can be reaped instead of blocking the ticket forever.
    handoff_accepted_at: datetime | None = None
    handoff_pid: int | None = None
    #: The detached agent process, and a fingerprint that survives pid reuse.
    #: Separate from handoff_pid: that one names a terminal a human pasted into,
    #: this one names a process this server spawned. See services/process_identity.
    agent_pid: int | None = None
    agent_pid_identity: str = ""
    #: Renewed by the thread supervising this run; see services/run_lease.
    #: Null means never renewed, which reads as the run's start time.
    last_seen_at: datetime | None = None
    # Cooperative stop: set by the API, observed by the permission bridge /
    # print-mode loop. Null means no cancel has been requested.
    cancel_requested_at: datetime | None = None
    # The isolated checkout this run executes in, when it has one. Null means
    # the run uses the shared workspace root. Orchestration set this attribute
    # before the column existed, so it silently did not persist and every
    # worktree created for a parallel run was orphaned.
    #
    # Indexed but deliberately not a foreign key: `worktrees.agent_run_id`
    # already points back here, and declaring both directions gives SQLAlchemy
    # a table cycle it cannot order ("unresolvable cycles between tables
    # agent_runs, worktrees"). The worktree side owns the constraint.
    worktree_id: str | None = Field(default=None, index=True)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)


class StageFanoutGroup(SQLModel, table=True):
    __tablename__ = "stage_fanout_groups"
    __table_args__ = (
        CheckConstraint("attempt_count >= 1", name="ck_stage_fanout_attempt_count"),
        Index("ix_stage_fanout_groups_ticket_stage", "ticket_id", "stage_key"),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    orchestration_run_id: str | None = Field(
        default=None, foreign_key="orchestration_runs.id", index=True
    )
    stage_key: str
    attempt_count: int = Field(default=1, ge=1)
    pre_fanout_workflow_stage_key: str = ""
    pre_fanout_workflow_stage_status: str = StageStatus.PENDING.value
    pre_fanout_stage_map_json: str = "[]"
    pre_fanout_next_agent: str = ""
    status: StageFanoutGroupStatus = Field(
        default=StageFanoutGroupStatus.OPEN,
        sa_column=_str_enum_column(StageFanoutGroupStatus, StageFanoutGroupStatus.OPEN, index=True),
    )
    outcome: StageFanoutOutcome = Field(
        default=StageFanoutOutcome.PENDING,
        sa_column=_str_enum_column(StageFanoutOutcome, StageFanoutOutcome.PENDING),
    )
    # Indexed but deliberately not a foreign key: StageFanoutAttempt.group_id
    # owns the child relation, and adding a reciprocal winner FK creates the
    # same table-cycle risk as AgentRun.worktree_id.
    winner_attempt_id: str | None = Field(default=None, index=True)
    declined_reason: str = ""
    failure_summary: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    settled_at: datetime | None = None


class StageFanoutAttempt(SQLModel, table=True):
    __tablename__ = "stage_fanout_attempts"
    __table_args__ = (
        UniqueConstraint("group_id", "attempt_index"),
        CheckConstraint("attempt_index >= 0", name="ck_stage_fanout_attempt_index"),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    group_id: str = Field(foreign_key="stage_fanout_groups.id", index=True)
    attempt_index: int = Field(ge=0)
    attempt_name: str = ""
    agent_run_id: str | None = Field(default=None, foreign_key="agent_runs.id", index=True)
    worktree_id: str | None = Field(default=None, foreign_key="worktrees.id", index=True)
    branch: str = ""
    status: StageFanoutAttemptStatus = Field(
        default=StageFanoutAttemptStatus.PLANNED,
        sa_column=_str_enum_column(
            StageFanoutAttemptStatus, StageFanoutAttemptStatus.PLANNED, index=True
        ),
    )
    failure_details: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class McpServer(SQLModel, table=True):
    """A third-party MCP server this control plane knows about.

    Agents reached exactly one server — loregarden's own, hardcoded into the
    `--mcp-config` payload — so anything else an agent might need had to be
    configured outside the control plane, where nothing could see or audit it.

    Two departures from a naive "store the config" table:

    `auth_env_var` holds the *name* of an environment variable, never a token.
    This database is copied around freely — into scratch dirs for migration
    dry-runs, into worktrees — and a secret at rest in it would travel with
    every copy. The value is read from the environment when the server is used.

    The health columns record what an actual `initialize` handshake found, and
    were added only once something performed one. There is still no rate-limit
    column: nothing enforces a limit, and a column no code reads is a claim the
    UI would happily render as fact.
    """

    __tablename__ = "mcp_servers"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    #: Key under `mcpServers` in the CLI config, so it must be unique.
    name: str = Field(index=True, unique=True)
    description: str = ""
    #: "http" (url) or "stdio" (command + args).
    transport: str = "http"
    url: str = ""
    command: str = ""
    args_json: str = "[]"
    #: Name of the env var holding the credential — not the credential.
    auth_env_var: str = ""
    #: Disabled servers stay registered but are withheld from agents, so a
    #: broken server can be parked without losing how it was configured.
    enabled: bool = True
    #: "prompt" (every call asks the operator) or "auto" (the server is trusted
    #: and its tools run unattended). Server-level rather than per-tool: it
    #: matches how an operator actually reasons about a third party, and a
    #: per-tool allowlist would have to be maintained for every server added.
    tool_policy: str = "prompt"
    #: Calls per minute this server will accept before further calls are
    #: refused. 0 means no ceiling, which is the default — a limit nobody set
    #: should not start refusing work.
    rate_limit_per_min: int = 0
    #: Result of the last health check. `last_checked_at` empty means never
    #: checked, which is a different thing from checked-and-failing and reads
    #: differently in the UI.
    last_checked_at: str = ""
    last_health_ok: bool = False
    last_health_latency_ms: int = 0
    #: Why the last check failed, in terms an operator can act on. Empty when
    #: the check passed.
    last_health_error: str = ""
    #: Tool names the server reported to `tools/list` during the last check.
    #: Cached rather than fetched per request: listing means dialling the
    #: server, which is not something a page render should do.
    tools_json: str = "[]"
    #: When that catalogue was collected. Empty means the tools were never
    #: listed — a server can answer `initialize` and still refuse `tools/list`,
    #: and "no tools" and "we never asked" are not the same answer.
    tools_listed_at: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class McpToolCall(SQLModel, table=True):
    """One tool-permission decision, recorded as it is made.

    The observation point is the permission bridge, which is what makes the
    columns what they are: it sees the *request* and issues a *decision*, and
    never sees the result. Tool execution latency and success are therefore not
    recorded — the CLI runs the tool itself and reports nothing back. Storing
    them as nullable columns would invite a UI to average over nulls.

    `decision_ms` is how long the decision took, which for a prompted call is
    how long the operator took to answer.
    """

    __tablename__ = "mcp_tool_calls"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    run_id: str = Field(index=True)
    ticket_id: str = Field(index=True)
    agent_id: str = ""
    #: Full name as the CLI reported it, e.g. `mcp__github__create_issue`.
    tool_name: str = ""
    #: Parsed server, or "" for a non-MCP tool such as Bash.
    server_name: str = Field(default="", index=True)
    #: How the call was resolved — see services.tool_telemetry.DECISIONS.
    decision: str = ""
    decision_ms: int = 0
    created_at: datetime = Field(default_factory=utcnow, index=True)


class RunMessage(SQLModel, table=True):
    """An operator's message to a run that is already in flight.

    Written by the API and drained by the permission bridge, which holds the
    agent's stdin open. `delivered_at` is what stops a message being injected
    twice, and what lets the UI show whether the agent has actually received it
    — a steer that was queued but never delivered is worse than none, because
    the operator believes the run was corrected.
    """

    __tablename__ = "run_messages"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    run_id: str = Field(foreign_key="agent_runs.id", index=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    content: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    delivered_at: datetime | None = None


class BtwExchange(SQLModel, table=True):
    """A question put to a ticket while one of its runs is working.

    Distinct from ``RunMessage``, which is imperative and one-way: an aside
    expects an answer back and must not change the work. It is answered by a
    separate read-only observer turn rather than by the run itself, so
    ``observed_run_id`` is what the answer is *about*, not what produced it.

    The row is the durable half of the exchange. The answer is also mirrored
    into ``triage_messages`` so the ticket keeps one readable transcript, but a
    mirror cannot carry a pending state, and a turn orphaned by a restart has to
    be settleable — which is what ``status`` is for.
    """

    __tablename__ = "btw_exchanges"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    # Null when nothing was running: an aside asked against an idle ticket is
    # still an aside, and refusing it would make the composer behave differently
    # for no reason the operator can see.
    observed_run_id: str | None = Field(default=None, foreign_key="agent_runs.id", index=True)
    question: str = ""
    answer: str = ""
    status: BtwStatus = Field(
        default=BtwStatus.PENDING,
        sa_column=_str_enum_column(BtwStatus, BtwStatus.PENDING, index=True),
    )
    error: str = ""
    # Set when the question was also written into the live run's stdin. Nothing
    # here records what the run said back — that lands in its own log.
    escalated_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow, index=True)
    answered_at: datetime | None = None


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
    # Null for workspace-scoped approvals raised by Home Baxter chat.
    ticket_id: str | None = Field(default=None, foreign_key="tickets.id", index=True)
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
    status: ApprovalStatus = Field(
        default=ApprovalStatus.PENDING,
        sa_column=_str_enum_column(ApprovalStatus, ApprovalStatus.PENDING),
    )
    created_at: datetime = Field(default_factory=utcnow)
    resolved_at: datetime | None = None
    # Who/what resolved this approval — "automation" for an auto_approve-mode
    # gate resolution, "" for a human clicking approve in the inbox. Absence of
    # an approvals row must never be the only record of an auto-approval, so
    # every auto-resolved gate still gets a row here, distinguishable from a
    # human sign-off by this column.
    resolved_by: str = ""
    resolving_orchestration_run_id: str | None = Field(
        default=None, foreign_key="orchestration_runs.id", index=True
    )


class TriageMessage(SQLModel, table=True):
    __tablename__ = "triage_messages"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    role: str = Field(index=True)  # user | assistant | system
    content: str = ""
    run_id: str | None = Field(default=None, foreign_key="agent_runs.id")
    # Ordered ChatPart JSON (see chat_primitives). Empty when the turn is plain text.
    parts_json: str = "[]"
    created_at: datetime = Field(default_factory=utcnow)


class DomainEvent(SQLModel, table=True):
    __tablename__ = "domain_events"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    # No default: every event states its own type.
    type: EventType = Field(sa_column=_str_enum_column(EventType))
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


class Skill(SQLModel, table=True):
    __tablename__ = "skills"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    slug: str = Field(index=True, unique=True)
    name: str
    description: str = ""
    body: str = ""
    required_capabilities_json: str = "[]"
    pack_id: str | None = None
    pack_commit: str | None = None
    upstream_name: str | None = None
    version: int = Field(default=1)
    built_in: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class SkillVersion(SQLModel, table=True):
    __tablename__ = "skill_versions"
    __table_args__ = (UniqueConstraint("skill_id", "version"),)

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    skill_id: str = Field(foreign_key="skills.id", index=True)
    version: int
    snapshot_json: str = "{}"
    created_by: str = ""
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


class TicketDependency(SQLModel, table=True):
    """A directed "waits for" edge between two tickets: ``ticket_id`` depends on
    ``depends_on_ticket_id`` and should run after it is complete. Ordering is
    best-effort (it steers subtree run order; it does not hard-block a standalone
    run). Edges are kept acyclic by TicketDependencyService.add_dependency.
    """

    __tablename__ = "ticket_dependencies"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    depends_on_ticket_id: str = Field(foreign_key="tickets.id", index=True)
    created_at: datetime = Field(default_factory=utcnow)
    created_by: str = ""


class TicketRelation(SQLModel, table=True):
    """A symmetric "see also" link between two tickets. Unlike TicketDependency
    it carries no ordering and never blocks: it exists so an operator or agent
    reading one ticket finds the others that share its context.

    Stored once per pair with ``ticket_id < related_ticket_id`` (canonical order,
    enforced by TicketRelationService) so the same pair cannot be inserted twice
    under two spellings.
    """

    __tablename__ = "ticket_relations"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    related_ticket_id: str = Field(foreign_key="tickets.id", index=True)
    created_at: datetime = Field(default_factory=utcnow)
    created_by: str = ""


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
    # Ordered ChatPart JSON (see chat_primitives). Empty when the turn is plain text.
    parts_json: str = "[]"
    # Set on assistant turns so their background lifecycle and approval prompts
    # are tied to the workspace-scoped AgentRun that owns the live CLI process.
    run_id: str | None = Field(default=None, foreign_key="agent_runs.id", index=True)
    created_at: datetime = Field(default_factory=utcnow)


class BaxterChatSession(SQLModel, table=True):
    """One Home Baxter conversation. Many per workspace, listed in the archive."""

    __tablename__ = "baxter_chat_sessions"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    # Derived from the first operator message unless the operator renames it.
    title: str = ""
    runtime_json: str = "{}"
    created_at: datetime = Field(default_factory=utcnow)
    # Ordering key for the archive: bumped by every turn, not by a rename.
    updated_at: datetime = Field(default_factory=utcnow, index=True)


class BaxterChatMessage(SQLModel, table=True):
    """Home Baxter chat message, persisted so a reload does not lose the thread."""

    __tablename__ = "baxter_chat_messages"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    session_id: str = Field(foreign_key="baxter_chat_sessions.id", index=True)
    role: str = Field(index=True)  # user | assistant | system
    content: str = ""
    # pending | complete | failed, as on BranchTriageMessage: the assistant row is
    # written pending before the turn runs, so an interrupted turn is recoverable
    # from the database rather than lost with the request.
    status: str = Field(default="complete", index=True)
    # Ordered ChatPart JSON (see chat_primitives). Empty when the turn is plain text.
    parts_json: str = "[]"
    # The skill the operator picked from the composer's `/` menu for this turn,
    # or "" for an ordinary message. Recorded on the user row because that is
    # whose choice it was; the assistant row is what the choice produced.
    skill_name: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class ComposerNote(SQLModel, table=True):
    """A post-it written from the composer's `/note` command.

    Workspace-scoped rather than session-scoped: a note exists to outlive the
    conversation it was written beside, and "send this into a new chat" is one
    of the two things it can do.
    """

    __tablename__ = "composer_notes"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    body: str = ""
    # When the note was last sent into a conversation, or None while unsent.
    # Kept rather than deleting on send: a note is a draft you may send twice.
    sent_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow, index=True)


class ChatTurnThinking(SQLModel, table=True):
    """The reasoning a chat turn is producing, while it is producing it.

    Keyed by the turn id every chat surface already publishes as
    ``active_turn_id`` — the pending assistant row for Home, branch and studio
    chat, the ``AgentRun`` for ticket triage. One table keyed by that serves all
    four; a column on each of the four message tables would not.

    This row is the durable copy, not the transport. The websocket carries the
    same text as it arrives; a reader who connects late, or reloads mid-turn,
    reads it from here instead of watching an empty panel until the next event.
    It lives exactly as long as the turn: when the turn settles, the transcript
    folds into that message's ``parts_json`` as a thinking part and the row goes.
    """

    __tablename__ = "chat_turn_thinking"

    turn_id: str = Field(primary_key=True)
    #: Reasoning and tool activity, interleaved in arrival order.
    content: str = ""
    #: The reply as it is being written. Transient, unlike ``content``: the
    #: settled message is the real copy, so this is never folded into it — it
    #: exists so a turn that produces no reasoning (read-only turns emit an
    #: empty thinking block) still has something live to show.
    answer: str = ""
    #: The one-line "what is it doing right now" header, e.g. "Read src/app.tsx".
    activity: str = ""
    #: Monotonic within a turn, so a frame that arrives out of order is dropped
    #: by the client rather than rewinding the transcript on screen.
    seq: int = 0
    updated_at: datetime = Field(default_factory=utcnow, index=True)


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
    # Ids of the workspace reference repos this session scopes against, and the
    # survey findings the scoper produced from them (see ReferenceRepo).
    reference_repo_ids_json: str = "[]"
    survey_json: str = "[]"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ReferenceRepo(SQLModel, table=True):
    """A third-party repo cloned locally so the scoper can read it alongside the
    workspace repo. Workspace-scoped and reusable: the clone is cached on disk and
    any number of ticket studio sessions can attach the same row."""

    __tablename__ = "reference_repos"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    url: str = ""
    slug: str = Field(default="", index=True)  # host/owner/name
    name: str = ""
    local_path: str = ""
    default_branch: str = ""
    head_sha: str = ""
    notes: str = ""
    last_synced_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class TicketStudioMessage(SQLModel, table=True):
    __tablename__ = "ticket_studio_messages"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    session_id: str = Field(foreign_key="ticket_studio_sessions.id", index=True)
    role: str = Field(index=True)  # user | assistant | system
    content: str = ""
    # pending | complete | failed, as on BranchTriageMessage and BaxterChatMessage.
    status: str = Field(default="complete", index=True)
    # Which scoper turn this is: chat | clarify | bootstrap_clarify | scope. It
    # selects the prompt AND how the reply is applied to the session, so the
    # background worker can finish a turn it did not start.
    turn_mode: str = ""
    # Ordered ChatPart JSON (see chat_primitives). Empty when the turn is plain text.
    parts_json: str = "[]"
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
    #: Set when the worktree belongs to a ticket rather than to one run. A
    #: ticket's stages share one tree, so reuse is a lookup on this column;
    #: `agent_run_id` stays as provenance for whichever run cut it. Null for
    #: the fan-out and parallel-queue paths, where a run really does want its
    #: own tree.
    ticket_id: str | None = Field(default=None, foreign_key="tickets.id", index=True)
    parent_branch: str = "main"
    worktree_path: str = ""
    state: WorktreeState = Field(
        default=WorktreeState.ACTIVE,
        sa_column=_str_enum_column(WorktreeState, WorktreeState.ACTIVE, index=True),
    )
    #: The branch checked out in this worktree, as opposed to `parent_branch`
    #: (what it was cut from) or the directory name. Merging needs this: the
    #: directory is named after the run, which is not a ref.
    branch: str = ""
    merge_base: str | None = None
    has_conflicts: bool = False
    conflict_files_json: str = "[]"
    conflict_summary: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    merged_at: datetime | None = None
    cleaned_at: datetime | None = None

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
    resolution_successful: bool = False
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
    #: Null on every slot in the shared pool. Capacity belongs to the machine,
    #: not to a workspace — the column survives only for rows written before
    #: migration 0058 collapsed the per-workspace pools.
    workspace_id: str | None = Field(default=None, foreign_key="workspaces.id", index=True)
    #: Unique, because the claim keys on it conceptually and the pool's size is
    #: the machine's concurrency limit. Two threads initialising an empty pool
    #: both inserted a full set, giving six slots for a limit of three — the
    #: admission gate's whole purpose, doubled silently.
    slot_number: int = Field(default=1, unique=True)
    is_available: bool = True
    current_run_id: str | None = Field(default=None, foreign_key="agent_runs.id")
    #: The orchestration occupying this lane. A lane runs a whole ticket, which
    #: spans many agent runs, so this — not `current_run_id` — is what holds the
    #: lane for the duration.
    current_orchestration_run_id: str | None = Field(
        default=None, foreign_key="orchestration_runs.id"
    )
    assigned_at: datetime | None = None
    released_at: datetime | None = None


class QueuedRun(SQLModel, table=True):
    __tablename__ = "queued_runs"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    ticket_id: str = Field(foreign_key="tickets.id", index=True)
    #: Null until this entry starts — a lane entry is a ticket waiting its turn,
    #: and nothing runs on its behalf before then.
    run_id: str | None = Field(default=None, foreign_key="agent_runs.id", index=True)
    orchestration_run_id: str | None = Field(
        default=None, foreign_key="orchestration_runs.id", index=True
    )
    #: Which lane this entry waits in. Each slot is its own serial pipeline.
    slot_number: int = Field(default=1, index=True)
    #: Order *within the lane*, not across the board.
    position: int = 0
    #: Answers from the dialog that queued this, honoured whenever the lane
    #: reaches it — which may be long after that dialog closed.
    auto_approve: bool = False
    stop_at_stage_key: str = ""
    #: "orchestration" (run the ticket) or "stage" (run one stage of it).
    #: Admission control parks both, and they dispatch differently.
    entry_kind: str = "orchestration"
    #: The stage to run, for a "stage" entry.
    stage_key: str = ""
    #: Overrides the caller asked for, held because the entry is the only record
    #: of the ask by the time a lane reaches it. Empty/None means the workspace's
    #: orchestration profile decides.
    driver: str = ""
    max_stages: int | None = None
    #: Max seconds each agent run in this orchestration may take. Null = agent default.
    timeout_seconds: int | None = None
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
    #: When someone acknowledged this entry's blocked/failed outcome on the lane
    #: card. Null while it still needs attention — the lane keeps showing it.
    dismissed_at: datetime | None = None
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


class View(SQLModel, table=True):
    """A user-composed workspace of containers — a flex grid or a canvas.

    Carries no rank of its own: a view's place in the sidebar lives on
    ``SidebarEntry``, which ranks views and pinned built-in pages in one list.
    """

    __tablename__ = "views"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    kind: ViewKind = Field(
        default=ViewKind.FLEX_GRID,
        sa_column=_str_enum_column(ViewKind, ViewKind.FLEX_GRID),
    )
    title: str = ""
    icon: str = ""
    #: A validated `view_layout.ViewLayout`, serialized. Never written unparsed:
    #: a malformed layout is a view that cannot be opened to be repaired.
    layout_json: str = "{}"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class SidebarEntry(SQLModel, table=True):
    """One row of the sidebar, holding either a view or a pinned built-in page.

    One table rather than two so the two kinds share a single ranking — a pinned
    page can sit between two views, and neither kind can drift out of the
    other's ordering.
    """

    __tablename__ = "sidebar_entries"
    __table_args__ = (
        # Pinning a page twice is the same request twice. A select-then-insert
        # cannot see a concurrent peer's uncommitted row, so the rule is the
        # database's. It is expressible as a plain UNIQUE only because a view
        # entry's `page_key` is NULL rather than '': SQLite counts NULLs as
        # distinct, so view entries do not all collide on one blank key.
        UniqueConstraint("workspace_id", "page_key", name="uq_sidebar_entries_page"),
        # Two entries sharing one rank is corruption, not a tie to break at read
        # time. The constraint's backing index is also the composite every query
        # wants — they all filter by workspace first and then order by position.
        UniqueConstraint("workspace_id", "position", name="uq_sidebar_entries_position"),
        # And the same rule for the other half. A view is ranked by exactly one
        # entry: a second one would list the view twice and give it two places in
        # an ordering that is supposed to be total.
        UniqueConstraint("workspace_id", "view_id", name="uq_sidebar_entries_view"),
        # An entry holds a page or a view, never both and never neither. Without
        # this an all-NULL row is legal and renders as nothing, and a both-set
        # row is an entry whose kind the columns disagree about.
        CheckConstraint(
            "(page_key IS NULL) <> (view_id IS NULL)",
            name="ck_sidebar_entries_one_half",
        ),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    workspace_id: str = Field(foreign_key="workspaces.id", index=True)
    position: int = Field(default=0)
    entry_kind: SidebarEntryKind = Field(
        default=SidebarEntryKind.VIEW,
        sa_column=_str_enum_column(SidebarEntryKind, SidebarEntryKind.VIEW),
    )
    #: The built-in page this entry pins, from the frontend's `AppPage` union —
    #: a vocabulary owned by the client, not by the control plane. NULL on a
    #: view entry.  # py-org: allow-string
    page_key: str | None = None
    #: The view this entry ranks, or NULL on a pinned page. NULL rather than ''
    #: because '' is not a view id, and because it is what lets
    #: `uq_sidebar_entries_view` ignore pinned pages instead of colliding every
    #: one of them on a blank. The declared foreign key is enforced (`PRAGMA
    #: foreign_keys=ON`, set on every connection in `db.session`), so this
    #: column can only ever name a view that exists — which is also why a view
    #: and its entry have to be written in that order. The flat wire shape is
    #: `entry_payload`'s job.
    view_id: str | None = Field(default=None, foreign_key="views.id")
