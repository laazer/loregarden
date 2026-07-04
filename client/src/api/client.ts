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
  branch: string;
  run_code: string;
  work_item_type: WorkItemType;
  parent_ticket_id: string | null;
  cycle_id: string | null;
  cycle_name: string;
  milestone: string;
  child_count: number;
}

export interface TicketTreeNode {
  id: string;
  external_id: string;
  title: string;
  state: TicketState;
  priority: number;
  work_item_type: WorkItemType;
  workflow_stage_name: string;
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
  stages: WorkflowStageView[];
  artifacts: {
    diff?: DiffArtifact | null;
    logs?: LogLine[];
    live?: string | null;
    tests?: TestArtifact | null;
    context?: ContextSection[];
  };
}

export interface DiffArtifact {
  file: string;
  add: string;
  del: string;
  files: string;
  lines: { type: string; ln: string; text: string }[];
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
}

export interface WorkflowTemplateSummary {
  id: string;
  slug: string;
  name: string;
  description: string;
  stage_count: number;
}

export interface WorkspaceWorkflow {
  template_slug: string;
  template_name: string;
  stages: { key: string; name: string; agent_id: string; skill_name: string; optional: boolean }[];
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
}

export interface CycleSummary {
  id: string;
  name: string;
  status: string;
  goal: string;
  workspace_slug: string;
  ticket_count: number;
}

function ticketQuery(params?: {
  workspace?: string;
  state?: TicketState;
  work_item_type?: WorkItemType;
  parent_ticket_id?: string;
  roots_only?: boolean;
  cycle_id?: string;
  milestone?: string;
  search?: string;
}) {
  const q = new URLSearchParams();
  if (params?.workspace) q.set("workspace", params.workspace);
  if (params?.state) q.set("state", params.state);
  if (params?.work_item_type) q.set("work_item_type", params.work_item_type);
  if (params?.parent_ticket_id) q.set("parent_ticket_id", params.parent_ticket_id);
  if (params?.roots_only) q.set("roots_only", "true");
  if (params?.cycle_id) q.set("cycle_id", params.cycle_id);
  if (params?.milestone) q.set("milestone", params.milestone);
  if (params?.search) q.set("search", params.search);
  const suffix = q.toString() ? `?${q}` : "";
  return suffix;
}

export const api = {
  workspaces: () => request<WorkspaceSummary[]>("/api/workspaces"),
  workspaceWorkflow: (slug: string) => request<WorkspaceWorkflow>(`/api/workspaces/${slug}/workflow`),
  workflowTemplates: () => request<WorkflowTemplateSummary[]>("/api/workflows/templates"),
  setWorkspaceTemplate: (slug: string, workflow_template_slug: string) =>
    request(`/api/workspaces/${slug}/workflow`, {
      method: "PATCH",
      body: JSON.stringify({ workflow_template_slug }),
    }),
  exportProjectBoard: () =>
    request<{ exported: number; paths: string[] }>("/api/export/project-board", { method: "POST" }),
  runs: (ticketId?: string) => {
    const q = ticketId ? `?ticket_id=${ticketId}` : "";
    return request<{ id: string; run_code: string; status: string; command: string }[]>(`/api/runs${q}`);
  },
  tickets: (params?: {
    workspace?: string;
    state?: TicketState;
    work_item_type?: WorkItemType;
    cycle_id?: string;
    search?: string;
  }) => request<TicketSummary[]>(`/api/tickets${ticketQuery(params)}`),
  ticketTree: (params?: {
    workspace?: string;
    state?: TicketState;
    work_item_type?: WorkItemType;
    cycle_id?: string;
    search?: string;
  }) => request<TicketTreeNode[]>(`/api/tickets/tree${ticketQuery(params)}`),
  cycles: (workspace?: string) => {
    const q = workspace ? `?workspace=${encodeURIComponent(workspace)}` : "";
    return request<CycleSummary[]>(`/api/cycles${q}`);
  },
  ticket: (id: string) => request<TicketDetail>(`/api/tickets/${id}`),
  updateTicket: (
    id: string,
    body: {
      state?: TicketState;
      workflow_stage_key?: string;
      workflow_stage_status?: StageStatus;
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
  startRun: (id: string) => request<TicketDetail>(`/api/tickets/${id}/start`, { method: "POST", body: "{}" }),
  advance: (id: string) => request<TicketDetail>(`/api/tickets/${id}/advance`, { method: "POST", body: "{}" }),
  approvals: () => request<Approval[]>("/api/inbox/approvals"),
  resolveApproval: (id: string, action: "approve" | "reject") =>
    request(`/api/inbox/approvals/${id}`, {
      method: "POST",
      body: JSON.stringify({ action }),
    }),
};
