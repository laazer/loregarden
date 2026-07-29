/** Chat UI primitives — TS mirror of server chat_primitives.py (v1). */

export const CHAT_PRIMITIVES_VERSION = 1;

export type PrimitiveKind =
  | "text"
  | "thinking"
  | "ticket"
  | "ticket_workflow"
  | "parent_ticket"
  | "ticket_list"
  | "status_column"
  | "kanban"
  | "filterable_kanban"
  | "agent"
  | "workflow"
  | "gate"
  | "terminal"
  | "edit"
  | "calendar"
  | "calendar_event"
  | "workspace"
  | "todo_list"
  | "branch_history"
  | "commit"
  | "qa"
  | "giphy";

export interface TextPart {
  primitive: "text";
  content: string;
}

export interface ThinkingPart {
  primitive: "thinking";
  content: string;
  collapsed?: boolean;
}

export interface TicketPart {
  primitive: "ticket";
  ticket_id: string;
  title?: string | null;
}

export interface TicketWorkflowPart {
  primitive: "ticket_workflow";
  ticket_id: string;
  title?: string | null;
}

export interface ParentTicketPart {
  primitive: "parent_ticket";
  ticket_id: string;
  title?: string | null;
}

export interface TicketListPart {
  primitive: "ticket_list";
  ticket_ids?: string[];
  parent_ticket_id?: string | null;
  title?: string | null;
}

export interface StatusColumnPart {
  primitive: "status_column";
  status: string;
  ticket_ids?: string[];
  title?: string | null;
}

export interface KanbanPart {
  primitive: "kanban";
  ticket_ids?: string[];
  statuses?: string[];
  title?: string | null;
}

export interface FilterableKanbanPart {
  primitive: "filterable_kanban";
  ticket_ids?: string[];
  statuses?: string[];
  filters?: string[];
  title?: string | null;
}

export interface AgentPart {
  primitive: "agent";
  agent_id?: string | null;
  slug?: string | null;
  draft?: Record<string, unknown> | null;
  title?: string | null;
}

export interface WorkflowPart {
  primitive: "workflow";
  workflow_slug?: string | null;
  draft?: Record<string, unknown> | null;
  title?: string | null;
}

export interface GatePart {
  primitive: "gate";
  ticket_id?: string | null;
  stage_key?: string | null;
  draft?: Record<string, unknown> | null;
  title?: string | null;
}

export interface TerminalLine {
  kind?: "command" | "stdout" | "stderr" | "status";
  text: string;
}

export interface TerminalPart {
  primitive: "terminal";
  title?: string;
  lines?: TerminalLine[];
  cwd?: string | null;
}

export interface EditPart {
  primitive: "edit";
  target?: "agent" | "workflow" | "gate" | "terminal" | "text";
  target_id?: string | null;
  language?: string;
  content?: string;
  title?: string | null;
}

export interface CalendarEventItem {
  id?: string | null;
  title: string;
  starts_at: string;
  ends_at?: string | null;
  kind?: "cron" | "scheduled" | "one_time" | "plan" | "run";
  ticket_id?: string | null;
  description?: string | null;
}

export interface CalendarPart {
  primitive: "calendar";
  view?: "month" | "week" | "day";
  focus_date?: string | null;
  events?: CalendarEventItem[];
  title?: string | null;
}

export interface CalendarEventPart {
  primitive: "calendar_event";
  event: CalendarEventItem;
}

export interface WorkspacePart {
  primitive: "workspace";
  workspace_slug: string;
  title?: string | null;
}

export interface TodoItem {
  id: string;
  text: string;
  checked?: boolean;
}

export interface TodoListPart {
  primitive: "todo_list";
  owner?: "agent" | "user";
  items?: TodoItem[];
  title?: string | null;
}

export interface BranchHistoryPart {
  primitive: "branch_history";
  workspace_slug: string;
  branch: string;
  limit?: number;
  title?: string | null;
}

export interface CommitPart {
  primitive: "commit";
  workspace_slug: string;
  sha: string;
  branch?: string | null;
  title?: string | null;
}

export interface QAItem {
  id: string;
  question: string;
  answer?: string;
}

export interface QAPart {
  primitive: "qa";
  items?: QAItem[];
  title?: string | null;
  prompt?: string | null;
  interactive?: boolean;
}

export interface GiphyPart {
  primitive: "giphy";
  giphy_id?: string | null;
  url?: string | null;
  alt?: string;
  title?: string | null;
  caption?: string | null;
}

export type ChatPart =
  | TextPart
  | ThinkingPart
  | TicketPart
  | TicketWorkflowPart
  | ParentTicketPart
  | TicketListPart
  | StatusColumnPart
  | KanbanPart
  | FilterableKanbanPart
  | AgentPart
  | WorkflowPart
  | GatePart
  | TerminalPart
  | EditPart
  | CalendarPart
  | CalendarEventPart
  | WorkspacePart
  | TodoListPart
  | BranchHistoryPart
  | CommitPart
  | QAPart
  | GiphyPart;

export type UnknownPart = { primitive: string; [key: string]: unknown };

export function isChatPart(value: unknown): value is ChatPart {
  return (
    typeof value === "object" &&
    value !== null &&
    "primitive" in value &&
    typeof (value as { primitive: unknown }).primitive === "string"
  );
}
