import { useMutation, useQueryClient, type UseMutationResult } from "@tanstack/react-query";

import { api, type TicketDetail } from "../api/client";
import { navigateToTicket } from "../lib/useAppNavigation";
import { pushToast } from "../state/toastStore";

/** What a chat surface knows about a ticket it is about to file. */
export interface ChatTicketDraft {
  title: string;
  /** The reply the ticket came from, when it came from one. */
  description?: string;
}

/**
 * File a ticket from a conversation, then open it.
 *
 * Shared because two chat affordances do the same thing from different inputs:
 * `/create <title>` types one, and "Start as ticket" lifts one out of a reply.
 * They were separate mutations that invalidated the same key, toasted the same
 * words and navigated the same way — three chances to drift.
 */
export function useCreateChatTicket(
  workspaceSlug: string,
): UseMutationResult<TicketDetail, Error, ChatTicketDraft> {
  const qc = useQueryClient();
  return useMutation({
    meta: { errorTitle: "Create ticket" },
    mutationFn: ({ title, description }: ChatTicketDraft) =>
      api.createTicket({
        workspace_slug: workspaceSlug,
        title,
        work_item_type: "task",
        ...(description ? { description } : {}),
      }),
    onSuccess: (ticket) => {
      qc.invalidateQueries({ queryKey: ["tickets"] });
      navigateToTicket(ticket.id);
      pushToast({
        title: "Ticket created",
        message: ticket.external_id || ticket.title,
        tone: "success",
      });
    },
  });
}
