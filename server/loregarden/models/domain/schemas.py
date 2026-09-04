"""API request/response DTOs (non-table SQLModel schemas)."""

from datetime import datetime
from typing import Any

from loregarden.models.domain.enums import (
    CliTool,
    DoctorCheck,
    DoctorStatus,
    EventType,
    ExternalHarness,
    OrchestrationDriver,
    OrchestrationRunStatus,
    RunStatus,
    StageStatus,
    TicketActivity,
    TicketState,
    TicketStudioSessionStatus,
    ToolGrantWarningCode,
    ToolPosture,
    WorkItemType,
)
from pydantic import ConfigDict
from sqlmodel import Field, SQLModel

# --- API DTOs ---


class ClassifyRoute(SQLModel):
    languages: list[str] = Field(default_factory=list)
    specialties: list[str] = Field(default_factory=list)
    agent_id: str
    skill_name: str = ""
    default: bool = False
    # Stage this route branches to, so one template can carry several paths to
    # completion. Distinct from the agent-facing `next_stage_key`, which is
    # rework-only: this branch is declared by the template, not chosen by an agent.
    to_stage: str = ""


class ParallelAgentSpec(SQLModel):
    agent_id: str
    skill_name: str = ""


class WorkflowStageDef(SQLModel):
    key: str
    name: str
    agent_id: str = ""
    skill_name: str = ""
    optional: bool = False
    order: int = 0
    stage_type: str = "agent"  # agent | classify | gate | parallel
    classify_routes: list[ClassifyRoute] = Field(default_factory=list)
    parallel_agents: list[ParallelAgentSpec] = Field(default_factory=list)
    gate_commands: list[str] = Field(default_factory=list)
    gate_required: bool = False
    # Evidence kinds this stage must produce for the current commit before it can
    # pass. Empty means unproven work advances, which is the old behaviour.
    required_evidence: list[str] = Field(default_factory=list)
    # Ends the workflow when reached. Falls back to `key == "done"` for templates
    # authored before this flag existed, including version-pinned instances.
    terminal: bool = False
    # Condition under which this stage is passed over; see SKIP_CONDITIONS.
    skip_when: str = ""
    model: str = ""
    checklist: list[str] = Field(default_factory=list)
    # Template-authored instruction handed to this stage's agent. What the
    # workflow wants from this stage specifically, beyond what its role file
    # says — the seam that lets a duty move off a human gate and onto the stage
    # that should have been doing it.
    stage_brief: str = ""
    # Names a set of stages that are alternatives to one another: a backend and
    # a frontend implementation stage, where a given ticket needs one, the other,
    # or both. Empty means the stage stands alone. The invariant is only that a
    # non-empty group cannot end with every member pruned — there is no
    # exclusivity, because existing templates already run both members happily.
    #
    # It exists because group members must be `optional` to be prunable at all,
    # and `_derive_ticket_state` drops optional stages from the required set. So
    # pruning every member leaves nothing required unresolved, and the ticket
    # derives DONE having implemented nothing.
    alternative_group: str = ""


class WorkflowStageView(SQLModel):
    key: str
    name: str
    status: StageStatus
    order: int = 0
    agent_id: str = ""
    skill_name: str = ""
    optional: bool = False
    note: str = ""
    stage_type: str = "agent"
    agents: list[ParallelAgentSpec] = Field(default_factory=list)
    model: str = ""


class WorkflowTransitionView(SQLModel):
    model_config = ConfigDict(populate_by_name=True)

    from_stage: str = Field(validation_alias="from", serialization_alias="from")
    to: str
    when: str = "default"
    agent_id: str = ""


class WorkspaceRuntimeUpdate(SQLModel):
    cli_adapter: str = "default"
    claude_model: str = ""
    cursor_model: str = ""
    codex_model: str = ""
    lmstudio_base_url: str = ""
    lmstudio_model: str = ""
    lmstudio_max_iterations: int = 0
    opencode_model: str = ""
    claude_effort: str = ""
    cursor_effort: str = ""
    lmstudio_effort: str = ""
    opencode_effort: str = ""


class WorkspaceRuntimeSettings(SQLModel):
    cli_adapter: str = "default"
    claude_model: str = ""
    cursor_model: str = ""
    codex_model: str = ""
    lmstudio_base_url: str = ""
    lmstudio_model: str = ""
    lmstudio_max_iterations: int = 0
    opencode_model: str = ""
    claude_effort: str = ""
    cursor_effort: str = ""
    lmstudio_effort: str = ""
    opencode_effort: str = ""


