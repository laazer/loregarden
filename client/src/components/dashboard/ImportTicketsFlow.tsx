import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type TicketImportPreviewResponse } from "../../api/client";
import { navigateToStudioTicketSession, navigateToTicket } from "../../lib/useAppNavigation";
import { toastWarning } from "../../state/toastStore";
import { errorDetail } from "../../utils/errorDetail";
import { ImportTicketsConfirmModal } from "../ImportTicketsConfirmModal";
import { ImportTicketsModal, type ImportMode } from "../ImportTicketsModal";

const UNREADABLE: TicketImportPreviewResponse = {
  tickets: [],
  errors: ["Failed to read or parse the selected files. Check the format and try again."],
  warnings: [],
  total: 0,
  by_type: {},
  formats: [],
  show_preview: false,
};

interface ImportTicketsFlowProps {
  workspaceSlug: string;
  /** Where the file picker starts: the workspace's repo. */
  browsePath: string;
  onClose: () => void;
}

/**
 * Importing work items from files, from the Console: pick files, then either
 * preview and confirm them, or hand them to a smart-import Studio session.
 * Mount it to open it: each opening starts clean.
 */
export function ImportTicketsFlow({ workspaceSlug, browsePath, onClose }: ImportTicketsFlowProps) {
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [preview, setPreview] = useState<TicketImportPreviewResponse | null>(null);

  const showPreview = (next: TicketImportPreviewResponse) => {
    setPreview(next);
    setConfirming(true);
  };

  const previewTicketImport = useMutation({
    meta: { errorTitle: "Preview import" },
    mutationFn: (filePaths: string[]) =>
      api.previewTicketImportPaths({ workspace_slug: workspaceSlug, file_paths: filePaths }),
    onSuccess: showPreview,
  });

  const startSmartImport = useMutation({
    meta: { errorTitle: "Start smart import" },
    mutationFn: async (filePaths: string[]) => {
      const found = await api.previewTicketImportPaths({ workspace_slug: workspaceSlug, file_paths: filePaths });
      if (found.tickets.length === 0) {
        throw new Error(found.errors[0] || "No importable tickets found in the selected files.");
      }
      const created = await api.createTicketStudioSession({
        workspace_slug: workspaceSlug,
        title: found.tickets.length === 1 ? found.tickets[0].title : `Smart import (${found.tickets.length} tickets)`,
        brief: `Imported from ${filePaths.length} file${filePaths.length === 1 ? "" : "s"} via smart import.`,
        is_preview: true,
        imported_tickets: found.tickets,
      });
      try {
        // auto_scope: the server generates the breakdown itself when the scoper
        // has nothing to ask, so the chain is not lost if this page goes away.
        return await api.requestTicketStudioClarifications(created.id, true);
      } catch (error) {
        // Keep the session rather than lose the brief; an empty studio otherwise looks intended.
        toastWarning("Session created without a breakdown", error, "Scoping did not start; retry it from the session");
        return created;
      }
    },
    onSuccess: (session) => {
      onClose();
      navigateToStudioTicketSession(session.id);
    },
  });

  const importTickets = useMutation({
    meta: { errorTitle: "Import tickets" },
    mutationFn: (tickets: TicketImportPreviewResponse["tickets"]) =>
      api.importTickets({ workspace_slug: workspaceSlug, tickets }),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["ticket-tree"] });
      qc.invalidateQueries({ queryKey: ["tickets"] });
      if (result.ticket_ids.length > 0) {
        navigateToTicket(result.ticket_ids[0], { replace: true });
      }
      if (result.errors.length > 0) {
        setPreview((current) =>
          current
            ? { ...current, tickets: [], total: 0, errors: result.errors, warnings: [], show_preview: false }
            : current,
        );
        return;
      }
      onClose();
    },
  });

  const handleContinue = async (filePaths: string[], mode: ImportMode) => {
    if (filePaths.length === 0) return;
    if (mode === "smart") {
      try {
        await startSmartImport.mutateAsync(filePaths);
      } catch {
        // silent-ok: startSmartImport.error renders as the modal's errorMessage
      }
      return;
    }
    try {
      await previewTicketImport.mutateAsync(filePaths);
    } catch {
      // silent-ok: shown in the confirm modal as the unreadable-files error, and toasted via meta.errorTitle
      showPreview(UNREADABLE);
    }
  };

  const picking = previewTicketImport.isPending || startSmartImport.isPending;

  return (
    <>
      <ImportTicketsModal
        open={!confirming}
        workspaceSlug={workspaceSlug}
        initialBrowsePath={browsePath}
        isLoading={picking}
        errorMessage={errorDetail(previewTicketImport.error) || errorDetail(startSmartImport.error)}
        onClose={() => {
          if (!picking) onClose();
        }}
        onContinue={handleContinue}
      />
      <ImportTicketsConfirmModal
        open={confirming}
        workspaceSlug={workspaceSlug}
        preview={preview}
        isImporting={importTickets.isPending}
        importError={errorDetail(importTickets.error)}
        onClose={() => {
          if (!importTickets.isPending) onClose();
        }}
        onConfirm={async (tickets) => {
          if (tickets.length === 0) return;
          await importTickets.mutateAsync(tickets);
        }}
      />
    </>
  );
}
