import { create } from "zustand";

/**
 * Which UUID each shareable ticket id stands for, as far as this tab has seen.
 *
 * A ticket's address bar shows its shareable id (`/tickets/lor-mcp-gateway-142/diff`),
 * but every ticket-scoped endpoint is keyed by the UUID. `TicketRouteResolver`
 * learns the pair the first time a ticket route is opened and records it here, so
 * anything reading the path — the dashboard, and app chrome outside the route —
 * can turn the id it sees back into the one the API takes, and link builders that
 * only hold a UUID can still write the readable link.
 *
 * Both directions are kept because both are asked: the route has the ref and
 * needs the UUID; `ticketPath` has the UUID and wants the ref.
 */
interface TicketRefState {
  uuidByRef: Record<string, string>;
  refByUuid: Record<string, string>;
  remember: (ticket: { id: string; external_id: string }) => void;
}

export const useTicketRefStore = create<TicketRefState>((set, get) => ({
  uuidByRef: {},
  refByUuid: {},
  remember: ({ id, external_id }) => {
    // A ticket with no shareable id keeps its UUID as its address.
    if (!external_id) return;
    const { uuidByRef, refByUuid } = get();
    if (uuidByRef[external_id] === id && refByUuid[id] === external_id) return;
    set({
      uuidByRef: { ...uuidByRef, [external_id]: id },
      refByUuid: { ...refByUuid, [id]: external_id },
    });
  },
}));
