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
  /** `initiativeId` null detaches the milestone, leaving it a workspace root. */
  setMilestoneInitiative: (milestoneId: string, initiativeId: string | null) =>
    request<TicketDetail>(`/api/tickets/${milestoneId}`, {
      method: "PATCH",
      body: JSON.stringify({ parent_ticket_id: initiativeId ?? "" }),
    }),
};
