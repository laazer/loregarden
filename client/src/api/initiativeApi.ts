import type { ChatMessageView } from "../components/chat/chatUtils";
import { request } from "./http";
import type { TicketDetail, TicketState, WorkItemType, WorkspaceRuntimeSettings } from "./types";

/** A ticket an initiative view lists, tagged with the workspace it lives in. */
export interface InitiativeItem {
  id: string;
  external_id: string;
  title: string;
  state: TicketState;
  workspace_slug: string;
  work_item_type: WorkItemType;
}

/** A top-level ticket of an initiative — a milestone, a sprint's feature or bug,
 * or a member it tracks without parenting. */
export interface InitiativeMilestone extends InitiativeItem {
  /** Tracked by membership: it keeps its own parent, milestone and branch. */
  member: boolean;
  /** For a member, the milestone it lives under; "" when none, and for children. */
  home_milestone: string;
}

export type SuggestionKind = "theme" | "sprint";

/** Open work a suggestion may claim. */
export interface SuggestedItem extends InitiativeItem {
  /** The milestone a feature or bug would leave; blank for a milestone. */
  from_milestone: string;
  /** Open work items it carries, in the unit pace is measured in. */
  cost: number;
}

export interface InitiativeSuggestion {
  key: string;
  kind: SuggestionKind;
  title: string;
  description: string;
  rationale: string;
  items: SuggestedItem[];
  /** Milestones this takes every open feature and bug out of; they roll up as done. */
  empties: string[];
  /** A sprint's last day (`YYYY-MM-DD`); null for a theme. */
  target_date: string | null;
}

export interface InitiativeSuggestionSet {
  source: "heuristic" | "agent";
  suggestions: InitiativeSuggestion[];
  /** Open milestones without an initiative that no suggestion claimed. */
  ungrouped: SuggestedItem[];
  sprint: {
    days: number;
    /** Work items the measured pace finishes in `days`; null when nothing was measured. */
    capacity: number | null;
    /** Open work items already directly under an initiative, charged before this sprint. */
    committed: number;
    planned: number;
    basis: ForecastBasis;
  };
  /** What the agent proposed that could not be used. */
  warnings: string[];
  generated_at: string;
}

export interface InitiativeDraft {
  title: string;
  description: string;
  item_ids: string[];
  /** Set as the initiative's plan target. */
  target_date: string | null;
}

export interface CreatedInitiative {
  id: string;
  external_id: string;
  title: string;
  attached: number;
}

export interface InitiativeView {
  id: string;
  external_id: string;
  title: string;
  description: string;
  state: TicketState;
  priority: number;
  milestones: InitiativeMilestone[];
  /** `resolved` counts done and wont_do milestones — the rollup's own rule. */
  progress: { resolved: number; total: number };
  workspaces: string[];
}

export type ScheduleMode = "fixed" | "rolling";
export type ScheduleStatus = "done" | "on_track" | "behind" | "late" | "unscheduled" | "no_forecast";
export type ForecastBasis =
  | "initiative_throughput"
  | "workspace_throughput"
  | "agent_time"
  | "none";

/** Dates are ISO `YYYY-MM-DD` strings, as the API sends them. */
export interface MilestoneSchedule {
  id: string;
  external_id: string;
  title: string;
  state: TicketState;
  workspace_slug: string;
  work_item_type: WorkItemType;
  /** A member the initiative tracks, rather than a child it parents. */
  member: boolean;
  plan_order: number;
  target_date: string | null;
  /** When its last open item lands, scheduled through the dependency graph. */
  forecast_date: string | null;
  /** Target in a fixed plan, forecast in a rolling one. */
  planned_date: string | null;
  /** Forecast minus target in days; positive is late. */
  drift_days: number | null;
  status: ScheduleStatus;
  basis: ForecastBasis;
  remaining: number;
  total: number;
  /** Open items by graph status. */
  counts: Partial<Record<NodeStatus, number>>;
  /** Items priced at the plan's median because nothing measured them. */
  assumed: number;
}

export type NodeStatus = "done" | "running" | "ready" | "waiting" | "needs_person" | "blocked";

