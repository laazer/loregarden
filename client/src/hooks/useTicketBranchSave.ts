import { useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import { toastActionFailed } from "../state/toastStore";

/**
 * Save the branch a field was optimistically written into the cache with.
 *
 * The optimistic write is what makes the input feel immediate, and it is also
 * what makes a rejected save dangerous: the wrong branch stays on screen
 * looking saved. A failure rolls the cache back to the server's answer and says
 * what happened, rather than leaving the operator to start a run on a branch
 * that was never stored.
 */
export function useTicketBranchSave(): {
  save: (ticketId: string, branch: string) => Promise<void>;
  saveOrThrow: (ticketId: string, branch: string) => Promise<void>;
} {
  const qc = useQueryClient();

  const write = async (ticketId: string, branch: string, rethrow: boolean) => {
    try {
      await api.updateTicket(ticketId, { branch });
    } catch (error) {
      toastActionFailed("Save branch", error);
      // Starting a run on a branch that was never stored is worse than not
      // starting it, so the run path aborts where the field edit recovers.
      if (rethrow) throw error;
    } finally {
      await qc.invalidateQueries({ queryKey: ["ticket", ticketId] });
    }
  };

  return {
    save: (ticketId, branch) => write(ticketId, branch, false),
    saveOrThrow: (ticketId, branch) => write(ticketId, branch, true),
  };
}
