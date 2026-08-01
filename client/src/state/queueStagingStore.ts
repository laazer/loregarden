/**
 * Tickets a user has placed into a slot but not started yet.
 *
 * Staging is deliberately client-only. Until Start is pressed there is no run,
 * no worktree, and nothing for the server to hold — writing a placeholder row
 * would put a ticket in the queue that the queue cannot execute. What the
 * server owns is what is running; what this owns is what you lined up.
 *
 * Not keyed by workspace: the slots are one shared pool, so the staging area
 * is one board. Each staged ticket carries the workspace it belongs to so the
 * card can say whose work it is. Persisted, so switching pages does not
 * discard the plan.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface StagedTicket {
  ticketId: string;
  /** external_id, e.g. LG-42 — shown on the slot card. */
  code: string;
  title: string;
  /** Whose work this is. Every workspace shares the board, so the card says. */
  workspaceName: string;
}

/** slot number -> the ticket staged there */
type SlotAssignments = Record<number, StagedTicket>;

interface QueueStagingState {
  staged: SlotAssignments;
  stage: (slotNumber: number, ticket: StagedTicket) => void;
  unstage: (slotNumber: number) => void;
  /**
   * Drop staged entries that the server has overtaken — the slot filled with
   * something else, or the ticket is running or queued already (started from
   * here, or from anywhere else).
   */
  reconcile: (busySlots: number[], liveTicketIds: string[]) => void;
}

function withoutSlots(assignments: SlotAssignments, slots: number[]): SlotAssignments {
  const next: SlotAssignments = {};
  for (const [slot, ticket] of Object.entries(assignments)) {
    if (!slots.includes(Number(slot))) next[Number(slot)] = ticket;
  }
  return next;
}

export const useQueueStagingStore = create<QueueStagingState>()(
  persist(
    (set) => ({
      staged: {},

      stage: (slotNumber, ticket) =>
        set((state) => {
          // A ticket occupies one slot at a time; staging it again moves it.
          const cleared = withoutSlots(
            state.staged,
            Object.entries(state.staged)
              .filter(([, staged]) => staged.ticketId === ticket.ticketId)
              .map(([slot]) => Number(slot)),
          );
          return { staged: { ...cleared, [slotNumber]: ticket } };
        }),

      unstage: (slotNumber) =>
        set((state) => {
          if (!state.staged[slotNumber]) return state;
          return { staged: withoutSlots(state.staged, [slotNumber]) };
        }),

      reconcile: (busySlots, liveTicketIds) =>
        set((state) => {
          const stale = Object.entries(state.staged)
            .filter(
              ([slot, ticket]) =>
                busySlots.includes(Number(slot)) || liveTicketIds.includes(ticket.ticketId),
            )
            .map(([slot]) => Number(slot));

          // No-op returns the same object so this is safe to call on every
          // socket snapshot without re-rendering every subscriber.
          if (stale.length === 0) return state;

          return { staged: withoutSlots(state.staged, stale) };
        }),
    }),
    { name: "loregarden-queue-staging" },
  ),
);