class TicketSummary(SQLModel):
    id: str
    external_id: str
    # The id this ticket was known by before the restructure. Carried so a
    # surface can still show, or be searched by, the id someone wrote down —
    # blank for anything created since.
    legacy_external_id: str = ""
    title: str
    state: TicketState
    priority: int
    workspace_slug: str
    workflow_stage_key: str
    workflow_stage_status: StageStatus
    workflow_stage_name: str = ""
    run_code: str = ""
    work_item_type: WorkItemType = WorkItemType.TASK
    parent_ticket_id: str | None = None
    milestone: str = ""
    branch: str = ""
    tags: list[str] = []
    child_count: int = 0
    #: The agent this ticket's current stage would dispatch, DERIVED at read
    #: time (`services.stage_agent_view`). Not `next_agent`: that is a pin,
    #: empty for most of a ticket's life, and reading it as a standing fact
    #: showed a stale or blank agent. `next_agent` keeps its meaning on the
    #: input side (CompleteStageRequest, RouteWorkflowRequest), where it is
    #: the pin write.
    current_stage_agent: str = ""
    #: Whether anything is executing on this ticket — orthogonal to ``state``.
    activity: TicketActivity = TicketActivity.IDLE
    stages: list[WorkflowStageView] = []


class TicketStatusSummary(SQLModel):
    """The board in one row: how many tickets sit in each state, and how many
    of them are actually moving.

    The state counts and the activity counts are different axes over the same
    tickets, so they do not sum to each other. ``idle`` is the intersection the
    board could not previously show: in progress, with nothing running.
    """

    backlog: int = 0
    in_progress: int = 0
    blocked: int = 0
    #: Open, and waiting on a person rather than on the pipeline. Counted apart
    #: from `blocked` because parked work holds nothing up — this is the tally
    #: of what a human owes (lg-workflow-integrity-449).
    parked: int = 0
    done: int = 0
    wont_do: int = 0
    #: Across every open ticket, not only the in-progress ones — work can be
    #: dispatched straight from the backlog.
    running: int = 0
    awaiting: int = 0
    queued: int = 0
    #: In progress with no run, no queue entry, and nothing awaiting.
    idle: int = 0


class TicketTreeNode(SQLModel):
    id: str
    external_id: str
    title: str
    state: TicketState
    priority: int
    work_item_type: WorkItemType
    workspace_slug: str = ""
    workflow_stage_name: str = ""
    workflow_stage_status: StageStatus = StageStatus.PENDING
    child_count: int = 0
    children: list["TicketTreeNode"] = []


class TicketDependencyRef(SQLModel):
    """A ticket at one end of an edge (dependency or relation), enough for the UI
    to render it."""

    id: str
    external_id: str
    title: str
    state: TicketState
    work_item_type: WorkItemType
    is_integration_review: bool = False


class TicketDependencyRequest(SQLModel):
    """Add a "this ticket waits for depends_on" edge; accepts a UUID or external_id."""

    depends_on: str


class TicketRelationRequest(SQLModel):
    """Relate this ticket to another (symmetric, non-blocking); accepts a UUID or
    external_id."""

    related_to: str


class TicketDetail(TicketSummary):
    #: Set only on the responses that start work. When the slot pool is full the
    #: request is queued rather than started, and the caller has to be able to
    #: tell those apart — the ticket looks the same either way.
    admission: dict | None = None
    description: str
    acceptance_criteria: list[str]
    #: Tickets this one waits for (its prerequisites) and tickets waiting on it.
    dependencies: list[TicketDependencyRef] = Field(default_factory=list)
    dependents: list[TicketDependencyRef] = Field(default_factory=list)
    #: Tickets linked for context only — symmetric, and never an ordering constraint.
    related: list[TicketDependencyRef] = Field(default_factory=list)
    revision: int
    last_updated_by: str
    next_status: str
    blocking_issues: str
    state_locked: bool = False
    workflow_template_slug: str = ""
    workflow_template_name: str = ""
    workflow_transitions: list[WorkflowTransitionView] = Field(default_factory=list)
    artifacts: dict[str, Any]
    orchestration_runtime: WorkspaceRuntimeSettings = Field(
        default_factory=WorkspaceRuntimeSettings
    )
    #: This ticket's own override ("" = inherit).
    compatibility_posture: str = ""
    #: What actually applies once inheritance is resolved, plus where it came from —
    #: an inherited value is meaningless to the operator without its origin.
    resolved_compatibility_posture: str = ""
    compatibility_posture_source: str = ""


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
    orchestration_profile_slug: str = ""


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
    checklist: list[str] = Field(default_factory=list)
    route_options: list[dict[str, str]] = Field(default_factory=list)
    ticket_id: str
    ticket_external_id: str
    kind: str = "workflow_gate"
    run_id: str = ""
    tool_name: str = ""
    tool_input_json: str = "{}"
    cli_adapter: str = ""


