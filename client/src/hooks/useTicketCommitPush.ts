import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import { useUiStore } from "../state/uiStore";
import { describeError, toastActionFailed } from "../state/toastStore";

/** The message handed to the ticket's triage agent when Commit & push fails. */
export function commitPushFailurePrompt(error: unknown): string {
  return [
    "Commit & push from the PR tab failed:",
    "",
    describeError(error, "unknown error"),
    "",
    "Work out why, fix it, then commit and push this ticket's branch.",
  ].join("\n");
}

/**
 * The PR tab's Commit & push.
 *
 * A failure stays on the ticket: the dock opens on this ticket's triage chat
 * (the dock binds to the conversation the route shows) and the error is sent
 * there, so the agent that knows the ticket picks it up. It used to jump to the
 * Branch Triage page instead, which dropped the operator out of the ticket.
 *
 * The failure itself is still toasted by the mutation's `errorTitle`.
 */
export function useTicketCommitPush() {
  const qc = useQueryClient();
  const setCopilotOpen = useUiStore((s) => s.setCopilotOpen);

  return useMutation({
    meta: { errorTitle: "Commit and push" },
    mutationFn: (ticketId: string) => api.commitPush(ticketId),
    onSuccess: (_data, ticketId) => {
      qc.invalidateQueries({ queryKey: ["ticket", ticketId] });
      qc.invalidateQueries({ queryKey: ["ticket-tree"] });
    },
    onError: (error, ticketId) => {
      setCopilotOpen(true);
      api
        .sendTriageMessage(ticketId, commitPushFailurePrompt(error))
        .then(() => qc.invalidateQueries({ queryKey: ["triage", ticketId] }))
        .catch((sendError: unknown) => toastActionFailed("Hand off to triage chat", sendError));
    },
  });
}