/** One ticket in the plan's dependency graph. */
export interface PlanNode {
  id: string;
  external_id: string;
  title: string;
  workspace_slug: string;
  state: TicketState;
  status: NodeStatus;
  lane: string;
  /** Null for a prerequisite outside the initiative. */
  milestone_id: string | null;
  step: number;
  deps: string[];
  waiting_on: string[];
  start: string | null;
  finish: string | null;
  duration_days: number | null;
  basis: ForecastBasis;
  assumed: boolean;
  critical: boolean;
  external: boolean;
}

export type AutopilotAction = "enabled" | "disabled" | "dispatched" | "refused" | "paused";

export interface AutopilotView {
  enabled: boolean;
  max_parallel: number;
  /** Why it stopped itself; blank when it has not. */
  paused_reason: string;
  in_flight: number;
  /** Ticket ids it would start next, in order. */
  next_up: string[];
  /** False on a sandbox server, where the loop never runs. */
  available: boolean;
  recent: {
    action: AutopilotAction;
    ticket_id: string | null;
    ticket_external_id: string | null;
    detail: string;
    created_at: string;
  }[];
}

export interface WorkspacePace {
  workspace_slug: string;
  per_day: number | null;
  completed: number;
  basis: ForecastBasis;
}

export interface ScheduleTargetInput {
  ticket_id: string;
  /** Omitted leaves the date alone; null clears it. */
  target_date?: string | null;
  plan_order?: number | null;
}

export interface ScheduleProposal {
  id: string;
  source: "draft" | "chat";
  mode: ScheduleMode | null;
  rationale: string;
  items: ScheduleTargetInput[];
  created_at: string;
}

export interface InitiativePlan {
  id: string;
  external_id: string;
  title: string;
  description: string;
  state: TicketState;
  mode: ScheduleMode;
  notes: string;
  target_date: string | null;
  forecast_date: string | null;
  planned_date: string | null;
  drift_days: number | null;
  status: ScheduleStatus;
  /** Open milestones with no forecast; when > 0 `forecast_date` is null. */
  unforecast_milestones: number;
  milestones: MilestoneSchedule[];
  paces: WorkspacePace[];
  window_days: number;
  pending_proposal: ScheduleProposal | null;
  nodes: PlanNode[];
  /** Ticket ids on the critical path, first to last. */
  critical_path: string[];
  /** Ticket ids caught in a dependency cycle. */
  cyclic: string[];
  lanes: string[];
  autopilot: AutopilotView;
  generated_at: string;
}

export interface PlannerMessage extends ChatMessageView {
  status: "pending" | "complete" | "failed";
  turn_mode: "chat" | "draft";
}

export interface PlannerSnapshot {
  initiative_id: string;
  messages: PlannerMessage[];
  /** The running turn's assistant row; null when idle. */
  active_turn_id: string | null;
  /** The provider/model the next turn runs on. */
  runtime: WorkspaceRuntimeSettings;
  /** The workspace the next turn runs in; null while none is checked out. */
  workspace_slug: string | null;
}

/** Initiatives span workspaces, so they are read here rather than through the
 * workspace-scoped ticket list. Writes reuse the ticket endpoints. */
