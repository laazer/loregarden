import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../../api/client";
import { localInstancesApi } from "../../api/localInstancesApi";
import type { InstallState, WorkspaceTemplates } from "../../api/localInstancesTypes";
import type { WorkspaceSummary } from "../../api/types";
import { INITIALIZABLE_REPOSITORY_STATES, type RepositoryState } from "../../api/workspaceRepositoryTypes";
import { INTEGRATION_KEY, WORKSPACE_TEMPLATES_KEY } from "../../hooks/useLocalInstances";
import { NotThisTarget } from "../../lib/agentActions/registry";
import { useAgentAction } from "../../lib/agentActions/useAgentAction";
import { pushToast, toastActionFailed } from "../../state/toastStore";
import { WorkspaceGatePresetsPanel } from "../workspaces/WorkspaceGatePresetsPanel";
import { WorkspaceIntegrationPanel } from "./WorkspaceIntegrationPanel";
import { WorkspaceTemplatesPanel } from "./WorkspaceTemplatesPanel";

const CHECK_STALE_MS = 60_000;

type Tone = "ok" | "warn" | "muted";

const INSTALL_TONE: Record<InstallState, Tone> = {
  current: "ok",
  missing: "warn",
  outdated: "warn",
  unavailable: "muted",
  built_in: "ok",
};

const INSTALL_WORD: Record<InstallState, string> = {
  current: "installed",
  missing: "missing",
  outdated: "outdated",
  unavailable: "n/a",
  built_in: "built in",
};

/** What the card says when the workspace's path is not a repository; `repository` says nothing. */
const REPO_PROBLEM: Record<Exclude<RepositoryState, "repository">, { chip: string; explain: string }> = {
  missing: {
    chip: "repo missing",
    explain: "Nothing exists at this path yet. Create the repository here — with loregarden's gates and AGENTS.md committed — or point the workspace somewhere else.",
  },
  empty: {
    chip: "repo empty",
    explain: "This folder is empty. Create the repository here — with loregarden's gates and AGENTS.md committed.",
  },
  not_a_repository: {
    chip: "not a git repo",
    explain: "This folder has files but is not a git repository. Run `git init` there yourself; loregarden will not initialize a folder with someone's work in it.",
  },
  inside_repository: {
    chip: "inside another repo",
    explain: "This path is inside another git repository. Point the workspace at that repository's root instead.",
  },
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
  const [opened, setOpened] = useState(false);
  const queryClient = useQueryClient();
  const createRepository = useMutation({
    // Its own toast names the step; the global one would say "Action failed" beside it.
    meta: { suppressErrorToast: true },
    mutationFn: () => api.createWorkspaceRepository(workspace.slug),
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      void queryClient.invalidateQueries({ queryKey: [...INTEGRATION_KEY, workspace.slug] });
      void queryClient.invalidateQueries({ queryKey: WORKSPACE_TEMPLATES_KEY });
      pushToast(
        created.follow_up
          ? { tone: "warning", title: `Created ${workspace.name}'s repository`, message: created.follow_up }
          : { tone: "success", title: `Created ${workspace.name}'s repository`, message: created.repo_root },
      );
    },
    onError: (error) => toastActionFailed(`Create ${workspace.name}'s repository`, error),
  });
  const repoProblem = summary && summary.repo_state !== "repository" ? REPO_PROBLEM[summary.repo_state] : undefined;
  const initializable = summary !== undefined && INITIALIZABLE_REPOSITORY_STATES.has(summary.repo_state);

  // One card per workspace; each answers only for its own, and only when it
  // would offer the button itself.
  useAgentAction("workspace.create_repository", async ({ workspace_slug }) => {
    if (workspace_slug !== workspace.slug) throw new NotThisTarget(`no card for ${workspace_slug} is on screen`);
    if (!initializable) {
      throw new Error(`${workspace.slug}'s repository is ${summary?.repo_state ?? "unknown"}; there is nothing to create`);
    }
    const created = await createRepository.mutateAsync();
    return { workspace_slug: workspace.slug, repo_root: created.repo_root, follow_up: created.follow_up };
  });

  return (
    <li className="instances-setup">
      {/* Opened once, the gates panel stays mounted: collapsing keeps unsaved ticks. */}
      <details onToggle={(e) => e.currentTarget.open && setOpened(true)}>
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
            {repoProblem && <Chip tone="warn">{repoProblem.chip}</Chip>}
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
          {repoProblem && (
            <div className="instances-setup-actions" role="status">
              <span className="instances-meta instances-warning">{repoProblem.explain}</span>
              {initializable && (
                <button
                  type="button"
                  className="btn-primary"
                  disabled={createRepository.isPending}
                  aria-busy={createRepository.isPending}
                  onClick={() => {
                    if (createRepository.isPending) return;
                    if (!window.confirm(`Create a git repository at ${workspace.repo_root} and commit loregarden's gates and AGENTS.md to it?`)) return;
                    createRepository.mutate();
                  }}
                >
                  {createRepository.isPending ? "Creating repository…" : "Create repository"}
                </button>
              )}
            </div>
          )}
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
          {/* Mounted on first open: it scans the repository and reads the profile, which
              every collapsed card on the page doing at load would be paying for nothing. */}
          {opened && <WorkspaceGatePresetsPanel slug={workspace.slug} name={workspace.name} />}
          <WorkspaceTemplatesPanel workspace={workspace} />
        </div>
      </details>
    </li>
  );
}
