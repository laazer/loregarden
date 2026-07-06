export const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json() as Promise<T>;
}

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
  agent_id: string;
  skill_name: string;
  optional: boolean;
  note: string;
  stage_type: string;
  agents: { agent_id: string; skill_name: string }[];
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

export interface DiffFileSection {
  path: string;
  add: number;
  del: number;
  lines: DiffLine[];
}

export interface DiffArtifact {
  file: string;
  add: string;
  del: string;
  files: string;
  range?: string;
  sections?: DiffFileSection[];
  /** @deprecated legacy flat diff — use sections */
  lines?: DiffLine[];
}

export interface LogLine {
  time: string;
  tag: string;
  text: string;
}

export interface TestArtifact {
  summary: string;
  color?: string;
  cmd: string;
  rows: { name: string; status: string; dur: string; msg?: string }[];
}

export interface ContextSection {
  title: string;
  rows: { k: string; v: string }[];
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
  memory_sqlite_url: string;
  database_url: string;
}

export interface MemoryStatus {
  enabled: boolean;
  workspace_slug: string | null;
  obsidian_vault: string | null;
  obsidian_memory_dir: string | null;
  obsidian_learnings_dir: string | null;
  memory_sqlite_path: string | null;
  memory_sqlite_in_icloud: boolean;
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
  stages: { key: string; name: string; agent_id: string; skill_name: string; optional: boolean }[];
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

export interface TriageSnapshot {
  pending_approvals: Approval[];
  recent_approvals: Approval[];
  messages: TriageMessage[];
  runtime: WorkspaceRuntimeSettings;
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
  timeout: number;
  default_skill: string;
  mcp_enabled: boolean;
  mcp_tools: string[];
  gate_checks: StudioGateCheck[];
  handoff_checks: StudioHandoffCheck[];
  built_in: boolean;
  read_only?: boolean;
  created_at: string;
  updated_at: string;
}

export interface StudioMcpToolGuide {
  name: string;
  description: string;
  when_to_use: string;
  example: string;
  orchestrator_only: boolean;
  stage_agent: boolean;
}

export interface StudioAgentPreview {
  markdown: string;
  sections: string[];
}

export interface StudioDefaults {
  mcp_tools: string[];
  gate_checks: StudioGateCheck[];
  handoff_checks: StudioHandoffCheck[];
}

export interface ClassifyRoute {
  languages: string[];
  specialties: string[];
  agent_id: string;
  skill_name: string;
  default: boolean;
}

export interface StudioWorkflowStage {
  key: string;
  name: string;
  stage_type: "agent" | "classify" | "gate";
  agent_id: string;
  skill_name: string;
  optional: boolean;
  order: number;
  gate_required: boolean;
  classify_routes: ClassifyRoute[];
}

export interface StudioWorkflow {
  id: string;
  slug: string;
  name: string;
  description: string;
  stages: StudioWorkflowStage[];
  transitions: { from: string; to: string }[];
  published_template_id: string | null;
  published_template_slug: string;
  built_in?: boolean;
  source_path?: string;
  read_only?: boolean;
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
}

export interface UsageSnapshot {
  providers: UsageProviderSnapshot[];
  near_limit: boolean;
  warnings: string[];
  fetched_at: string;
}

function ticketQuery(params?: {
  workspace?: string;
  state?: TicketState | TicketState[];
  work_item_type?: WorkItemType | WorkItemType[];
  parent_ticket_id?: string;
  roots_only?: boolean;
  milestone?: string;
  search?: string;
}) {
  const q = new URLSearchParams();
  if (params?.workspace) q.set("workspace", params.workspace);
  const states = params?.state
    ? Array.isArray(params.state)
      ? params.state
      : [params.state]
    : [];
  for (const state of states) q.append("state", state);
  const workItemTypes = params?.work_item_type
    ? Array.isArray(params.work_item_type)
      ? params.work_item_type
      : [params.work_item_type]
    : [];
  for (const workItemType of workItemTypes) q.append("work_item_type", workItemType);
  if (params?.parent_ticket_id) q.set("parent_ticket_id", params.parent_ticket_id);
  if (params?.roots_only) q.set("roots_only", "true");
  if (params?.milestone) q.set("milestone", params.milestone);
  if (params?.search) q.set("search", params.search);
  const suffix = q.toString() ? `?${q}` : "";
  return suffix;
}

export const api = {
  workspaces: () => request<WorkspaceSummary[]>("/api/workspaces"),
  createWorkspace: (body: WorkspaceCreateRequest) =>
    request<WorkspaceCreateResponse>("/api/workspaces", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  browseDirectory: (path?: string) => {
    const q = path ? `?path=${encodeURIComponent(path)}` : "";
    return request<BrowseDirectoryResponse>(`/api/system/browse${q}`);
  },
  browseImportDirectory: (path?: string) => {
    const q = path ? `?path=${encodeURIComponent(path)}` : "";
    return request<BrowseImportDirectoryResponse>(`/api/system/browse-import${q}`);
  },
  workspaceWorkflow: (slug: string) => request<WorkspaceWorkflow>(`/api/workspaces/${slug}/workflow`),
  workflowTemplates: () => request<WorkflowTemplateSummary[]>("/api/workflows/templates"),
  setWorkspaceTemplate: (slug: string, workflow_template_slug: string) =>
    request(`/api/workspaces/${slug}/workflow`, {
      method: "PATCH",
      body: JSON.stringify({ workflow_template_slug }),
    }),
  runtimeOptions: () => request<RuntimeOptions>("/api/workspaces/runtime-options"),
  workspaceRuntime: (slug: string) =>
    request<WorkspaceRuntimeSettings>(`/api/workspaces/${slug}/runtime`),
  setWorkspaceRuntime: (slug: string, body: WorkspaceRuntimeSettings) =>
    request<WorkspaceRuntimeSettings>(`/api/workspaces/${slug}/runtime`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  exportProjectBoard: () =>
    request<{ exported: number; paths: string[] }>("/api/export/project-board", { method: "POST" }),
  runs: (ticketId?: string) => {
    const q = ticketId ? `?ticket_id=${ticketId}` : "";
    return request<
      {
        id: string;
        run_code: string;
        status: string;
        command: string;
        agent_id: string;
        stage_key: string;
        stderr: string;
      }[]
    >(`/api/runs${q}`);
  },
  tickets: (params?: {
    workspace?: string;
    state?: TicketState | TicketState[];
    work_item_type?: WorkItemType | WorkItemType[];
    search?: string;
  }) => request<TicketSummary[]>(`/api/tickets${ticketQuery(params)}`),
  ticketTree: (params?: {
    workspace?: string;
    state?: TicketState | TicketState[];
    work_item_type?: WorkItemType | WorkItemType[];
    search?: string;
  }) => request<TicketTreeNode[]>(`/api/tickets/tree${ticketQuery(params)}`),
  ticket: (id: string) => request<TicketDetail>(`/api/tickets/${id}`),
  createTicket: (body: CreateTicketRequest) =>
    request<TicketDetail>("/api/tickets", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  previewTicketImport: (body: { workspace_slug: string; files: TicketImportFile[] }) =>
    request<TicketImportPreviewResponse>("/api/tickets/import/preview", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  previewTicketImportPaths: (body: { workspace_slug: string; file_paths: string[] }) =>
    request<TicketImportPreviewResponse>("/api/tickets/import/preview-paths", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  importTickets: (body: { workspace_slug: string; tickets: TicketImportItem[] }) =>
    request<TicketImportResult>("/api/tickets/import", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateTicket: (
    id: string,
    body: {
      title?: string;
      description?: string;
      state?: TicketState;
      workflow_stage_key?: string;
      workflow_stage_status?: StageStatus;
      workflow_template_slug?: string;
      branch?: string;
      stage_key?: string;
      stage_status?: StageStatus;
      stage_updates?: Record<string, StageStatus>;
      auto_state?: boolean;
    },
  ) =>
    request<TicketDetail>(`/api/tickets/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  orchestrationProfile: (slug: string) =>
    request<OrchestrationProfileView>(`/api/orchestration/workspaces/${slug}/profile`),
  startRun: (id: string, options?: { stage_key?: string }) =>
    request<TicketDetail>(`/api/tickets/${id}/start`, {
      method: "POST",
      body: JSON.stringify({ manual: true, stage_key: options?.stage_key }),
    }),
  orchestrate: (
    id: string,
    body?: { max_stages?: number; stop_at_stage_key?: string; auto_approve?: boolean },
  ) =>
    request<TicketDetail>(`/api/tickets/${id}/orchestrate`, {
      method: "POST",
      body: JSON.stringify(body ?? {}),
    }),
  openPr: (id: string) =>
    request<TicketDetail>(`/api/tickets/${id}/open-pr`, {
      method: "POST",
      body: "{}",
    }),
  advance: (id: string) => request<TicketDetail>(`/api/tickets/${id}/advance`, { method: "POST", body: "{}" }),
  approvals: (ticketId?: string) =>
    request<Approval[]>(
      ticketId ? `/api/inbox/approvals?ticket_id=${encodeURIComponent(ticketId)}` : "/api/inbox/approvals",
    ),
  resolveApproval: (
    id: string,
    body: {
      action: "approve" | "reject";
      answers?: Record<string, string | string[]>;
      response?: string;
      always_allow?: boolean;
      allow_for_ticket?: boolean;
      allow_for_stage?: boolean;
    },
  ) =>
    request(`/api/inbox/approvals/${id}`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  triage: (ticketId: string) => request<TriageSnapshot>(`/api/tickets/${ticketId}/triage`),
  setTriageRuntime: (ticketId: string, body: WorkspaceRuntimeSettings) =>
    request<WorkspaceRuntimeSettings>(`/api/tickets/${ticketId}/triage/runtime`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  sendTriageMessage: (ticketId: string, content: string) =>
    request<{ user_message: TriageMessage; assistant_message: TriageMessage }>(
      `/api/tickets/${ticketId}/triage/messages`,
      {
        method: "POST",
        body: JSON.stringify({ content }),
      },
    ),
  skills: () => request<string[]>("/api/agents/skills"),
  studioMcpTools: () => request<string[]>("/api/studio/mcp-tools"),
  studioMcpToolGuides: () => request<StudioMcpToolGuide[]>("/api/studio/mcp-tool-guides"),
  studioDefaults: () => request<StudioDefaults>("/api/studio/defaults"),
  previewStudioAgent: (body: Partial<StudioAgent> & { name: string }) =>
    request<StudioAgentPreview>("/api/studio/agents/preview", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  studioAgents: () => request<StudioAgent[]>("/api/studio/agents"),
  studioAgent: (slug: string) => request<StudioAgent>(`/api/studio/agents/${slug}`),
  createStudioAgent: (body: Partial<StudioAgent> & { slug: string; name: string }) =>
    request<StudioAgent>("/api/studio/agents", { method: "POST", body: JSON.stringify(body) }),
  updateStudioAgent: (slug: string, body: Partial<StudioAgent>) =>
    request<StudioAgent>(`/api/studio/agents/${slug}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteStudioAgent: (slug: string) =>
    request<{ ok: boolean }>(`/api/studio/agents/${slug}`, { method: "DELETE" }),
  studioWorkflows: () => request<StudioWorkflow[]>("/api/studio/workflows"),
  studioWorkflow: (slug: string) => request<StudioWorkflow>(`/api/studio/workflows/${slug}`),
  createStudioWorkflow: (body: {
    slug: string;
    name: string;
    description?: string;
    stages: StudioWorkflowStage[];
    transitions?: { from: string; to: string }[];
  }) => request<StudioWorkflow>("/api/studio/workflows", { method: "POST", body: JSON.stringify(body) }),
  updateStudioWorkflow: (
    slug: string,
    body: Partial<{ name: string; description: string; stages: StudioWorkflowStage[]; transitions: { from: string; to: string }[] }>,
  ) =>
    request<StudioWorkflow>(`/api/studio/workflows/${slug}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteStudioWorkflow: (slug: string) =>
    request<{ ok: boolean }>(`/api/studio/workflows/${slug}`, { method: "DELETE" }),
  publishStudioWorkflow: (slug: string) =>
    request<StudioWorkflow>(`/api/studio/workflows/${slug}/publish`, { method: "POST" }),
  usage: () => request<UsageSnapshot>("/api/usage"),
  memoryConfig: () => request<MemoryConfigResponse>("/api/memory/config"),
  setMemoryConfig: (body: MemoryConfigSettings) =>
    request<MemoryConfigResponse>("/api/memory/config", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  memoryStatus: () => request<MemoryStatus>("/api/memory/status"),
  editorRefs: (slug: string, contextRoot?: string) => {
    const q = new URLSearchParams();
    if (contextRoot) q.set("context_root", contextRoot);
    const suffix = q.toString() ? `?${q}` : "";
    return request<EditorRefsResponse>(`/api/workspaces/${slug}/editor/refs${suffix}`);
  },
  editorCheckout: (slug: string, body: { branch?: string; worktree_path?: string }) =>
    request<EditorRefsResponse>(`/api/workspaces/${slug}/editor/checkout`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  editorBrowse: (slug: string, path?: string, contextRoot?: string) => {
    const q = new URLSearchParams();
    if (path) q.set("path", path);
    if (contextRoot) q.set("context_root", contextRoot);
    const suffix = q.toString() ? `?${q}` : "";
    return request<EditorBrowseResponse>(`/api/workspaces/${slug}/editor/browse${suffix}`);
  },
  editorReadFile: (slug: string, path: string, contextRoot?: string) => {
    const q = new URLSearchParams({ path });
    if (contextRoot) q.set("context_root", contextRoot);
    return request<EditorFileResponse>(`/api/workspaces/${slug}/editor/file?${q}`);
  },
  editorWriteFile: (
    slug: string,
    body: { path: string; content: string; context_root?: string },
  ) =>
    request<{ path: string; saved: boolean; size: number }>(`/api/workspaces/${slug}/editor/file`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
};