export const initiativeApi = {
  initiatives: () => request<InitiativeView[]>("/api/initiatives"),
  initiative: (id: string) => request<InitiativeView>(`/api/initiatives/${id}`),
  /** Tickets of any type but initiative, in any workspace, the initiative does not cover yet. */
  initiativeMemberCandidates: (id: string, search: string) =>
    request<InitiativeMilestone[]>(
      `/api/initiatives/${id}/member-candidates?search=${encodeURIComponent(search)}`,
    ),
  /** Track a ticket and its subtree without re-parenting it. */
  addInitiativeMember: (id: string, ticketId: string) =>
    request<InitiativeView>(`/api/initiatives/${id}/members`, {
      method: "POST",
      body: JSON.stringify({ ticket_id: ticketId }),
    }),
  removeInitiativeMember: (id: string, ticketId: string) =>
    request<void>(`/api/initiatives/${id}/members/${ticketId}`, { method: "DELETE" }),
  attachableMilestones: () =>
    request<InitiativeMilestone[]>("/api/initiatives/attachable-milestones"),
  createInitiative: (body: { title: string; description?: string; priority?: number }) =>
    request<TicketDetail>("/api/tickets", {
      method: "POST",
      body: JSON.stringify({ ...body, work_item_type: "initiative" }),
    }),
  /** Instant keyword themes plus a paced sprint, from work no initiative owns. */
  initiativeSuggestions: (sprintDays: number) =>
    request<InitiativeSuggestionSet>(`/api/initiatives/suggestions?sprint_days=${sprintDays}`),
  /** One agent turn over the same candidates; holds the request until it answers. */
  agentInitiativeSuggestions: (sprintDays: number, signal?: AbortSignal) =>
    request<InitiativeSuggestionSet>("/api/initiatives/suggestions/agent", {
      method: "POST",
      body: JSON.stringify({ sprint_days: sprintDays }),
      signal,
    }),
  /** Create every kept suggestion; the server checks the whole batch before writing. */
  applyInitiativeSuggestions: (initiatives: InitiativeDraft[]) =>
    request<{ created: CreatedInitiative[] }>("/api/initiatives/suggestions/apply", {
      method: "POST",
      body: JSON.stringify({ initiatives }),
    }),
  /** Open features and bugs that could join a sprint-style initiative; never integration reviews. */
  initiativeAddableWork: (id: string, search: string) =>
    request<SuggestedItem[]>(`/api/initiatives/${id}/addable-work?search=${encodeURIComponent(search)}`),
  initiativePlan: (id: string) => request<InitiativePlan>(`/api/initiatives/${id}/plan`),
  updateInitiativePlan: (
    id: string,
    body: { mode?: ScheduleMode; notes?: string; targets?: ScheduleTargetInput[] },
  ) =>
    request<InitiativePlan>(`/api/initiatives/${id}/plan`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  resolveScheduleProposal: (id: string, proposalId: string, action: "accept" | "discard") =>
    request<InitiativePlan>(`/api/initiatives/${id}/plan/proposals/${proposalId}/${action}`, {
      method: "POST",
    }),
  setAutopilot: (id: string, body: { enabled?: boolean; max_parallel?: number }) =>
    request<InitiativePlan>(`/api/initiatives/${id}/autopilot`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  /** Queue named tickets if the plan says they are ready; returns id -> outcome. */
  startInitiativeWork: (id: string, ticketIds: string[]) =>
    request<Record<string, string>>(`/api/initiatives/${id}/work`, {
      method: "POST",
      body: JSON.stringify({ ticket_ids: ticketIds }),
    }),
  markNeedsPerson: (id: string, ticketIds: string[], needsPerson: boolean) =>
    request<InitiativePlan>(`/api/initiatives/${id}/needs-person`, {
      method: "POST",
      body: JSON.stringify({ ticket_ids: ticketIds, needs_person: needsPerson }),
    }),
  plannerChat: (id: string) => request<PlannerSnapshot>(`/api/initiatives/${id}/planner`),
  sendPlannerMessage: (id: string, content: string, mode: "chat" | "draft" = "chat") =>
    request<PlannerSnapshot>(`/api/initiatives/${id}/planner/messages`, {
      method: "POST",
      body: JSON.stringify({ content, mode }),
    }),
  stopPlannerTurn: (id: string) =>
    request<PlannerSnapshot>(`/api/initiatives/${id}/planner/stop`, { method: "POST" }),
  setPlannerRuntime: (id: string, body: WorkspaceRuntimeSettings) =>
    request<WorkspaceRuntimeSettings>(`/api/initiatives/${id}/planner/runtime`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  /** `initiativeId` null detaches the milestone, leaving it a workspace root. */
  setMilestoneInitiative: (milestoneId: string, initiativeId: string | null) =>
    request<TicketDetail>(`/api/tickets/${milestoneId}`, {
      method: "PATCH",
      body: JSON.stringify({ parent_ticket_id: initiativeId ?? "" }),
    }),
};