class EventView(SQLModel):
    id: str
    type: EventType
    ticket_id: str | None
    workspace_id: str | None
    payload: dict[str, Any]
    created_at: datetime


class StartRunRequest(SQLModel):
    stage_key: str | None = None
    manual: bool = False
    auto_approve: bool = False
    timeout_seconds: int | None = None
    #: The lane to run in. None means any — the pool picks. A debug run spends
    #: the same machine capacity as any other, so it waits its turn like one.
    slot_number: int | None = None
    #: Dispatch the stage even though it has reached its retry budget. The
    #: breaker exists to stop a stage redispatching itself, not to stop a human
    #: deliberately re-running one; the override is recorded.
    force: bool = False


class HandoffCheckinRequest(SQLModel):
    """Sent by a pasted terminal-handoff command when it starts. The pid is the
    pasting shell's, used for local liveness checks by the stale-run reaper."""

    pid: int


class StartOrchestrationRequest(SQLModel):
    driver: OrchestrationDriver | None = None
    max_stages: int | None = None
    stop_at_stage_key: str | None = None
    auto_approve: bool = False
    #: Max seconds each agent run in this orchestration (and its child tickets)
    #: may take. None keeps each agent's configured default.
    timeout_seconds: int | None = None
    #: The lane to run in. None means any — the pool picks the quietest free
    #: one, and a full pool parks the request in the shortest queue.
    slot_number: int | None = None


class ExternalHarnessPromptRequest(SQLModel):
    """Which outside harness the copied ticket prompt is being written for."""

    harness: ExternalHarness


class ExternalHarnessPromptView(SQLModel):
    harness: ExternalHarness
    ticket_id: str
    external_id: str
    workspace_slug: str
    prompt: str


class ExternalStageRunView(SQLModel):
    """One runnable member of a stage checked out to an external harness.

    A sequential stage yields exactly one of these; a parallel stage yields one
    per member of ``parallel_agents``, each with its own run, agent, skill and
    prompt. Every member shares ``repo_path`` — the tree is resolved once,
    before any of them start, so concurrent members cannot end up in different
    checkouts.
    """

    agent_run_id: str = ""
    run_code: str = ""
    agent_id: str = ""
    skill_name: str = ""
    prompt: str = ""
    repo_path: str = ""
    started_at: datetime | None = None


class ExternalStageView(SQLModel):
    """A stage checked out to an external harness, with the prompts to run it.

    ``runs`` is empty for a stage that runs nothing — a human approval gate, the
    terminal stage that finalized the workflow, or a blocked workflow.
    ``message`` says which, so the harness stops instead of inventing work.
    """

    stage_key: str = ""
    stage_name: str = ""
    parallel: bool = False
    runs: list[ExternalStageRunView] = Field(default_factory=list)
    message: str = ""


class ExternalStageResultView(SQLModel):
    """What one external stage member cost, and where the workflow went next.

    A parallel stage settles one member per call: ``workflow_stage_status``
    stays RUNNING until the last outstanding member is handed back, and
    ``stage_finalized`` says whether this call was that last one.
    """

    agent_run_id: str
    stage_key: str
    status: RunStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float = 0.0
    workflow_stage_key: str = ""
    workflow_stage_status: StageStatus
    ticket_state: TicketState
    blocking_issues: str = ""
    workflow_finished: bool = False
    stage_finalized: bool = True
    outstanding_members: int = 0


