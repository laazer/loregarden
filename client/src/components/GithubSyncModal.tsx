import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { useMatch } from "react-router-dom";

import { api, type TicketDetail } from "../api/client";
import {
  githubIssueApi,
  type GithubLinkSyncResult,
  type GithubWorkspaceSyncRequest,
  type GithubWorkspaceSyncResult,
} from "../api/githubIssueApi";
import { describeLinkSync, syncFieldList } from "../lib/githubSyncSummary";
import { parentTypesForChild } from "../lib/parentTicketTree";
import { looksLikeTicketUuid } from "../lib/ticketIds";
import { workItemTypeLabel } from "../lib/workItemHierarchy";
import { useTicketRefStore } from "../state/ticketRefStore";
import { describeError, pushToast } from "../state/toastStore";
import { useUiStore } from "../state/uiStore";
import { GithubBackgroundSync } from "./GithubBackgroundSync";
import { IconCloseButton } from "./IconCloseButton";
import { ParentTicketSelector } from "./ParentTicketSelector";
import { Button } from "./ui/Button";
import { Input } from "./ui/Input";
import { Select } from "./ui/Select";
import { ModalShell } from "./ui/ModalShell";

interface GithubSyncModalProps {
  open: boolean;
  onClose: () => void;
}

function outcome(result: GithubLinkSyncResult): { text: string; failed: boolean } {
  if (result.error) return { text: `Failed: ${result.error}`, failed: true };
  if (result.conflicts.length) {
    const moved = result.pulled.length || result.pushed.length ? `${describeLinkSync(result)} · ` : "";
    return {
      text: `${moved}Conflict in ${syncFieldList(result.conflicts.map((c) => c.field))} — resolve from the ticket`,
      failed: true,
    };
  }
  return { text: describeLinkSync(result), failed: false };
}

function ResultRows({ rows }: { rows: GithubLinkSyncResult[] }) {
  return (
    <ul style={{ listStyle: "none", padding: 0, margin: "6px 0 0", display: "grid", gap: 4 }}>
      {rows.map((row) => {
        const { text, failed } = outcome(row);
        return (
          <li key={row.ticket_id} style={{ fontSize: "var(--fs-body)" }}>
            <a href={row.issue_url} target="_blank" rel="noreferrer">
              #{row.issue_number}
            </a>{" "}
            <span style={{ fontFamily: "var(--mono)" }}>{row.external_id}</span>{" "}
            <span style={{ color: failed ? "var(--red)" : undefined }}>{text}</span>
          </li>
        );
      })}
    </ul>
  );
}

function SyncSummary({ result }: { result: GithubWorkspaceSyncResult }) {
  const failures = result.links.filter((r) => r.error || r.conflicts.length).length;
  if (!result.links.length && !result.imported.length) {
    return (
      <p className="modal-hint">
        Nothing to sync in {result.repo}: no ticket in this workspace is linked yet. Publish one from
        its ticket details, or choose a parent above to import the repository's open issues.
      </p>
    );
  }
  return (
    <div aria-live="polite">
      <p className="modal-hint" style={{ margin: 0 }}>
        {result.repo}: synced {result.links.length} linked ticket{result.links.length === 1 ? "" : "s"}
        {failures ? `, ${failures} need attention` : ""}; imported {result.imported.length} new issue
        {result.imported.length === 1 ? "" : "s"}.
      </p>
      {result.links.length > 0 && <ResultRows rows={result.links} />}
      {result.imported.length > 0 && (
        <>
          <div className="state-label" style={{ marginTop: 10 }}>
            Imported
          </div>
          <ResultRows rows={result.imported} />
        </>
      )}
    </div>
  );
}

const BUG_PARENT_TYPES = parentTypesForChild("bug");

/** The ticket on screen, if any, fetched the way `TicketRouteResolver` does.
 *
 * The topbar sits outside the ticket route, so the id is read off the path; it
 * may be the shareable id, and the sync request needs the UUID. The keys match
 * the resolver's, so on a ticket page this is the request already made.
 */
