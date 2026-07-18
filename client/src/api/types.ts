// Shared API type definitions for the Loregarden client.
// Split out of client.ts so the module boundary separates data shapes from calls.

export type TicketState = "backlog" | "in_progress" | "blocked" | "done" | "wont_do";
export type StageStatus = "pending" | "running" | "blocked" | "awaiting" | "done" | "wont_do";
export type WorkItemType = "milestone" | "feature" | "capability" | "task" | "bug";

export interface TicketSummary {
  id: string;
  external_id: string;
  title: string;
  state: TicketState;
  priority: number;
  workspace_slug: string;
  workflow_stage_key: string;
  workflow_stage_status: StageStatus;
  workflow_stage_name: string;
  run_code: string;
  work_item_type: WorkItemType;
  parent_ticket_id: string | null;
  milestone: string;
  branch: string;
  child_count: number;
}

export interface TicketTreeNode {
  id: string;
  external_id: string;
  title: string;
  state: TicketState;
  priority: number;
  work_item_type: WorkItemType;
  workspace_slug?: string;
  workflow_stage_name: string;
  workflow_stage_status: StageStatus;
  child_count: number;
  children: TicketTreeNode[];
}

export interface WorkflowStageView {
  key: string;
  name: string;
  status: StageStatus;
  order?: number;
  agent_id: string;
  skill_name: string;
  optional: boolean;
  note: string;
  stage_type: string;
  agents: { agent_id: string; skill_name: string }[];
  model?: string;
}

export interface WorkflowTransition {
  from: string;
  to: string;
  when?: string;
  agent_id?: string;
}

export interface TicketDetail extends TicketSummary {
  description: string;
  acceptance_criteria: string[];
  revision: number;
  last_updated_by: string;
  next_agent: string;
  next_status: string;
  blocking_issues: string;
  state_locked: boolean;
  workflow_template_slug: string;
  workflow_template_name: string;
  workflow_transitions?: WorkflowTransition[];
  /** This ticket's own override; "" means inherit. */
  compatibility_posture?: string;
  /** What applies after inheritance, and where it came from. */
  resolved_compatibility_posture?: string;
  compatibility_posture_source?: string;
  stages: WorkflowStageView[];
  artifacts: {
    diff?: DiffArtifact | null;
    logs?: LogLine[];
    live?: string | null;
    tests?: TestArtifact | null;
    context?: ContextSection[];
    error?: RunErrorArtifact | null;
    pr?: {
      url: string;
      number: string;
      title: string;
      branch: string;
      body: string;
    } | null;
  };
  orchestration_runtime?: WorkspaceRuntimeSettings;
}

export interface RunErrorArtifact {
  message: string;
  run_code: string;
  agent_id: string;
  stage_key: string;
  command: string;
}

export interface DiffLine {
  type: string;
  ln: string;
  text: string;
}

export interface DiffFileEntry {
  path: string;
  add: number;
  del: number;
}

export interface DiffFileSection {
  path: string;
  add: number;
  del: number;
  lines: DiffLine[];
  truncated?: boolean;
}

export interface DiffArtifact {
  file: string;
  add: string;
  del: string;
  files: string;
  range?: string;
  file_entries?: DiffFileEntry[];
  sections?: DiffFileSection[];
  /** @deprecated legacy flat diff — use sections */
  lines?: DiffLine[];
}

export interface LogLine {
  time: string;
  tag: string;
  text: string;
}

/** One run's rendered log, as served by GET /api/runs/{id}/log. */
export interface RunLog {
  id: string;
  run_code: string;
  agent_id: string;
  skill_name: string;
  stage_key: string;
  status: string;
  command: string;
  started_at: string | null;
  finished_at: string | null;
  /** Empty for runs that predate the log streamer. */
  lines: LogLine[];
  live: string | null;
  stderr: string;
}

export interface TestArtifact {
  summary: string;
  color?: string;
  cmd: string;
  rows: { name: string; status: string; dur: string; msg?: string }[];
}

export interface ContextSection {
  // Absent on stage reports, which the Context tab renders per-stage instead.
  title?: string;
  rows?: { k: string; v: string }[];
  stage_key?: string;
  status?: string;
  confidence?: number;
  reroute_to_stage?: string | null;
  reroute_context?: string;
}

