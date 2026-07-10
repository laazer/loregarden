export const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    let message = text || res.statusText;
    try {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed.detail === "string") message = parsed.detail;
    } catch {
      // response wasn't JSON — fall back to raw text
    }
    throw new ApiError(res.status, message);
  }
  return res.json() as Promise<T>;
}


export type * from "./types";
import type {
  TicketState,
  StageStatus,
  WorkItemType,
  TicketSummary,
  TicketTreeNode,
  TicketDetail,
  WorkspaceSummary,
  RuntimeOptions,
  WorkspaceRuntimeSettings,
  WorkflowTemplateSummary,
  WorkspaceCreateRequest,
  WorkspaceCreateResponse,
  EditorBrowseResponse,
  EditorRefsResponse,
  EditorFileResponse,
  BrowseDirectoryResponse,
  BrowseImportDirectoryResponse,
  MemoryConfigSettings,
  MemoryStatus,
  MemoryConfigResponse,
  OrchestrationProfileView,
  WorkspaceWorkflow,
  Approval,
  TriageMessage,
  TriageSnapshot,
  StudioAgent,
  StudioMcpToolGuide,
  StudioAgentPreview,
  StudioDefaults,
  StudioGeneratedAgent,
  StudioGeneratedWorkflow,
  StudioWorkflowStage,
  StudioWorkflow,
  CreateTicketRequest,
  TicketImportFile,
  TicketImportItem,
  TicketImportPreviewResponse,
  TicketImportResult,
  TicketStudioCommitResult,
  TicketStudioDraftItem,
  TicketStudioSession,
  UsageSnapshot,
  CIStatusResponse,
} from "./types";


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
        workspace_id?: string;
        stdout?: string;
        stderr?: string;
      }[]
    >(`/api/runs${q}`);
  },
  run: (runId: string) =>
    request<{
      id: string;
      run_code: string;
      status: string;
      command: string;
      agent_id: string;
      stage_key: string;
      workspace_id: string;
      stdout: string;
      stderr: string;
    }>(`/api/runs/${runId}`),
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
  deleteTicket: (id: string) =>
    request<{ ok: boolean }>(`/api/tickets/${id}`, { method: "DELETE" }),
  orchestrationProfile: (slug: string) =>
    request<OrchestrationProfileView>(`/api/orchestration/workspaces/${slug}/profile`),
  startRun: (id: string, options?: { stage_key?: string; auto_approve?: boolean }) =>
    request<TicketDetail>(`/api/tickets/${id}/start`, {
      method: "POST",
      body: JSON.stringify({
        manual: true,
        stage_key: options?.stage_key,
        auto_approve: options?.auto_approve,
      }),
    }),
  buildTerminalHandoffCommand: (id: string, stageKey: string) =>
    request<{ run_id: string; adapter: string; command: string }>(
      `/api/tickets/${id}/terminal_handoff_command`,
      {
        method: "POST",
        body: JSON.stringify({ stage_key: stageKey }),
      },
    ),
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
  commitPush: (id: string) =>
    request<TicketDetail>(`/api/tickets/${id}/commit-push`, {
      method: "POST",
      body: "{}",
    }),
  advance: (id: string) => request<TicketDetail>(`/api/tickets/${id}/advance`, { method: "POST", body: "{}" }),
  routeWorkflow: (
    id: string,
    body: {
      from_stage_key: string;
      outcome?: "pass" | "reject";
      next_stage_key?: string;
      next_agent?: string;
      blocking_issues?: string;
    },
  ) =>
    request<TicketDetail>(`/api/tickets/${id}/route-workflow`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
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
  generateStudioAgent: (description: string) =>
    request<StudioGeneratedAgent>("/api/studio/agents/generate", {
      method: "POST",
      body: JSON.stringify({ description }),
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
  generateStudioWorkflow: (description: string) =>
    request<StudioGeneratedWorkflow>("/api/studio/workflows/generate", {
      method: "POST",
      body: JSON.stringify({ description }),
    }),
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
  ticketStudioSessions: (workspace?: string) => {
    const q = workspace ? `?workspace=${encodeURIComponent(workspace)}` : "";
    return request<TicketStudioSession[]>(`/api/ticket-studio/sessions${q}`);
  },
  createTicketStudioSession: (body: {
    workspace_slug: string;
    title: string;
    brief?: string;
    parent_ticket_id?: string | null;
  }) =>
    request<TicketStudioSession>("/api/ticket-studio/sessions", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  ticketStudioSession: (id: string) => request<TicketStudioSession>(`/api/ticket-studio/sessions/${id}`),
  updateTicketStudioSession: (
    id: string,
    body: { title?: string; brief?: string; parent_ticket_id?: string | null },
  ) =>
    request<TicketStudioSession>(`/api/ticket-studio/sessions/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteTicketStudioSession: (id: string) =>
    request<{ ok: boolean }>(`/api/ticket-studio/sessions/${id}`, { method: "DELETE" }),
  setTicketStudioRuntime: (id: string, body: WorkspaceRuntimeSettings) =>
    request<WorkspaceRuntimeSettings>(`/api/ticket-studio/sessions/${id}/runtime`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  updateTicketStudioDraft: (id: string, items: TicketStudioDraftItem[]) =>
    request<TicketStudioSession>(`/api/ticket-studio/sessions/${id}/draft`, {
      method: "PATCH",
      body: JSON.stringify({ items }),
    }),
  sendTicketStudioMessage: (id: string, content: string) =>
    request<TicketStudioSession>(`/api/ticket-studio/sessions/${id}/messages`, {
      method: "POST",
      body: JSON.stringify({ content }),
    }),
  requestTicketStudioClarifications: (id: string) =>
    request<TicketStudioSession>(`/api/ticket-studio/sessions/${id}/clarify`, { method: "POST" }),
  saveTicketStudioClarifications: (id: string, answers: string[]) =>
    request<TicketStudioSession>(`/api/ticket-studio/sessions/${id}/clarifications`, {
      method: "PATCH",
      body: JSON.stringify({ answers }),
    }),
  generateTicketStudioScope: (id: string) =>
    request<TicketStudioSession>(`/api/ticket-studio/sessions/${id}/scope`, { method: "POST" }),
  commitTicketStudioSession: (id: string) =>
    request<TicketStudioCommitResult>(`/api/ticket-studio/sessions/${id}/commit`, { method: "POST" }),
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
  ciStatus: (ticketId: string) => request<CIStatusResponse>(`/api/ci/status/${ticketId}`),
  triggerAutoFix: (ticketId: string) =>
    request<{ status: string; attempt_id?: string; attempt_number?: number; message?: string }>(
      `/api/ci/trigger-auto-fix/${ticketId}`,
      { method: "POST" },
    ),
  skipCICheck: (ticketId: string) =>
    request<{ status: string; message: string }>(`/api/ci/manual-override/${ticketId}`, {
      method: "POST",
    }),
};
