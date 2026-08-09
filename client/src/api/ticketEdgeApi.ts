import { request } from "./http";
import type { TicketDetail } from "./types";

/** Edges between tickets: blocking dependencies, and non-blocking relations.
 * Both sides return the refreshed TicketDetail so callers re-render from one
 * response instead of a write followed by a read. */
export const ticketEdgeApi = {
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
