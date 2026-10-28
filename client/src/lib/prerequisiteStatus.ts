import type { TicketDependencyRef } from "../api/types";
import { ticketStateLabel } from "./ticketStates";

/** Where a prerequisite stands, as dispatch judges it: whether it still holds this ticket, and why. */
export function prerequisiteStatus(dep: TicketDependencyRef): { text: string; holding: boolean } {
  if (dep.overridden) return { text: "started past it", holding: false };
  if (dep.unmet_reason === "not_landed") {
    return { text: `done, not landed on ${dep.landing_target || "the target"}`, holding: true };
  }
  if (dep.unmet_reason === "not_done") {
    return { text: `waiting · ${ticketStateLabel(dep.state).toLowerCase()}`, holding: true };
  }
  return { text: "met", holding: false };
}
