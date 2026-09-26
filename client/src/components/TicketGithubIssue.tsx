import { type ReactNode, useState } from "react";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { TicketDetail } from "../api/client";
import {
  githubIssueApi,
  type GithubConflictPolicy,
  type GithubLinkSyncResult,
  type GithubSyncField,
} from "../api/githubIssueApi";
import { describeError, pushToast } from "../state/toastStore";

interface TicketGithubIssueProps {
  ticket: TicketDetail;
}

const FIELD_LABEL: Record<GithubSyncField, string> = {
  title: "title",
  body: "description",
  closure: "open/closed state",
};

function fieldList(fields: GithubSyncField[]): string {
  return fields.map((field) => FIELD_LABEL[field]).join(", ");
}

/** One line saying what the last sync moved, in which direction. */
function describeSync(result: GithubLinkSyncResult): string {
  const parts: string[] = [];
  if (result.pulled.length) parts.push(`Pulled ${fieldList(result.pulled)} from GitHub`);
  if (result.pushed.length) parts.push(`Pushed ${fieldList(result.pushed)} to GitHub`);
  if (!parts.length && !result.conflicts.length) return "Already in sync.";
  return parts.join(" · ");
}

/** The ticket's two-way link to a GitHub issue: publish, sync, resolve, unlink.
 *
 * A field changed on only one side since the last sync moves to the other on
 * sync; a field changed on both is shown as a conflict and left alone until the
 * operator picks a side. */
export function TicketGithubIssue({ ticket }: TicketGithubIssueProps) {
  const qc = useQueryClient();
  const [lastResult, setLastResult] = useState<GithubLinkSyncResult | null>(null);
  const linkKey = ["ticket", ticket.id, "github-issue"];

  const linkQuery = useQuery({
    queryKey: linkKey,
    queryFn: () => githubIssueApi.link(ticket.id),
  });

  const settle = (result: GithubLinkSyncResult) => {
    setLastResult(result);
    if (result.error) {
      pushToast({ tone: "error", title: "GitHub sync failed", message: result.error });
    }
    qc.invalidateQueries({ queryKey: ["ticket", ticket.id] });
    qc.invalidateQueries({ queryKey: ["tickets"] });
  };

  const publish = useMutation({
    meta: { errorTitle: "Publish to GitHub" },
    mutationFn: () => githubIssueApi.publish(ticket.id),
    onSuccess: settle,
  });

  const sync = useMutation({
    meta: { errorTitle: "Sync with GitHub" },
    mutationFn: (policy: GithubConflictPolicy) => githubIssueApi.sync(ticket.id, policy),
    onSuccess: settle,
  });

  const unlink = useMutation({
    meta: { errorTitle: "Unlink GitHub issue" },
    mutationFn: () => githubIssueApi.unlink(ticket.id),
    onSuccess: () => {
      setLastResult(null);
      qc.invalidateQueries({ queryKey: linkKey });
    },
  });

  const busy = publish.isPending || sync.isPending || unlink.isPending;
  const link = linkQuery.data;
  const conflicts = lastResult?.conflicts ?? [];

  let body: ReactNode;
  if (linkQuery.isPending) {
    body = <p className="modal-hint">Checking for a linked issue…</p>;
  } else if (linkQuery.isError) {
    body = (
      <p className="modal-hint" style={{ color: "var(--red)" }}>
        Could not read the GitHub link: {describeError(linkQuery.error, "request failed")}
      </p>
    );
  } else if (!link) {
    body = (
      <>
        <p className="modal-hint" style={{ marginTop: 4 }}>
          Not linked. Publishing opens an issue in this workspace's repository and keeps its
          title, description and open/closed state in sync with this ticket.
        </p>
        <button
          type="button"
          className="btn-secondary btn-compact"
          style={{ marginTop: 6 }}
          disabled={busy}
          onClick={() => publish.mutate()}
        >
          {publish.isPending ? "Publishing…" : "Publish to GitHub"}
        </button>
      </>
    );
  } else {
    body = (
      <>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4 }}>
          <a href={link.issue_url} target="_blank" rel="noreferrer">
            {link.repo}#{link.issue_number}
          </a>
          <span className="modal-hint">
            synced {new Date(link.last_synced_at).toLocaleString()}
          </span>
        </div>
        {link.last_error && (
          <p className="modal-hint" style={{ marginTop: 4, color: "var(--red)" }}>
            Last sync failed: {link.last_error}
          </p>
        )}
        <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
          <button
            type="button"
            className="btn-secondary btn-compact"
            disabled={busy}
            onClick={() => sync.mutate("report")}
          >
            {sync.isPending ? "Syncing…" : "Sync now"}
          </button>
          <button
            type="button"
            className="btn-secondary btn-compact"
            disabled={busy}
            onClick={() => unlink.mutate()}
          >
            Unlink
          </button>
        </div>
        {lastResult && !lastResult.error && (
          <p className="modal-hint" style={{ marginTop: 6 }}>
            {describeSync(lastResult)}
          </p>
        )}
        {conflicts.length > 0 && (
          <div style={{ marginTop: 6 }}>
            <p className="modal-hint" style={{ color: "var(--red)", margin: 0 }}>
              Changed on both sides since the last sync: {fieldList(conflicts.map((c) => c.field))}.
            </p>
            <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
              <button
                type="button"
                className="btn-secondary btn-compact"
                disabled={busy}
                onClick={() => sync.mutate("local")}
              >
                Keep ticket's version
              </button>
              <button
                type="button"
                className="btn-secondary btn-compact"
                disabled={busy}
                onClick={() => sync.mutate("remote")}
              >
                Keep GitHub's version
              </button>
            </div>
          </div>
        )}
      </>
    );
  }

  return (
    <div className="state-card">
      <div className="state-label">GitHub issue</div>
      {body}
    </div>
  );
}
