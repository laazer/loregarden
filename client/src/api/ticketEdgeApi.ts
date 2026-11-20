import { request } from "./http";
import type { TicketDetail, TicketState } from "./types";

/** How a sibling claims a file: it has changed it, declared it mid-run, or planned it. */
export type PathClaimSource = "observed" | "declared" | "planned";

export interface SiblingClaim {
  ticket_id: string;
  external_id: string;
  title: string;
  state: TicketState;
  /** Either ticket already waits for the other, so they cannot run side by side. */
  ordered: boolean;
  /** Strongest claim first. */
  paths: { path: string; source: PathClaimSource }[];
}

export interface SiblingClaimsView {
  /** How many files this ticket claims; 0 means there is nothing to compare yet. */
  claimed_paths: number;
  /** Not-yet-ordered siblings first. */
  siblings: SiblingClaim[];
}

/** Edges between tickets: blocking dependencies, and non-blocking relations.
 * Both sides return the refreshed TicketDetail so callers re-render from one
 * response instead of a write followed by a read. */
export const ticketEdgeApi = {
  /** Open siblings in this ticket's landing tree that claim the same files. */
  siblingClaims: (id: string) => request<SiblingClaimsView>(`/api/tickets/${id}/sibling_claims`),
  addDependency: (id: string, dependsOn: string) =>
    request<TicketDetail>(`/api/tickets/${id}/dependencies`, {
      method: "POST",
      body: JSON.stringify({ depends_on: dependsOn }),
    }),
  removeDependency: (id: string, dependsOnId: string) =>
    request<TicketDetail>(`/api/tickets/${id}/dependencies/${dependsOnId}`, {
      method: "DELETE",
    }),
  addRelation: (id: string, relatedTo: string) =>
    request<TicketDetail>(`/api/tickets/${id}/relations`, {
      method: "POST",
      body: JSON.stringify({ related_to: relatedTo }),
    }),
  removeRelation: (id: string, relatedId: string) =>
    request<TicketDetail>(`/api/tickets/${id}/relations/${relatedId}`, {
      method: "DELETE",
    }),
};
