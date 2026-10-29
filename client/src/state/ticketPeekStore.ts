import { create } from "zustand";

import type { ArtifactTab } from "../lib/appNavigation";

/**
 * The ticket a link was clicked for, read in place.
 *
 * A ticket named on another surface (a plan row, a dependency, a chat reply)
 * is usually a question ("what is this one?") rather than a decision to leave
 * the page. Its link hands the ticket here; `TicketPeekHost`, mounted once in
 * `AppLayout`, shows its details in a dialog with "Open ticket" to go on to the
 * ticket's page, on `tab` when the link named one.
 */
export interface TicketPeek {
  /** The UUID or the shareable id; the ticket endpoint answers to both. */
  ticketId: string;
  tab?: ArtifactTab;
}

interface TicketPeekState {
  peek: TicketPeek | null;
  open: (peek: TicketPeek) => void;
  close: () => void;
}

export const useTicketPeekStore = create<TicketPeekState>((set) => ({
  peek: null,
  open: (peek) => set({ peek }),
  close: () => set({ peek: null }),
}));

export function peekTicket(ticketId: string, tab?: ArtifactTab): void {
  useTicketPeekStore.getState().open({ ticketId, tab });
}