export interface WorkspaceSummary {
  id: string;
  slug: string;
  name: string;
  repo_path: string;
  repo_root: string;
  repo_exists: boolean;
  ticket_count: number;
  blocked_count: number;
  workflow_template_slug: string;
  cli_adapter: string;
  claude_model: string;
  cursor_model: string;
  lmstudio_base_url: string;
  lmstudio_model: string;
}

export interface RuntimeOption {
  id: string;
  label: string;
}

export interface RuntimeOptions {
  cli_adapters: RuntimeOption[];
  claude_models: RuntimeOption[];
  cursor_models: RuntimeOption[];
}

export interface WorkspaceRuntimeSettings {
  cli_adapter: string;
  claude_model: string;
  cursor_model: string;
  lmstudio_base_url: string;
  lmstudio_model: string;
}

export interface WorkflowTemplateSummary {
  id: string;
  slug: string;
  name: string;
  description: string;
  stage_count: number;
}

export interface WorkspaceCreateRequest {
  slug: string;
  name: string;
  workflow_template_slug?: string;
  repo_path?: string;
  orchestration_profile_slug?: string;
}

export interface WorkspaceCreateResponse {
  id: string;
  slug: string;
  name: string;
  workflow_template_slug: string;
}

export interface BrowseDirectoryEntry {
  name: string;
  path: string;
  repo_path: string;
}

export interface BrowseImportEntry extends BrowseDirectoryEntry {
  kind: "directory" | "file";
}

export interface EditorBrowseEntry extends BrowseImportEntry {}

export interface EditorBrowseResponse {
  current_path: string;
  repo_path: string;
  parent_path: string | null;
  parent_repo_path: string | null;
  context_root: string;
  context_path: string;
  entries: EditorBrowseEntry[];
}

export interface EditorBranchRef {
  name: string;
  current: boolean;
}

export interface EditorWorktreeRef {
  path: string;
  branch: string;
  label: string;
  current: boolean;
  repo_path: string;
}

export interface EditorRefsResponse {
  workspace_root: string;
  context_root: string;
  context_path: string;
  current_branch: string;
  branches: EditorBranchRef[];
  worktrees: EditorWorktreeRef[];
}

export interface EditorFileResponse {
  path: string;
  content: string;
  language: string;
  size: number;
}

export interface BrowseDirectoryResponse {
  current_path: string;
  repo_path: string;
  parent_path: string | null;
  repo_root: string;
  entries: BrowseDirectoryEntry[];
}

export interface BrowseImportDirectoryResponse extends Omit<BrowseDirectoryResponse, "entries"> {
  entries: BrowseImportEntry[];
}

export interface MemoryConfigSettings {
  icloud_root: string;
  obsidian_vault_dir: string;
  obsidian_memory_subdir: string;
  obsidian_learnings_subdir: string;
  obsidian_blogposts_subdir: string;
  obsidian_checkpoints_subdir: string;
  memory_sqlite_url: string;
  database_url: string;
}

export interface MemoryStatus {
  enabled: boolean;
  workspace_slug: string | null;
  obsidian_vault: string | null;
  obsidian_memory_dir: string | null;
  obsidian_learnings_dir: string | null;
  obsidian_blogposts_dir: string | null;
  obsidian_checkpoints_dir: string | null;
  memory_sqlite_path: string | null;
  memory_sqlite_in_icloud: boolean;
  memory_graph_tables: string[];
  memory_graph_node_types: string[];
  memory_graph_excludes: string[];
  database_path: string;
}

export interface MemoryConfigResponse {
  config: MemoryConfigSettings;
  status: MemoryStatus;
  defaults: {
    icloud_root: string | null;
    mobile_documents_dir: string | null;
    obsidian_icloud_dir: string | null;
    obsidian_documents_dir: string | null;
  };
}

export interface OrchestrationProfileView {
  slug: string;
  name: string;
  driver: string;
  workflow_template: string;
  orchestrator_skill: string;
  gates_enabled: boolean;
  gates_commands: string[];
  gates_transition_script: string;
  max_stages_per_run: number;
}

export interface OrchestrationRunView {
  id: string;
  run_code: string;
  ticket_id: string;
  driver: string;
  profile_slug: string;
  status: string;
  current_stage_key: string;
  error_message: string;
}

export interface WorkspaceWorkflow {
  template_slug: string;
  template_name: string;
  stages: { key: string; name: string; agent_id: string; skill_name: string; optional: boolean; order?: number }[];
  transitions: WorkflowTransition[];
}

export interface AgentQuestionOption {
  label: string;
  description?: string;
}

