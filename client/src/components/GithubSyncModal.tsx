import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";

import { api } from "../api/client";
import {
  githubIssueApi,
  type GithubLinkSyncResult,
  type GithubWorkspaceSyncRequest,
  type GithubWorkspaceSyncResult,
} from "../api/githubIssueApi";
import { describeLinkSync, syncFieldList } from "../lib/githubSyncSummary";
import { describeError } from "../state/toastStore";
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
          <li key={row.ticket_id} style={{ fontSize: 13 }}>
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

/** Sync every linked ticket in a workspace with its GitHub issue, and
 * optionally import the repository's unlinked open issues under a parent. */
export function GithubSyncModal({ open, onClose }: GithubSyncModalProps) {
  const qc = useQueryClient();
  const activeWorkspace = useUiStore((s) => s.workspace);
  const workspaceFieldId = useId();
  const labelFieldId = useId();

  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces, enabled: open });
  const [chosenSlug, setChosenSlug] = useState("");
  const [parentId, setParentId] = useState<string | null>(null);
  const [label, setLabel] = useState("");

  const slugs = (workspaces.data ?? []).map((ws) => ws.slug);
  const defaultSlug = activeWorkspace !== "all" && slugs.includes(activeWorkspace) ? activeWorkspace : slugs[0] ?? "";
  const slug = chosenSlug || defaultSlug;

  const syncSettings = useQuery({
    queryKey: ["github-sync-settings", slug],
    queryFn: () => githubIssueApi.syncSettings(slug),
    enabled: open && Boolean(slug),
  });
  // The saved import parent and label are this workspace's defaults: prefill
  // them once per workspace, so "Sync now" and the schedule start from the same.
  const savedSettings = syncSettings.data;
  const [prefilledFor, setPrefilledFor] = useState("");
  useEffect(() => {
    if (!savedSettings || prefilledFor === savedSettings.workspace_slug) return;
    setPrefilledFor(savedSettings.workspace_slug);
    setParentId(savedSettings.import_parent_ticket_id || null);
    setLabel(savedSettings.import_label);
  }, [savedSettings, prefilledFor]);

  const sync = useMutation({
    meta: { errorTitle: "Sync GitHub issues" },
    mutationFn: (request: GithubWorkspaceSyncRequest) => githubIssueApi.syncWorkspace(slug, request),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["tickets"] });
      void qc.invalidateQueries({ queryKey: ["ticket"] });
    },
  });

  if (!open) {
    // Closed but mounted: the shell plays its exit with the last content it drew.
    return <ModalShell open={false} onDismiss={undefined} labelledBy="github-sync-title">{null}</ModalShell>;
  }

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
