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
  /** Its workspace; a prerequisite in another one is not on the board you are on. */
  workspace_slug?: string;
  /** On a prerequisite: why it still holds this ticket, judged as a dispatch
   *  judges it (git included). Null or absent when it is met. */
  unmet_reason?: UnmetReason | null;
  /** The branch a not-landed prerequisite has to land on. */
  landing_target?: string;
  /** Unmet, but a person started this ticket past it, so it no longer holds it. */
  overridden?: boolean;
}

/** Mirrors `UnmetReason` in `services.dependency_readiness`. */
export type UnmetReason = "not_done" | "not_landed";
