import { IconCloseButton } from "./IconCloseButton";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { api, type TicketImportItem, type TicketImportPreviewResponse } from "../api/client";
import {
  applyMilestoneToTicket,
  applyParentToTicket,
  buildImportMilestoneOptions,
  buildQuickImportItem,
  collectImportExternalIds,
  milestoneOptionFromItem,
  validateImportDraft,
} from "../lib/importTicketPreview";
import { workItemTypeLabel } from "../lib/workItemHierarchy";
import { ImportQuickCreate } from "./ImportQuickCreate";
import { ImportTicketPreviewCard } from "./ImportTicketPreviewCard";

export interface ImportTicketsConfirmModalProps {
  open: boolean;
  workspaceSlug: string;
  preview: TicketImportPreviewResponse | null;
  isImporting: boolean;
  importError?: string | null;
  onClose: () => void;
  onConfirm: (tickets: TicketImportItem[]) => void | Promise<void>;
}

function formatCounts(byType: Record<string, number>): string {
  return Object.entries(byType)
    .map(([type, count]) => {
      const label = workItemTypeLabel(type as Parameters<typeof workItemTypeLabel>[0]);
      return `${count} ${label}${count === 1 ? "" : "s"}`;
    })
    .join(", ");
}

export function ImportTicketsConfirmModal({
  open,
  workspaceSlug,
  preview,
  isImporting,
  importError,
  onClose,
  onConfirm,
}: ImportTicketsConfirmModalProps) {
  const [draftTickets, setDraftTickets] = useState<TicketImportItem[]>([]);
  const [bulkMilestoneId, setBulkMilestoneId] = useState("");

  useEffect(() => {
    if (!open || !preview) return;
    setDraftTickets(preview.tickets.map((ticket) => ({ ...ticket })));
    setBulkMilestoneId("");
  }, [open, preview]);

  const workspaceTickets = useQuery({
    queryKey: ["tickets", "import-preview", workspaceSlug],
    queryFn: () => api.tickets({ workspace: workspaceSlug }),
    enabled: open && !!workspaceSlug,
  });

  const milestoneOptions = useMemo(
    () => buildImportMilestoneOptions(workspaceTickets.data ?? [], draftTickets),
    [workspaceTickets.data, draftTickets],
  );

  const draftIssues = useMemo(() => validateImportDraft(draftTickets), [draftTickets]);
  const fileTickets = useMemo(
    () => draftTickets.filter((ticket) => ticket.source_format !== "quick"),
    [draftTickets],
  );
  const quickContainers = useMemo(
    () => draftTickets.filter((ticket) => ticket.source_format === "quick"),
    [draftTickets],
  );
  const parseErrors = preview?.errors ?? [];
  const hasBlockingErrors = parseErrors.length > 0;
  const hasTickets = fileTickets.length > 0;
  const canImport = hasTickets && !hasBlockingErrors && draftIssues.length === 0 && !isImporting;

  if (!open || !preview) return null;

  const updateTicket = (index: number, ticket: TicketImportItem) => {
    setDraftTickets((current) => current.map((item, i) => (i === index ? ticket : item)));
  };

  const applyBulkMilestone = () => {
    if (!bulkMilestoneId) return;
    const milestone = milestoneOptions.find((option) => option.external_id === bulkMilestoneId);
    if (!milestone) return;
    setDraftTickets((current) => current.map((ticket) => applyMilestoneToTicket(ticket, milestone)));
  };

  const handleQuickCreateMilestone = (title: string, ticketIndex?: number) => {
    setDraftTickets((current) => {
      const item = buildQuickImportItem({
        work_item_type: "milestone",
        title,
        existingExternalIds: collectImportExternalIds(workspaceTickets.data ?? [], current),
      });
      const next = [...current, item];
      const milestone = milestoneOptionFromItem(item);
      if (ticketIndex === undefined) {
        setBulkMilestoneId(item.external_id ?? "");
        return next;
      }
      return next.map((ticket, index) =>
        index === ticketIndex ? applyMilestoneToTicket(ticket, milestone) : ticket,
      );
    });
  };

  const handleQuickCreateCapability = (
    title: string,
    featureKey: string,
    ticketIndex: number,
  ) => {
    setDraftTickets((current) => {
      const featureOptions = [
        ...(workspaceTickets.data ?? []).filter((ticket) => ticket.work_item_type === "feature"),
      ];
      const featureFromBatch = current.filter(
        (ticket) => ticket.work_item_type === "feature" && ticket.external_id,
      );

      let parent_ticket_id: string | null = null;
      let parent_external_id = "";
      if (featureKey.startsWith("id:")) {
        parent_ticket_id = featureKey.slice(3);
      } else if (featureKey.startsWith("ext:")) {
        parent_external_id = featureKey.slice(4);
      } else {
        return current;
      }

      const featureTicket =
        featureOptions.find((ticket) => ticket.id === parent_ticket_id) ??
        featureFromBatch.find((ticket) => ticket.external_id === parent_external_id);

      const item = buildQuickImportItem({
        work_item_type: "capability",
        title,
        existingExternalIds: collectImportExternalIds(workspaceTickets.data ?? [], current),
        parent_ticket_id,
        parent_external_id,
        milestone: featureTicket?.milestone ?? "",
      });
      const next = [...current, item];
      return next.map((ticket, index) =>
        index === ticketIndex
          ? applyParentToTicket(ticket, {
              id: null,
              external_id: item.external_id ?? "",
              label: `${item.external_id} · ${item.title} (new)`,
              source: "quick",
            })
          : ticket,
      );
    });
  };

  const handleQuickCreateFeature = (
    title: string,
    milestoneKey: string,
    ticketIndex: number,
  ) => {
    setDraftTickets((current) => {
      const milestoneOpts = buildImportMilestoneOptions(workspaceTickets.data ?? [], current);
      const milestone = milestoneOpts.find((option) => option.external_id === milestoneKey);
      if (!milestone) return current;

      const item = buildQuickImportItem({
        work_item_type: "feature",
        title,
        existingExternalIds: collectImportExternalIds(workspaceTickets.data ?? [], current),
        parent_ticket_id: milestone.id,
        parent_external_id: milestone.id ? "" : milestone.external_id,
        milestone: milestone.milestone,
      });
      const next = [...current, item];
      return next.map((ticket, index) =>
        index === ticketIndex
          ? applyParentToTicket(ticket, {
              id: null,
              external_id: item.external_id ?? "",
              label: `${item.external_id} · ${item.title} (new)`,
              source: "quick",
            })
          : ticket,
      );
    });
  };

  return (
    <>
      <div className="modal-overlay" onClick={isImporting ? undefined : onClose} role="presentation" />
      <div
        className="modal-panel import-confirm-modal"
        role="dialog"
        aria-labelledby="import-tickets-title"
      >
        <div className="modal-header">
          <div>
            <div className="state-label">{workspaceSlug}</div>
            <h2 id="import-tickets-title" className="modal-title">
              Import work items
            </h2>
            <p className="modal-subtitle">
              Review parsed fields, assign milestones, and confirm before importing.
            </p>
          </div>
          <IconCloseButton disabled={isImporting} onClick={onClose} />
        </div>

        <div className="modal-body">
          <div className="modal-field">
            <div className="modal-field-label">Summary</div>
            <div style={{ fontSize: 13, lineHeight: 1.55, color: "var(--txm)" }}>
              {hasTickets ? (
                <>
                  <strong style={{ color: "var(--tx)" }}>{fileTickets.length}</strong> work item
                  {fileTickets.length === 1 ? "" : "s"} from {preview.formats.join(", ") || "unknown"}{" "}
                  file{preview.formats.length === 1 ? "" : "s"}
                  {quickContainers.length > 0 && (
                    <>
                      {" "}
                      + {quickContainers.length} new container
                      {quickContainers.length === 1 ? "" : "s"}
                    </>
                  )}
                  {Object.keys(preview.by_type).length > 0 && <> — {formatCounts(preview.by_type)}</>}
                </>
              ) : (
                "No valid tickets were found in the selected files."
              )}
            </div>
          </div>

          {hasTickets && (
            <div className="modal-field import-bulk-milestone">
              <div className="modal-field-label">Assign milestone to all</div>
              <div className="import-bulk-milestone-row">
                <select
                  className="btn-secondary filter-select"
                  style={{ flex: 1, fontSize: 12 }}
                  value={bulkMilestoneId}
                  disabled={isImporting}
                  onChange={(event) => setBulkMilestoneId(event.target.value)}
                >
                  <option value="">Choose milestone…</option>
                  {milestoneOptions.map((option) => (
                    <option key={`bulk-${option.source}-${option.external_id}`} value={option.external_id}>
                      {option.external_id} · {option.title}
                      {option.source === "quick" ? " (new)" : option.source === "import" ? " (import)" : ""}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="btn-secondary btn-compact"
                  disabled={isImporting || !bulkMilestoneId}
                  onClick={applyBulkMilestone}
                >
                  Apply to all
                </button>
                <ImportQuickCreate
                  label="+ Milestone"
                  placeholder="New milestone title"
                  actionLabel="Add"
                  disabled={isImporting}
                  onSubmit={(title) => handleQuickCreateMilestone(title)}
                />
              </div>
            </div>
          )}

          {parseErrors.length > 0 && (
            <div className="modal-field">
              <div className="modal-field-label">Parse errors</div>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: "var(--rdl)" }}>
                {parseErrors.map((error) => (
                  <li key={error}>{error}</li>
                ))}
              </ul>
            </div>
          )}

          {draftIssues.length > 0 && (
            <div className="modal-field">
              <div className="modal-field-label">Import blockers</div>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: "var(--amb)" }}>
                {draftIssues.map((issue) => (
                  <li key={issue}>{issue}</li>
                ))}
              </ul>
            </div>
          )}

          {preview.warnings.length > 0 && draftIssues.length === 0 && (
            <div className="modal-field">
              <div className="modal-field-label">Notes</div>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: "var(--txm)" }}>
                {preview.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </div>
          )}

          {importError && (
            <p className="modal-hint" style={{ color: "var(--rdl)" }}>
              {importError}
            </p>
          )}

          {quickContainers.length > 0 && (
            <div className="modal-field">
              <div className="modal-field-label">New containers</div>
              <ul className="import-quick-container-list">
                {quickContainers.map((ticket) => (
                  <li key={ticket.external_id ?? ticket.title}>
                    <span style={{ color: "var(--tx)" }}>{ticket.title}</span>
                    {" · "}
                    {workItemTypeLabel(ticket.work_item_type)}
                    {ticket.external_id ? ` · ${ticket.external_id}` : ""}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {hasTickets && (
            <div className="modal-field">
              <div className="modal-field-label">
                {fileTickets.length === 1 ? "Ticket preview" : "Ticket previews"}
              </div>
              <div className="import-preview-stack">
                {draftTickets.map((ticket, index) => {
                  if (ticket.source_format === "quick") return null;
                  return (
                    <ImportTicketPreviewCard
                      key={`${ticket.source_label}-${index}`}
                      ticket={ticket}
                      index={index}
                      total={fileTickets.length}
                      milestoneOptions={milestoneOptions}
                      existingTickets={workspaceTickets.data ?? []}
                      batchTickets={draftTickets}
                      disabled={isImporting}
                      onChange={(updated) => updateTicket(index, updated)}
                      onQuickCreateMilestone={handleQuickCreateMilestone}
                      onQuickCreateCapability={handleQuickCreateCapability}
                      onQuickCreateFeature={handleQuickCreateFeature}
                    />
                  );
                })}
              </div>
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button type="button" className="btn-secondary" disabled={isImporting} onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={!canImport}
            onClick={() => void onConfirm(draftTickets)}
          >
            {isImporting
              ? "Importing…"
              : `Import ${draftTickets.length} item${draftTickets.length === 1 ? "" : "s"}`}
          </button>
        </div>
      </div>
    </>
  );
}