export interface AgentQuestion {
  question: string;
  header?: string;
  multiSelect?: boolean;
  options: AgentQuestionOption[];
}

export interface Approval {
  id: string;
  title: string;
  level: string;
  workspace_slug: string;
  stage_key: string;
  stage_name: string;
  impact: string;
  checklist?: string[];
  route_options?: { key: string; name: string }[];
  ticket_id: string;
  ticket_external_id: string;
  kind: "workflow_gate" | "cli_permission" | "cli_question";
  status?: string;
  run_id: string;
  tool_name: string;
  tool_input_json: string;
  cli_adapter: string;
  questions?: AgentQuestion[];
  resolved_answers?: Record<string, string | string[]> | null;
  created_at?: string;
  resolved_at?: string;
}

export interface TriageMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
}

export type TriageRunStatus = "idle" | "running" | "awaiting_input";

export interface TriageSnapshot {
  pending_approvals: Approval[];
  recent_approvals: Approval[];
  messages: TriageMessage[];
  runtime: WorkspaceRuntimeSettings;
  run_status: TriageRunStatus;
  active_run_id: string | null;
}

export interface TriageSendResult {
  user_message: TriageMessage;
  run_id: string;
  status: "queued";
}

export interface StudioGateCheck {
  kind: string;
  title: string;
  impact: string;
}

export interface StudioHandoffCheck {
  kind: string;
  prompt: string;
}

export interface StudioAgent {
  id: string;
  slug: string;
  name: string;
  description: string;
  role_body: string;
  role_file?: string;
  adapter: string;
  default_model: string;
  timeout: number;
  default_skill: string;
  mcp_enabled: boolean;
  mcp_tools: string[];
  gate_checks: StudioGateCheck[];
  handoff_checks: StudioHandoffCheck[];
  built_in: boolean;
  read_only?: boolean;
  version?: number;
  created_at: string;
  updated_at: string;
}

export interface StudioAgentVersion {
  version: number;
  created_by: string;
  change_note: string;
  created_at: string;
  snapshot?: StudioAgent | null;
}

export interface StudioWorkflowVersion {
  version: number;
  created_by: string;
  change_note: string;
  created_at: string;
  snapshot?: StudioWorkflow | null;
}

export interface StudioMcpToolGuide {
  name: string;
  description: string;
  when_to_use: string;
  example: string;
  orchestrator_only: boolean;
  stage_agent: boolean;
}

export interface StudioAgentPreviewProfile {
  description: string;
  model: string;
  provider: string;
  default_skill: string;
  timeout: number;
  always_apply: boolean | null;
}

export interface StudioAgentPreview {
  name: string;
  markdown: string;
  sections: string[];
  profile: StudioAgentPreviewProfile;
}

export interface StudioDefaults {
  mcp_tools: string[];
  gate_checks: StudioGateCheck[];
  handoff_checks: StudioHandoffCheck[];
}

export interface StudioGeneratedAgent {
  name: string;
  slug: string;
  description: string;
  role_body: string;
  adapter: string;
  default_skill: string;
  mcp_tools: string[];
}

export interface StudioGeneratedWorkflow {
  name: string;
  slug: string;
  description: string;
  stages: StudioWorkflowStage[];
}

export interface ClassifyRoute {
  languages: string[];
  specialties: string[];
  agent_id: string;
  skill_name: string;
  default: boolean;
  /** Stage this route branches to. Empty means continue to the next stage in order. */
  to_stage?: string;
}

export interface ParallelAgentSpec {
  agent_id: string;
  skill_name: string;
}

export interface StudioWorkflowStage {
  key: string;
  name: string;
  stage_type: "agent" | "classify" | "gate" | "parallel";
  agent_id: string;
  skill_name: string;
  optional: boolean;
  order: number;
  gate_required: boolean;
  classify_routes: ClassifyRoute[];
  parallel_agents: ParallelAgentSpec[];
  model: string;
}

export interface StudioWorkflow {
  id: string;
  slug: string;
  name: string;
  description: string;
  stages: StudioWorkflowStage[];
  transitions: WorkflowTransition[];
  published_template_id: string | null;
  published_template_slug: string;
  built_in?: boolean;
  source_path?: string;
  read_only?: boolean;
  version?: number;
  created_at: string;
  updated_at: string;
}