class RunUsage(SQLModel):
    """What one agent run consumed, and under what model and effort.

    Every field defaults to ``None``, which means *nobody measured it* — never
    zero. A CLI that printed no usage block, an adapter with no usage surface,
    and a harness that declined to report all leave the field unset, so a run
    of unknown cost can never be summed in as a free one. Only figures an
    executor actually read are stored.

    ``input_tokens`` counts tokens billed at the full input rate. Cache reads
    and cache writes are priced differently and are recorded on their own, so
    the three are disjoint and their sum is the run's whole input context. Each
    adapter's own dialect is normalized to that in ``agents.run_usage``.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    model: str | None = None
    effort: str | None = None

    def measured(self) -> bool:
        """Whether any token figure was reported at all."""
        return any(
            value is not None
            for value in (
                self.input_tokens,
                self.output_tokens,
                self.cache_read_tokens,
                self.cache_write_tokens,
            )
        )


class CompleteStageRequest(SQLModel):
    stage_key: str
    next_agent: str = ""
    next_stage_key: str = ""
    outcome: str = "pass"  # pass | reject
    blocking_issues: str = ""
    advance: bool = True


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
    #: The outside harness that drove this run, when one did. Null means this
    #: control plane's own agents ran it — the comparison baseline.
    external_harness: ExternalHarness | None = None
    status: OrchestrationRunStatus
    current_stage_key: str
    error_message: str
    started_at: datetime | None = None
    finished_at: datetime | None = None


class OrchestrationProfileView(SQLModel):
    slug: str
    name: str
    driver: OrchestrationDriver
    workflow_template: str
    orchestrator_skill: str = ""
    gates_enabled: bool = False
    gates_commands: list[str] = Field(default_factory=list)
    gates_transition_script: str = ""
    max_stages_per_run: int = 0


class GatesConfigUpdate(SQLModel):
    enabled: bool = False
    commands: list[str] = Field(default_factory=list)
    transition_script: str = ""


class GitAutomationView(SQLModel):
    """What a queue is allowed to do with a finished run's work."""

    worktree: bool = True
    commit: bool = False
    push: bool = False
    open_pr: bool = False
    auto_merge: bool = False
    auto_resolve_conflicts: bool = False
    max_conflict_resolve_attempts: int = 2
    base_branch: str = "main"


class GitBoundary(SQLModel):
    """The git state a run executed against: which checkout, on what branch, at
    which commit, over how dirty a tree.

    One value rather than four loose parameters because the interesting uses are
    comparisons — the tree a stage inherited against the tree its predecessor
    attested to — and a comparison of four positional strings is a bug waiting
    for its argument order to be swapped.

    Every field is optional. A repo that cannot be read yields an empty boundary
    rather than an exception: a git read must never be the reason a dispatch
    fails, and an unrecorded boundary is `unknown`, which is not the same claim
    as a mismatch.
    """

    #: The checkout actually used — a worktree path when the run has one, the
    #: workspace root otherwise. A branch and sha say nothing without it.
    repo_path: str = ""
    #: Empty on a detached HEAD, which is a state, not a failure.
    branch: str = ""
    head_sha: str = ""
    #: Paths already dirty when the run started, so a sweep of pre-existing
    #: edits stays attributable to the tree the run inherited.
    dirty_paths: list[str] = Field(default_factory=list)

    @property
    def is_recorded(self) -> bool:
        """Whether this boundary says anything. An empty one is unknown."""
        return bool(self.repo_path and self.head_sha)


class DoctorFinding(SQLModel):
    """One environment check's result.

    Carries its own remediation because the person reading it is usually not the
    person who learned the fix — that knowledge currently lives in CLAUDE.md, in
    agent memory files, and in whoever hit the trap last.
    """

    check: DoctorCheck
    status: DoctorStatus = DoctorStatus.PASS
    #: One line on what was observed.
    finding: str = ""
    #: What to do about it. Empty on a PASS, where there is nothing to do.
    remediation: str = ""

    @property
    def ok(self) -> bool:
        return self.status is not DoctorStatus.FAIL


class AdvanceStageRequest(SQLModel):
    # backend decides transition; optional hint only for logging
    reason: str = ""


class RouteWorkflowRequest(SQLModel):
    from_stage_key: str
    outcome: str = "reject"
    next_stage_key: str = ""
    next_agent: str = ""
    blocking_issues: str = ""


