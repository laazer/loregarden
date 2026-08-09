import type { TicketState, WorkItemType } from "./types";

/** A ticket at one end of an edge — a dependency or a relation. Both edge kinds
 * render the same way, so they share one shape rather than two identical ones. */
export interface TicketDependencyRef {
  id: string;
  external_id: string;
  title: string;
  state: TicketState;
  work_item_type: WorkItemType;
  is_integration_review: boolean;
}