export interface CreateTicketRequest {
  workspace_slug: string;
  title: string;
  work_item_type: WorkItemType;
  parent_ticket_id?: string | null;
  description?: string;
  acceptance_criteria?: string[];
  priority?: number;
  milestone?: string;
  external_id?: string;
}

export interface TicketImportFile {
  name: string;
  content: string;
}

export interface TicketImportItem {
  title: string;
  work_item_type: WorkItemType;
  description?: string;
  acceptance_criteria?: string[];
  priority?: number;
  milestone?: string;
  external_id?: string;
  parent_external_id?: string;
  parent_ticket_id?: string | null;
  source_format?: string;
  source_label?: string;
  preview_markdown?: string;
}

export interface TicketImportPreviewResponse {
  tickets: TicketImportItem[];
  errors: string[];
  warnings: string[];
  total: number;
  by_type: Record<string, number>;
  formats: string[];
  show_preview: boolean;
  mode?: string;
  studio_context?: Record<string, unknown> | null;
}

export interface TicketImportResult {
  created_count: number;
  ticket_ids: string[];
  errors: string[];
}

export type UsageMeterStatus = "ok" | "warning" | "critical";

export interface UsageMeter {
  key: string;
  label: string;
  used: number;
  limit: number | null;
  unit: "percent" | "dollars" | string;
  percent_used: number | null;
  resets_at: string | null;
  status: UsageMeterStatus;
}

export interface UsageBreakdownItem {
  name: string;
  amount: number;
  unit: string;
  share_percent: number;
}

export interface UsageProviderSnapshot {
  provider: "claude" | "cursor";
  plan: string | null;
  logged_in: boolean;
  error: string | null;
  meters: UsageMeter[];
  breakdown: UsageBreakdownItem[];
  from_cache: boolean;
  cached_at: string | null;
}

export interface UsageSnapshot {
  providers: UsageProviderSnapshot[];
  near_limit: boolean;
  warnings: string[];
  fetched_at: string;
}

export interface ImportedTicket {
  external_id: string;
  title: string;
  description?: string;
  work_item_type: WorkItemType;
  acceptance_criteria?: string[];
  priority?: 1 | 2 | 3;
  source_workspace?: string;
}

export interface TicketStudioDraftItem {
  ref: string;
  work_item_type: WorkItemType;
  parent_ref: string | null;
  title: string;
  description: string;
  acceptance_criteria: string[];
  priority: number;
  suggested_agent: string;
  selected: boolean;
}

export interface TicketStudioMessage {
  id: string;
  role: string;
  content: string;
  display_content?: string;
  created_at: string;
}

export interface TicketStudioSession {
  id: string;
  workspace_slug: string;
  title: string;
  brief: string;
  parent_ticket_id: string | null;
  parent_ticket_title: string;
  status: "draft" | "committed";
  summary: string;
  clarifying_questions: string[];
  clarifying_answers: string[];
  clarifying_resolved: boolean;
  draft: TicketStudioDraftItem[];
  messages: TicketStudioMessage[];
  runtime: WorkspaceRuntimeSettings;
  is_preview: boolean;
  imported_tickets: ImportedTicket[];
  created_at: string;
  updated_at: string;
}

export interface TicketStudioCommitResult {
  session_id: string;
  created_ticket_ids: string[];
  created_count: number;
  breakdown: Record<string, number>;
  root_ticket_id: string | null;
}

export type CIStatusValue = "pending" | "passing" | "failing" | "partial" | "skipped";
export type AutoFixStatusValue = "pending" | "running" | "succeeded" | "failed";

export interface CIRunResult {
  id: string;
  workspace_id: string;
  ticket_id: string;
  status: CIStatusValue;
  provider: string;
  external_run_id: string | null;
  logs_url: string | null;
  failure_summary: string | null;
  full_logs: string | null;
  created_at: string;
  updated_at: string;
}

export interface AutoFixAttempt {
  id: string;
  ci_run_result_id: string;
  attempt_number: number;
  run_id: string | null;
  status: AutoFixStatusValue;
  result_summary: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface CIStatusResponse {
  ci_status: CIRunResult | null;
  auto_fix_history: AutoFixAttempt[];
}

export interface ReloadStatus {
  workspace_slug: string;
  supported: boolean;
  ready: boolean;
  blockers: string[];
  active_agent_runs: { id: string; run_code: string; stage_key: string }[];
  active_orchestrations: { id: string; run_code: string }[];
  running_workflow_tickets: { ticket_id: string; external_id: string }[];
}