class UpdateTicketRequest(SQLModel):
    # Rejects unknown fields rather than ignoring them. Pydantic's default of
    # dropping extras let PATCH answer 200 to a write it had silently discarded,
    # which cost a debugging session and taught an agent to stash acceptance
    # criteria in the description instead. A field this model lacks should fail
    # loudly at the edge.
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    description: str | None = None
    #: Replaces the stored list; omit to leave it alone, [] to clear it.
    acceptance_criteria: list[str] | None = None
    #: Replaces the stored tags; omit to leave them alone, [] to clear them.
    tags: list[str] | None = None
    state: TicketState | None = None
    #: Move this work item under a different parent. Omit to leave it alone;
    #: "" detaches it (only legal for a milestone). `None` cannot mean "detach"
    #: here because this model uses None for "not supplied" throughout.
    parent_ticket_id: str | None = None
    priority: int | None = None
    branch: str | None = None
    workflow_stage_key: str | None = None
    workflow_stage_status: StageStatus | None = None
    workflow_template_slug: str | None = None
    stage_key: str | None = None
    stage_status: StageStatus | None = None
    stage_updates: dict[str, StageStatus] | None = None
    auto_state: bool | None = None
    #: "" clears the override so the ticket inherits again.
    compatibility_posture: str | None = None
    #: Per-ticket git automation overrides, as {key: value} for the keys that
    #: differ from the workspace policy (see GitAutomationConfig). {} clears the
    #: override so the ticket inherits again — which is not the same as an
    #: override with every flag false.
    git_automation: dict[str, bool | int | str] | None = None


class TicketCreate(SQLModel):
    workspace_slug: str
    title: str
    work_item_type: WorkItemType = WorkItemType.TASK
    parent_ticket_id: str | None = None
    description: str = ""
    acceptance_criteria: list[str] = []
    priority: int = 3
    milestone: str = ""
    external_id: str = ""


class TicketImportFile(SQLModel):
    name: str
    content: str


class TicketImportItem(SQLModel):
    title: str
    work_item_type: WorkItemType = WorkItemType.TASK
    description: str = ""
    acceptance_criteria: list[str] = []
    priority: int = 3
    milestone: str = ""
    external_id: str = ""
    parent_external_id: str = ""
    parent_ticket_id: str | None = None
    source_format: str = ""
    source_label: str = ""
    preview_markdown: str = ""


class TicketImportPreviewRequest(SQLModel):
    workspace_slug: str
    files: list[TicketImportFile]
    mode: str = "smart"  # "smart" or "regular"


class TicketImportPreviewPathsRequest(SQLModel):
    workspace_slug: str
    file_paths: list[str]


class TicketImportPreviewResponse(SQLModel):
    # `exclude_none` was here as a ConfigDict key, which Pydantic v2 has no such
    # setting for: it was stored and ignored, and every None field serialized
    # anyway. Removing it changes nothing observable — omitting None from this
    # response would be a deliberate API change, not a typo fix. Found by
    # re-enabling `typeddict-unknown-key` (547).

    tickets: list[TicketImportItem]
    errors: list[str]
    warnings: list[str]
    total: int
    by_type: dict[str, int]
    formats: list[str]
    show_preview: bool
    mode: str = "regular"
    studio_context: dict[str, Any] | None = None


class TicketImportRequest(SQLModel):
    workspace_slug: str
    tickets: list[TicketImportItem]


class TicketImportResult(SQLModel):
    created_count: int
    ticket_ids: list[str]
    errors: list[str]


class ApprovalAction(SQLModel):
    action: str  # approve | reject
    answers: dict[str, str | list[str]] | None = None
    response: str = ""
    always_allow: bool = False
    allow_for_ticket: bool = False
    allow_for_stage: bool = False
    # Workflow gates only: an explicit earlier stage to route the ticket back
    # to. On approve, sends a passing gate back for formalization (code +
    # tests). On reject, overrides the template's default reject route.
    route_to_stage_key: str = ""


class TriageMessageCreate(SQLModel):
    content: str
    auto_approve: bool = False


class BtwQuestionCreate(SQLModel):
    content: str


class StudioGateCheck(SQLModel):
    kind: str = "workflow_gate"  # workflow_gate | ac_review | human_approval
    title: str = ""
    impact: str = ""


class StudioHandoffCheck(SQLModel):
    kind: str = "mcp_complete"  # mcp_complete | blocking_clear | custom
    prompt: str = ""


class StudioAgentToolGrants(SQLModel):
    """What tools an agent may reach, beyond which of them run unattended.

    Distinct from the auto-approve policy in ``services.tool_policy``: that
    answers "may this run without asking", this answers "is this offered at
    all". A tool withheld here never reaches the permission bridge, so it
    produces no approval row and no telemetry — which is why the default is
    ``INHERIT`` and why ``analyze_tool_grants`` exists to say what a narrowing
    would cost.

    ``mcp_servers`` names registered servers; empty means every enabled one,
    which is the behaviour that predates this model.
    """

    posture: ToolPosture = ToolPosture.INHERIT
    allowed_tools: list[CliTool] = Field(default_factory=list)
    disallowed_tools: list[CliTool] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)


