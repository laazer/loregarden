import type { ChatMessageView } from "../components/chat/chatUtils";
import { request } from "./http";
import type { TicketDetail, TicketState } from "./types";

/** A milestone as an initiative sees it: tagged with the workspace it lives in. */
export interface InitiativeMilestone {
  id: string;
  external_id: string;
  title: string;
  state: TicketState;
  workspace_slug: string;
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
}

/** Initiatives span workspaces, so they are read here rather than through the
 * workspace-scoped ticket list. Writes reuse the ticket endpoints. */
export const initiativeApi = {
  initiatives: () => request<InitiativeView[]>("/api/initiatives"),
  attachableMilestones: () =>
    request<InitiativeMilestone[]>("/api/initiatives/attachable-milestones"),
  createInitiative: (body: { title: string; description?: string; priority?: number }) =>
    request<TicketDetail>("/api/tickets", {
      method: "POST",
      body: JSON.stringify({ ...body, work_item_type: "initiative" }),
    }),
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
  /** `initiativeId` null detaches the milestone, leaving it a workspace root. */
  setMilestoneInitiative: (milestoneId: string, initiativeId: string | null) =>
    request<TicketDetail>(`/api/tickets/${milestoneId}`, {
      method: "PATCH",
      body: JSON.stringify({ parent_ticket_id: initiativeId ?? "" }),
    }),
};
