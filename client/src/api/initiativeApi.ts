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
  /** Measured pace in plan order; never before `earliest_date`. */
  forecast_date: string | null;
  /** Agent run-time floor for the remaining stages. */
  earliest_date: string | null;
  /** Target in a fixed plan, forecast in a rolling one. */
  planned_date: string | null;
  /** Forecast minus target in days; positive is late. */
  drift_days: number | null;
  status: ScheduleStatus;
  basis: ForecastBasis;
  remaining: number;
  total: number;
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