class ToolGrantWarning(SQLModel):
    """A configured grant that will not do what it looks like it does."""

    code: ToolGrantWarningCode
    message: str
    tools: list[str] = Field(default_factory=list)


class StudioAgentCreate(SQLModel):
    slug: str
    name: str
    description: str = ""
    role_body: str = ""
    adapter: str = "claude"
    default_model: str = ""
    timeout: int = 600
    default_skill: str = ""
    mcp_enabled: bool = True
    mcp_tools: list[str] = Field(default_factory=list)
    gate_checks: list[StudioGateCheck] = Field(default_factory=list)
    handoff_checks: list[StudioHandoffCheck] = Field(default_factory=list)
    tool_grants: StudioAgentToolGrants = Field(default_factory=StudioAgentToolGrants)


class StudioAgentUpdate(SQLModel):
    name: str | None = None
    description: str | None = None
    role_body: str | None = None
    adapter: str | None = None
    default_model: str | None = None
    timeout: int | None = None
    default_skill: str | None = None
    mcp_enabled: bool | None = None
    mcp_tools: list[str] | None = None
    gate_checks: list[StudioGateCheck] | None = None
    handoff_checks: list[StudioHandoffCheck] | None = None
    tool_grants: StudioAgentToolGrants | None = None
    change_note: str | None = None


class StudioAgentView(SQLModel):
    id: str
    slug: str
    name: str
    description: str
    role_body: str
    role_file: str = ""
    adapter: str
    default_model: str = ""
    timeout: int
    default_skill: str
    mcp_enabled: bool
    mcp_tools: list[str]
    gate_checks: list[StudioGateCheck]
    handoff_checks: list[StudioHandoffCheck]
    tool_grants: StudioAgentToolGrants
    # Required, not defaulted: an empty list must mean "checked, nothing wrong",
    # never "nobody computed this". A default here would let a view built by a
    # forgetful call site report a clean bill of health it never established.
    tool_grant_warnings: list[ToolGrantWarning]
    built_in: bool = False
    read_only: bool = False
    version: int = 1
    created_at: datetime
    updated_at: datetime


class StudioAgentVersionView(SQLModel):
    version: int
    created_by: str = ""
    change_note: str = ""
    created_at: datetime
    snapshot: StudioAgentView | None = None


class StudioSkillCreate(SQLModel):
    slug: str
    name: str | None = None
    description: str | None = None
    body: str = ""
    markdown: str | None = None
    required_capabilities: list[str] = Field(default_factory=list)
    pack_id: str | None = None
    pack_commit: str | None = None
    upstream_name: str | None = None
    created_by: str = "studio-ui"
    change_note: str = ""


class StudioSkillUpdate(SQLModel):
    name: str | None = None
    description: str | None = None
    body: str | None = None
    markdown: str | None = None
    required_capabilities: list[str] | None = None
    pack_id: str | None = None
    pack_commit: str | None = None
    upstream_name: str | None = None
    created_by: str = "studio-ui"
    change_note: str


class StudioSkillView(SQLModel):
    id: str
    slug: str
    name: str
    description: str
    body: str
    required_capabilities: list[str]
    pack_id: str | None = None
    pack_commit: str | None = None
    upstream_name: str | None = None
    built_in: bool = False
    read_only: bool = False
    version: int = 1
    created_at: datetime
    updated_at: datetime


class StudioSkillVersionView(SQLModel):
    version: int
    created_by: str = ""
    change_note: str = ""
    created_at: datetime
    snapshot: StudioSkillView | None = None


class StudioSkillRestore(SQLModel):
    created_by: str = "studio-ui"
    change_note: str


class StudioMcpToolGuide(SQLModel):
    name: str
    description: str
    when_to_use: str
    example: str
    orchestrator_only: bool = False
    stage_agent: bool = True


class StudioAgentPreviewProfile(SQLModel):
    description: str = ""
    model: str = ""
    provider: str = ""
    default_skill: str = ""
    timeout: int = 0
    always_apply: bool | None = None


class StudioAgentPreview(SQLModel):
    name: str = ""
    markdown: str
    sections: list[str]
    profile: StudioAgentPreviewProfile = Field(default_factory=StudioAgentPreviewProfile)


class StudioAgentPreviewRequest(SQLModel):
    slug: str = ""
    name: str = "Preview Agent"
    description: str = ""
    role_body: str = ""
    adapter: str = "claude"
    timeout: int = 600
    default_skill: str = ""
    mcp_enabled: bool = True
    mcp_tools: list[str] = Field(default_factory=list)
    gate_checks: list[StudioGateCheck] = Field(default_factory=list)
    handoff_checks: list[StudioHandoffCheck] = Field(default_factory=list)


