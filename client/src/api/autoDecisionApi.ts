import { request } from "./http";

/** The decisions `services/auto_decisions.py` lists for review (`REVIEWABLE`). */
export type AutoDecisionKind = "split_criteria" | "deferred_undecided_findings";

/** Mirrors `AutoDecisionView` in `services/auto_decisions.py`. */
export interface AutoDecision {
  ticket_id: string;
  external_id: string;
  title: string;
  decision: AutoDecisionKind;
  reason: string;
  at: string;
  /** The ticket a split produced, or "" when the decision names none. */
  related_external_id: string;
}

export const autoDecisionsKey = (ticketId: string) => ["auto-decisions", ticketId] as const;

export const autoDecisionApi = {
  /** What the control plane decided without a person on this ticket and every one under it, newest first. */
  autoDecisions: (id: string) => request<AutoDecision[]>(`/api/events/ticket/${id}/auto-decisions`),
};
