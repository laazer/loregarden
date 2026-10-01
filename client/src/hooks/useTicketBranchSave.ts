import { useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import { toastActionFailed } from "../state/toastStore";

/**
 * Save a ticket's branch, then refetch the ticket so it shows the server's answer.
 *
 * The cached ticket is the stored branch, never a draft: callers compare
 * against it to decide whether a save is needed. A rejected save says what
 * happened and the refetch puts the stored branch back on screen, rather than
 * leaving the operator to start a run on a branch that was never stored.
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