class StudioWorkflowStage(SQLModel):
    key: str
    name: str
    stage_type: str = "agent"
    agent_id: str = ""
    skill_name: str = ""
    optional: bool = False
    order: int = 0
    gate_required: bool = False
    terminal: bool = False
    skip_when: str = ""
    classify_routes: list[ClassifyRoute] = Field(default_factory=list)
    parallel_agents: list[ParallelAgentSpec] = Field(default_factory=list)
    gate_commands: list[str] = Field(default_factory=list)
    model: str = ""
    # Carried so a publish round-trips them. A field WorkflowStageDef has and this
    # model does not is silently dropped the first time a template is published
    # from Studio — which is how `terminal` and `skip_when` were once lost, and
    # how a live template's evidence gates would be.
    required_evidence: list[str] = Field(default_factory=list)
    checklist: list[str] = Field(default_factory=list)
    stage_brief: str = ""
    #: See `WorkflowStageDef.alternative_group`.
    alternative_group: str = ""


class StudioWorkflowCreate(SQLModel):
    slug: str
    name: str
    description: str = ""
    stages: list[StudioWorkflowStage] = Field(default_factory=list)
    transitions: list[dict[str, str]] = Field(default_factory=list)


class StudioWorkflowUpdate(SQLModel):
    name: str | None = None
    description: str | None = None
    stages: list[StudioWorkflowStage] | None = None
    transitions: list[dict[str, str]] | None = None
    change_note: str | None = None


class StudioWorkflowView(SQLModel):
    id: str
    slug: str
    name: str
    description: str
    stages: list[StudioWorkflowStage]
    transitions: list[dict[str, str]]
    published_template_id: str | None = None
    published_template_slug: str = ""
    built_in: bool = False
    source_path: str = ""
    read_only: bool = False
    version: int = 1
    created_at: datetime
    updated_at: datetime


class StudioWorkflowVersionView(SQLModel):
    version: int
    created_by: str = ""
    change_note: str = ""
    created_at: datetime
    snapshot: StudioWorkflowView | None = None


class StudioGenerateRequest(SQLModel):
    description: str


class StudioGeneratedAgent(SQLModel):
    name: str
    slug: str = ""
    description: str = ""
    role_body: str = ""
    adapter: str = "claude"
    default_skill: str = ""
    mcp_tools: list[str] = Field(default_factory=list)


class StudioGeneratedWorkflow(SQLModel):
    name: str
    slug: str = ""
    description: str = ""
    stages: list[StudioWorkflowStage] = Field(default_factory=list)


class RunMessageCreate(SQLModel):
    content: str


class TicketStudioDraftItem(SQLModel):
    ref: str
    work_item_type: WorkItemType
    parent_ref: str | None = None
    title: str
    description: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    priority: int = 3
    # Workflow this item should run, "" to inherit the workspace default. Per
    # item so one commit can mix shapes — a feature planned in full while its
    # tasks, already scoped here, take a shorter path.
    workflow_template_slug: str = ""
    selected: bool = True


class TicketStudioSurveyFinding(SQLModel):
    """One part of a reference repo the scoper thinks is worth pulling over."""

    ref: str
    title: str
    repo_slug: str = ""
    source_paths: list[str] = Field(default_factory=list)
    what_it_gives: str = ""
    fit: str = ""
    risks: str = ""
    # adopt (port largely as-is) | adapt (rework to our shape) | inspire (idea
    # only, own implementation) | skip (looked, not worth it)
    verdict: str = "adapt"
    effort: str = ""  # S | M | L
    selected: bool = False


class TicketStudioSurveyUpdate(SQLModel):
    findings: list[TicketStudioSurveyFinding]


class TicketStudioReferenceReposUpdate(SQLModel):
    reference_repo_ids: list[str]


class ReferenceRepoCreate(SQLModel):
    workspace_slug: str
    url: str
    notes: str = ""


class ReferenceRepoView(SQLModel):
    id: str
    workspace_slug: str
    url: str
    slug: str
    name: str
    local_path: str
    default_branch: str = ""
    head_sha: str = ""
    notes: str = ""
    cloned: bool = False
    last_synced_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TicketStudioSessionCreate(SQLModel):
    workspace_slug: str
    title: str
    brief: str = ""
    parent_ticket_id: str | None = None
    is_preview: bool = False
    imported_tickets: list[dict[str, Any]] = Field(default_factory=list)
    reference_repo_ids: list[str] = Field(default_factory=list)


