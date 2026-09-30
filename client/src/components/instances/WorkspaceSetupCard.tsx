import { useQuery } from "@tanstack/react-query";

import { localInstancesApi } from "../../api/localInstancesApi";
import type { InstallState, WorkspaceTemplates } from "../../api/localInstancesTypes";
import type { WorkspaceSummary } from "../../api/types";
import { INTEGRATION_KEY } from "../../hooks/useLocalInstances";
import { WorkspaceIntegrationPanel } from "./WorkspaceIntegrationPanel";
import { WorkspaceTemplatesPanel } from "./WorkspaceTemplatesPanel";

const CHECK_STALE_MS = 60_000;

type Tone = "ok" | "warn" | "muted";

const INSTALL_TONE: Record<InstallState, Tone> = {
  current: "ok",
  missing: "warn",
  outdated: "warn",
  unavailable: "muted",
};

const INSTALL_WORD: Record<InstallState, string> = {
  current: "installed",
  missing: "missing",
  outdated: "outdated",
  unavailable: "n/a",
};

function Chip({ tone, children }: { tone: Tone; children: React.ReactNode }) {
  return <span className={`instances-chip instances-chip--${tone}`}>{children}</span>;
}

/**
 * One workspace's loregarden setup, folded to a single row.
 *
 * The page used to render every workspace's setup table *and* template table
 * open, one after another — eight full-width panels to scroll past before
 * anything answered "is this workspace set up?". The summary answers that in
 * chips; the panels are one click away and unchanged. Shares the integration
 * query (and its cache) with `WorkspaceIntegrationPanel`, so opening a card
 * does not re-check.
 */
interface WorkspaceSetupCardProps {
  workspace: WorkspaceTemplates;
  /** Its row from `/api/workspaces`, for ticket counts and archive state; absent while loading. */
  summary: WorkspaceSummary | undefined;
  /** In flight for this workspace: the archive/restore button shows it and stays disabled. */
  archiving: boolean;
  onArchive: (archive: boolean) => void;
}

export function WorkspaceSetupCard({ workspace, summary, archiving, onArchive }: WorkspaceSetupCardProps) {
  const status = useQuery({
    queryKey: [...INTEGRATION_KEY, workspace.slug],
    queryFn: () => localInstancesApi.integration(workspace.slug),
    staleTime: CHECK_STALE_MS,
  });
  const launchable = workspace.entries.filter((entry) => entry.launchable).length;

  return (
    <li className="instances-setup">
      <details>
        <summary>
          <span className="instances-setup-name">
            {workspace.name}
            <span className="instances-setup-path">{workspace.repo_root}</span>
          </span>
          <span className="instances-setup-chips">
            {status.isPending ? (
              <Chip tone="muted">checking…</Chip>
            ) : status.error ? (
              <Chip tone="warn">check failed</Chip>
            ) : status.data.installers.length === 0 ? (
              <Chip tone="muted">no installers reported</Chip>
            ) : (
              status.data.installers.map((installer) => (
                <Chip key={installer.installer} tone={INSTALL_TONE[installer.state]}>
                  {installer.installer === "hooks" ? "gates" : "AGENTS.md"} {INSTALL_WORD[installer.state]}
                </Chip>
              ))
            )}
            {summary && !summary.repo_exists && <Chip tone="warn">repo missing</Chip>}
            {summary && (
              <Chip tone={summary.blocked_count > 0 ? "warn" : "muted"}>
                {summary.ticket_count} {summary.ticket_count === 1 ? "ticket" : "tickets"}
                {summary.blocked_count > 0 ? `, ${summary.blocked_count} blocked` : ""}
              </Chip>
            )}
            <Chip tone={workspace.file_error ? "warn" : launchable ? "ok" : "muted"}>
              {workspace.file_error ? "templates broken" : `${launchable} ${launchable === 1 ? "template" : "templates"}`}
            </Chip>
          </span>
        </summary>
        <div className="instances-setup-body">
          {summary && (
            <div className="instances-setup-actions">
              <span className="instances-meta">
                {summary.archived_at
                  ? "Archived — listed apart from active workspaces. Its tickets and runs are unaffected."
                  : "Archiving only moves it to the archived list; tickets, runs and instances are unaffected."}
              </span>
              <button
                type="button"
                className="btn-secondary"
                disabled={archiving}
                aria-busy={archiving}
                onClick={() => onArchive(!summary.archived_at)}
              >
                {summary.archived_at
                  ? archiving
                    ? "Restoring…"
                    : `Restore ${workspace.name}`
                  : archiving
                    ? "Archiving…"
                    : `Archive ${workspace.name}`}
              </button>
            </div>
          )}
          <WorkspaceIntegrationPanel workspace={workspace} />
          <WorkspaceTemplatesPanel workspace={workspace} />
        </div>
      </details>
    </li>
  );
}