function useOpenTicket() {
  const ref = useMatch("/tickets/:ticketId/*")?.params.ticketId ?? "";
  const uuid = useTicketRefStore((s) => (looksLikeTicketUuid(ref) ? ref : s.uuidByRef[ref]));
  const query = useQuery({
    queryKey: uuid ? ["ticket", uuid] : ["ticket-ref", ref],
    queryFn: () => api.ticket(uuid ?? ref),
    enabled: ref !== "",
    retry: false,
    // The resolver learning the UUID moves the key; keep the ticket meanwhile
    // rather than hide the form again.
    placeholderData: (previous) => previous,
  });
  // A failed refetch keeps the ticket already shown; only a failure with
  // nothing to show falls back.
  const failed = query.isError && !query.data;
  const { error } = query;
  useEffect(() => {
    if (!failed) return;
    pushToast({
      tone: "error",
      title: "Sync GitHub issues",
      message: `Could not load the open ticket to import under, so the saved default is used: ${describeError(error, "request failed")}`,
    });
  }, [failed, error]);
  return { ref, ticket: query.data ?? null, settled: ref === "" || !query.isPending };
}

/** Why the open ticket was not preselected, or null when it was (or none is open). */
function openTicketSkipReason(ticket: TicketDetail | null, slugs: string[]): string | null {
  if (!ticket) return null;
  const name = ticket.external_id || ticket.title;
  if (!BUG_PARENT_TYPES.has(ticket.work_item_type)) {
    return `The open ticket, ${name}, is a ${workItemTypeLabel(ticket.work_item_type).toLowerCase()}, which can't hold bugs, so the saved default is used.`;
  }
  if (!slugs.includes(ticket.workspace_slug)) {
    return `The open ticket, ${name}, is in a workspace that isn't listed here, so the saved default is used.`;
  }
  return null;
}

/** Sync every linked ticket in a workspace with its GitHub issue, and
 * optionally import the repository's unlinked open issues under a parent. */
export function GithubSyncModal({ open, onClose }: GithubSyncModalProps) {
  if (!open) {
    // Closed but mounted: the shell plays its exit with the last content it drew.
    return <ModalShell open={false} onDismiss={undefined} labelledBy="github-sync-title">{null}</ModalShell>;
  }
  // Mounted per opening, so each opening starts from the ticket now on screen.
  return <GithubSyncDialog onClose={onClose} />;
}

function GithubSyncDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const activeWorkspace = useUiStore((s) => s.workspace);
  const workspaceFieldId = useId();
  const labelFieldId = useId();

  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });
  const openTicket = useOpenTicket();
  const [chosenSlug, setChosenSlug] = useState("");
  const [parentId, setParentId] = useState<string | null>(null);
  const [label, setLabel] = useState("");

  const slugs = (workspaces.data ?? []).map((ws) => ws.slug);
  const skipReason = openTicketSkipReason(openTicket.ticket, slugs);
  // The open ticket outranks the saved default for this opening only; it is
  // never written to the saved settings unless the operator saves it.
  const preselect = openTicket.ticket && !skipReason ? openTicket.ticket : null;
  const defaultSlug =
    preselect?.workspace_slug ??
    (activeWorkspace !== "all" && slugs.includes(activeWorkspace) ? activeWorkspace : slugs[0] ?? "");
  const slug = chosenSlug || defaultSlug;

  const syncSettings = useQuery({
    queryKey: ["github-sync-settings", slug],
    queryFn: () => githubIssueApi.syncSettings(slug),
    // Wait for the open ticket: it may pick a different workspace.
    enabled: Boolean(slug) && openTicket.settled,
  });
  // The saved import parent and label are this workspace's defaults: prefill
  // them once per workspace, so "Sync now" and the schedule start from the same.
  const savedSettings = syncSettings.data;
  const [prefilledFor, setPrefilledFor] = useState("");
  useEffect(() => {
    if (!savedSettings || prefilledFor === savedSettings.workspace_slug) return;
    setPrefilledFor(savedSettings.workspace_slug);
    const useOpen = !chosenSlug && preselect?.workspace_slug === savedSettings.workspace_slug;
    setParentId(useOpen ? preselect.id : savedSettings.import_parent_ticket_id || null);
    setLabel(savedSettings.import_label);
  }, [savedSettings, prefilledFor, chosenSlug, preselect]);

  const sync = useMutation({
    meta: { errorTitle: "Sync GitHub issues" },
    mutationFn: (request: GithubWorkspaceSyncRequest) => githubIssueApi.syncWorkspace(slug, request),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["tickets"] });
      void qc.invalidateQueries({ queryKey: ["ticket"] });
    },
  });

  return (
    <ModalShell open onDismiss={onClose} labelledBy="github-sync-title">
      <div className="modal-header">
        <div>
          <div className="state-label">GitHub</div>
          <h2 id="github-sync-title" className="modal-title">
            Sync GitHub issues
          </h2>
          <p className="modal-subtitle">
            Brings every linked ticket and its issue up to date: title, description and open/closed state
            move in whichever direction changed.
          </p>
        </div>
        <IconCloseButton onClick={onClose} />
      </div>

      <div className="modal-body" style={{ display: "grid", gap: 12 }}>
        {workspaces.isPending ? (
          <p className="modal-hint">Loading workspaces…</p>
        ) : workspaces.isError ? (
          <p className="modal-hint" role="alert" style={{ color: "var(--red)" }}>
            Could not load workspaces: {describeError(workspaces.error, "request failed")}
          </p>
        ) : slugs.length === 0 ? (
          <p className="modal-hint">No workspaces yet. Add one before syncing with GitHub.</p>
        ) : !openTicket.settled ? (
          // The open ticket may choose the workspace and parent below.
          <p className="modal-hint">Loading the open ticket…</p>
        ) : syncSettings.isPending ? (
          // The saved settings prefill the import fields below; showing them
          // first would let a choice made now be overwritten when they land.
          <p className="modal-hint">Loading this workspace's GitHub settings…</p>
        ) : (
          <>
            <div>
              <label className="field-label" htmlFor={workspaceFieldId}>
                Workspace
              </label>
              <Select
                id={workspaceFieldId}
                className="input"
                value={slug}
                disabled={sync.isPending}
                onChange={(e) => {
                  setChosenSlug(e.target.value);
                  setParentId(null);
                  sync.reset();
                }}
              >
                {slugs.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </Select>
            </div>

            <ParentTicketSelector
              key={slug}
              workspaceSlug={slug}
              value={parentId}
              onChange={(id) => setParentId(id)}
              childWorkItemType="bug"
              noneLabel="None — don't import new issues"
              label="Import new issues under"
              hint="Unlinked open issues become bugs under this work item. Leave empty to only sync existing links."
              disabled={sync.isPending}
            />
            {skipReason && (
              <p className="modal-hint" style={{ margin: 0 }}>
                {skipReason}
              </p>
            )}

            {parentId && (
              <div>
                <label className="field-label" htmlFor={labelFieldId}>
                  Only import issues labelled (optional)
                </label>
                <Input
                  id={labelFieldId}
                  className="input"
                  value={label}
                  placeholder="e.g. loregarden"
                  disabled={sync.isPending}
                  onChange={(e) => setLabel(e.target.value)}
                />
              </div>
            )}

            <GithubBackgroundSync
              workspaceSlug={slug}
              settings={syncSettings}
              importParentTicketId={parentId ?? ""}
              importLabel={label.trim()}
            />

            {sync.isError && (
              <p className="modal-hint" role="alert" style={{ color: "var(--red)", margin: 0 }}>
                Sync failed: {describeError(sync.error, "request failed")}
              </p>
            )}
            {sync.isPending && <p className="modal-hint">Syncing with GitHub…</p>}
            {sync.data && <SyncSummary result={sync.data} />}
          </>
        )}
      </div>

      <div className="modal-footer">
        <Button variant="secondary" onClick={onClose}>
          Close
        </Button>
        <Button
          variant="primary"
          disabled={!slug || sync.isPending}
          onClick={() => sync.mutate({ import_parent_ticket_id: parentId ?? "", import_label: label.trim() })}
        >
          {sync.isPending ? "Syncing…" : parentId ? "Sync and import" : "Sync now"}
        </Button>
      </div>
    </ModalShell>
  );
}
