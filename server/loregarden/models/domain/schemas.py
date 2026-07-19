"""API request/response DTOs (non-table SQLModel schemas)."""

from datetime import datetime
from typing import Any

from loregarden.models.domain.enums import (
    EventType,
    OrchestrationDriver,
    OrchestrationRunStatus,
    StageStatus,
    TicketState,
    TicketStudioSessionStatus,
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
    lmstudio_base_url: str = ""
    lmstudio_model: str = ""


class WorkspaceRuntimeSettings(SQLModel):
    cli_adapter: str = "default"
    claude_model: str = ""
    cursor_model: str = ""
    lmstudio_base_url: str = ""
    lmstudio_model: str = ""


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
    run_code: str = ""
    work_item_type: WorkItemType = WorkItemType.TASK
    parent_ticket_id: str | None = None
    milestone: str = ""
    branch: str = ""
    child_count: int = 0
    next_agent: str = ""
    stages: list[WorkflowStageView] = []


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


class TicketDetail(TicketSummary):
    description: str
    acceptance_criteria: list[str]
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


class StartOrchestrationRequest(SQLModel):
    driver: OrchestrationDriver | None = None
    max_stages: int | None = None
    stop_at_stage_key: str | None = None
    auto_approve: bool = False


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
    title: str | None = None
    description: str | None = None
    state: TicketState | None = None
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
    model_config = ConfigDict(exclude_none=True)

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


class StudioGateCheck(SQLModel):
    kind: str = "workflow_gate"  # workflow_gate | ac_review | human_approval
    title: str = ""
    impact: str = ""


class StudioHandoffCheck(SQLModel):
    kind: str = "mcp_complete"  # mcp_complete | blocking_clear | custom
    prompt: str = ""


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


class TicketStudioDraftItem(SQLModel):
    ref: str
    work_item_type: WorkItemType
    parent_ref: str | None = None
    title: str
    description: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    priority: int = 3
    suggested_agent: str = ""
    # Workflow this item should run, "" to inherit the workspace default. Per
    # item so one commit can mix shapes — a feature planned in full while its
    # tasks, already scoped here, take a shorter path.
    workflow_template_slug: str = ""
    selected: bool = True


class TicketStudioSessionCreate(SQLModel):
    workspace_slug: str
    title: str
    brief: str = ""
    parent_ticket_id: str | None = None
    is_preview: bool = False
    imported_tickets: list[dict[str, Any]] = Field(default_factory=list)


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
    runtime: dict[str, str] = Field(default_factory=dict)
    is_preview: bool = False
    imported_tickets: list[dict[str, Any]] = Field(default_factory=list)
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


HierarchyWorkItem.model_rebuild()
TicketTreeNode.model_rebuild()
