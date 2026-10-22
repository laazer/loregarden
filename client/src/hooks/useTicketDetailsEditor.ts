import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import * as apiClient from "../api/client";
import type { TicketDetailsSaveDraft } from "../components/TicketDetailsModal";
import { describeError } from "../state/toastStore";

/** The fields of `draft` that differ from `current`, as an update body. */
function changedFields(
  current: apiClient.TicketDetail,
  draft: TicketDetailsSaveDraft,
): Parameters<typeof apiClient.api.updateTicket>[1] {
  const patch: Parameters<typeof apiClient.api.updateTicket>[1] = {};
  if (draft.title !== current.title) patch.title = draft.title;
  if (draft.description !== (current.description ?? "")) patch.description = draft.description;
  if (draft.acceptanceCriteria.join("\n") !== (current.acceptance_criteria ?? []).join("\n")) {
    patch.acceptance_criteria = draft.acceptanceCriteria;
  }
  if (draft.checkedAcceptanceCriteria.join("\n") !== (current.checked_acceptance_criteria ?? []).join("\n")) {
    patch.checked_acceptance_criteria = draft.checkedAcceptanceCriteria;
  }
  if (draft.tags.join(",") !== (current.tags ?? []).join(",")) patch.tags = draft.tags;
  if (draft.state !== current.state) patch.state = draft.state;
  if (draft.priority !== current.priority) patch.priority = draft.priority;
  return patch;
}

/**
 * Everything `TicketDetailsModal` needs for one ticket: the fetch, the edit and
 * the read-mode check-off, with failures held as `saveError` for the modal to
 * render. `ticketId` may be the UUID or the shareable id; writes go to the UUID
 * once the ticket has loaded. Nothing is fetched while `enabled` is false.
 */
export function useTicketDetailsEditor(ticketId: string | null, enabled: boolean) {
  const [saveError, setSaveError] = useState<string | undefined>();
  const qc = useQueryClient();

  const query = useQuery({
    queryKey: ["ticket", ticketId],
    queryFn: () => apiClient.api.ticket(ticketId ?? ""),
    enabled: enabled && Boolean(ticketId),
  });
  const ticket = query.data ?? null;

  const refresh = (updated?: apiClient.TicketDetail) => {
    if (updated) qc.setQueryData(["ticket", ticketId], updated);
    void qc.invalidateQueries({ queryKey: ["ticket", ticketId] });
    if (ticket && ticket.id !== ticketId) void qc.invalidateQueries({ queryKey: ["ticket", ticket.id] });
  };

  const saveDetails = useMutation({
    meta: { errorTitle: "Save ticket details" },
    mutationFn: async (draft: TicketDetailsSaveDraft) => {
      if (!ticket) return;
      const patch = changedFields(ticket, draft);
      if (Object.keys(patch).length === 0) return;
      await apiClient.api.updateTicket(ticket.id, patch);
    },
    onSuccess: () => {
      setSaveError(undefined);
      refresh();
      void qc.invalidateQueries({ queryKey: ["ticket-tree"] });
      void qc.invalidateQueries({ queryKey: ["tickets"] });
    },
    onError: (err) => setSaveError(describeError(err, "Failed to save ticket details")),
  });

  // Ticking a criterion from read mode: one field, saved on the click.
  const saveChecks = useMutation({
    meta: { errorTitle: "Check off acceptance criterion" },
    mutationFn: (checked: string[]) =>
      apiClient.api.updateTicket(ticket?.id ?? ticketId ?? "", { checked_acceptance_criteria: checked }),
    onSuccess: (updated) => {
      setSaveError(undefined);
      refresh(updated);
    },
    onError: (err) => setSaveError(describeError(err, "Failed to save the checked criteria")),
  });

  // Both reject on failure, after onError has put the message in `saveError`,
  // which TicketDetailsModal renders; the rejection is how it knows to keep
  // the draft (or revert the checkbox) rather than assume the write landed.
  return {
    ticket,
    isLoading: query.isLoading,
    loadError: query.error ? describeError(query.error, "Failed to load ticket details") : undefined,
    saveError,
    clearSaveError: () => setSaveError(undefined),
    isSaving: saveDetails.isPending,
    isSavingChecks: saveChecks.isPending,
    save: async (draft: TicketDetailsSaveDraft) => {
      setSaveError(undefined);
      await saveDetails.mutateAsync(draft);
    },
    saveChecks: async (checked: string[]) => {
      setSaveError(undefined);
      await saveChecks.mutateAsync(checked);
    },
  };
}