class TicketStudioSessionUpdate(SQLModel):
    title: str | None = None
    brief: str | None = None
    parent_ticket_id: str | None = None


class TicketStudioDraftUpdate(SQLModel):
    items: list[TicketStudioDraftItem]


class TicketStudioClarificationsUpdate(SQLModel):
    answers: list[str]


class TicketStudioMessageCreate(SQLModel):
    content: str


class TicketStudioSessionView(SQLModel):
    id: str
    workspace_slug: str
    title: str
    brief: str
    parent_ticket_id: str | None = None
    parent_ticket_title: str = ""
    status: TicketStudioSessionStatus
    summary: str = ""
    clarifying_questions: list[str] = Field(default_factory=list)
    clarifying_answers: list[str] = Field(default_factory=list)
    clarifying_resolved: bool = True
    draft: list[TicketStudioDraftItem] = Field(default_factory=list)
    messages: list[dict[str, Any]] = Field(default_factory=list)
    #: A dump of `WorkspaceRuntimeSettings`, which is no longer all strings —
    #: `lmstudio_max_iterations` is a bounded count. Typed `str | int` rather than
    #: `Any` so the widening says what actually varies.
    runtime: dict[str, str | int] = Field(default_factory=dict)
    is_preview: bool = False
    imported_tickets: list[dict[str, Any]] = Field(default_factory=list)
    # idle | running. Server-derived, so a reload mid-turn still shows the
    # scoper working instead of an idle panel that silently changes later.
    run_status: str = "idle"
    active_turn_id: str | None = None
    reference_repos: list[ReferenceRepoView] = Field(default_factory=list)
    survey: list[TicketStudioSurveyFinding] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class TicketStudioCommitResult(SQLModel):
    session_id: str
    created_ticket_ids: list[str]
    created_count: int
    breakdown: dict[str, int] = Field(default_factory=dict)
    root_ticket_id: str | None = None


class HierarchyWorkItem(SQLModel):
    external_id: str
    title: str
    work_item_type: WorkItemType
    description: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    priority: int = 3
    parent_ticket_id: str | None = None
    children: list["HierarchyWorkItem"] = Field(default_factory=list)


class FinalizeHierarchyRequest(SQLModel):
    workspace_slug: str
    hierarchy: list[HierarchyWorkItem]


class FinalizeHierarchyResponse(SQLModel):
    created_ids: list[str]
    total_created: int


class McpServerCreate(SQLModel):
    name: str
    description: str = ""
    transport: str = "http"
    url: str = ""
    command: str = ""
    args: list[str] = Field(default_factory=list)
    #: Name of an environment variable holding the credential — never the value.
    auth_env_var: str = ""
    enabled: bool = True
    #: "prompt" or "auto" — see services.tool_policy.
    tool_policy: str = "prompt"
    #: Calls per minute before further calls are refused. 0 means no ceiling.
    rate_limit_per_min: int = 0


class McpServerUpdate(SQLModel):
    name: str | None = None
    description: str | None = None
    transport: str | None = None
    url: str | None = None
    command: str | None = None
    args: list[str] | None = None
    auth_env_var: str | None = None
    enabled: bool | None = None
    tool_policy: str | None = None
    rate_limit_per_min: int | None = None


class McpServerView(SQLModel):
    id: str
    name: str
    description: str
    transport: str
    url: str
    command: str
    args: list[str]
    auth_env_var: str
    enabled: bool
    #: Whether that environment variable is actually set in this process. Lets
    #: the UI show "credential missing" without ever reading the value.
    auth_present: bool = False
    tool_policy: str = "prompt"
    #: 0 means no ceiling.
    rate_limit_per_min: int = 0
    #: Empty means never checked — distinct from checked-and-failing, and the
    #: UI says so rather than showing a server as healthy by default.
    last_checked_at: str = ""
    last_health_ok: bool = False
    last_health_latency_ms: int = 0
    last_health_error: str = ""
    #: Tools the server reported when it was last checked.
    tools: list[str] = Field(default_factory=list)
    #: Empty means the tools were never listed — distinct from a server that
    #: answered `tools/list` with nothing.
    tools_listed_at: str = ""
    created_at: datetime
    updated_at: datetime


HierarchyWorkItem.model_rebuild()
TicketTreeNode.model_rebuild()
