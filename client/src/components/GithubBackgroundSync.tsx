import { useMutation, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";

import {
  githubIssueApi,
  type GithubSyncSettings,
  type GithubSyncSettingsUpdate,
} from "../api/githubIssueApi";
import { describeError } from "../state/toastStore";

const INTERVALS = [5, 15, 30, 60, 180, 720, 1440];

function intervalLabel(minutes: number): string {
  if (minutes < 60) return `Every ${minutes} minutes`;
  const hours = minutes / 60;
  return hours === 1 ? "Every hour" : `Every ${hours} hours`;
}

interface GithubBackgroundSyncProps {
  workspaceSlug: string;
  settings: UseQueryResult<GithubSyncSettings>;
  /** The import parent and label chosen in the modal; saved with the schedule. */
  importParentTicketId: string;
  importLabel: string;
}

/** Turn the workspace's periodic GitHub sync on or off, and see its last run. */
export function GithubBackgroundSync({
  workspaceSlug,
  settings,
  importParentTicketId,
  importLabel,
}: GithubBackgroundSyncProps) {
  const qc = useQueryClient();
  const toggleId = useId();
  const intervalId = useId();
  const pushId = useId();
  const saved = settings.data;
  const [enabled, setEnabled] = useState(false);
  const [interval, setIntervalMinutes] = useState(15);
  const [pushOnEdit, setPushOnEdit] = useState(false);

  useEffect(() => {
    if (!saved) return;
    setEnabled(saved.enabled);
    setIntervalMinutes(saved.interval_minutes);
    setPushOnEdit(saved.push_on_edit);
  }, [saved]);

  const save = useMutation({
    meta: { errorTitle: "Save background sync" },
    mutationFn: (body: GithubSyncSettingsUpdate) => githubIssueApi.saveSyncSettings(workspaceSlug, body),
    onSuccess: (next) => qc.setQueryData(["github-sync-settings", workspaceSlug], next),
  });

  if (settings.isPending) return <p className="modal-hint">Loading background sync settings…</p>;
  if (settings.isError) {
    return (
      <p className="modal-hint" role="alert" style={{ color: "var(--red)" }}>
        Could not load background sync settings: {describeError(settings.error, "request failed")}
      </p>
    );
  }

  const dirty =
    !saved ||
    saved.enabled !== enabled ||
    saved.interval_minutes !== interval ||
    saved.push_on_edit !== pushOnEdit ||
    saved.import_parent_ticket_id !== importParentTicketId ||
    saved.import_label !== importLabel;
  const intervals = INTERVALS.includes(interval) ? INTERVALS : [...INTERVALS, interval].sort((a, b) => a - b);

  return (
    <div className="state-card">
      <div className="state-label">Automatic sync</div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4 }}>
        <input
          id={toggleId}
          type="checkbox"
          checked={enabled}
          disabled={save.isPending}
          onChange={(e) => setEnabled(e.target.checked)}
        />
        <label htmlFor={toggleId}>Sync this workspace automatically</label>
      </div>
      {enabled && (
        <div style={{ marginTop: 8 }}>
          <label className="field-label" htmlFor={intervalId}>
            How often
          </label>
          <select
            id={intervalId}
            className="input"
            value={interval}
            disabled={save.isPending}
            onChange={(e) => setIntervalMinutes(Number(e.target.value))}
          >
            {intervals.map((minutes) => (
              <option key={minutes} value={minutes}>
                {intervalLabel(minutes)}
              </option>
            ))}
          </select>
          <p className="modal-hint" style={{ marginTop: 4 }}>
            {importParentTicketId
              ? "Each run also imports new issues under the parent chosen above."
              : "Each run only syncs linked tickets. Choose a parent above to import new issues too."}
          </p>
        </div>
      )}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8 }}>
        <input
          id={pushId}
          type="checkbox"
          checked={pushOnEdit}
          disabled={save.isPending}
          onChange={(e) => setPushOnEdit(e.target.checked)}
        />
        <label htmlFor={pushId}>Push ticket edits to GitHub as they happen</label>
      </div>
      {pushOnEdit && (
        <p className="modal-hint" style={{ marginTop: 4 }}>
          A linked ticket's title, description or state is synced a couple of seconds after it
          changes. An issue edited on GitHub meanwhile is still reported as a conflict, not
          overwritten.
        </p>
      )}
      {saved?.last_run_at && (
        <p className="modal-hint" style={{ marginTop: 6 }}>
          Last background run {new Date(saved.last_run_at).toLocaleString()}
          {saved.last_error ? "" : " succeeded."}
        </p>
      )}
      {saved?.last_error && (
        <p className="modal-hint" style={{ marginTop: 4, color: "var(--red)" }}>
          Last background run failed: {saved.last_error}
        </p>
      )}
      <button
        type="button"
        className="btn-secondary btn-compact"
        style={{ marginTop: 8 }}
        disabled={!dirty || save.isPending}
        onClick={() =>
          save.mutate({
            enabled,
            interval_minutes: interval,
            push_on_edit: pushOnEdit,
            import_parent_ticket_id: importParentTicketId,
            import_label: importLabel,
          })
        }
      >
        {save.isPending ? "Saving…" : dirty ? "Save settings" : "Saved"}
      </button>
    </div>
  );
}
