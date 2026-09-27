import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { localInstancesApi } from "../../api/localInstancesApi";
import type {
  InstallState,
  WorkspaceInstaller,
  WorkspaceInstallerStatus,
  WorkspaceIntegration,
  WorkspaceTemplates,
} from "../../api/localInstancesTypes";
import { INSTANCES_KEY } from "../../hooks/useLocalInstances";
import { pushToast, toastActionFailed } from "../../state/toastStore";

/** Not under INSTANCES_KEY: each check runs two scripts, and instances poll. */
export const INTEGRATION_KEY = ["workspace-integration"] as const;
const CHECK_STALE_MS = 60_000;

const INSTALLERS: Record<WorkspaceInstaller, { label: string; file: string; what: string }> = {
  hooks: {
    label: "Pre-commit gates",
    file: "lefthook.yml",
    what: "loregarden's organization and silent-failure gates, for commits made by hand",
  },
  docs: {
    label: "Agent instructions",
    file: "AGENTS.md",
    what: "a section telling agents in this repo that tickets live in loregarden, and which tools reach them",
  },
};

const STATE_LABEL: Record<InstallState, string> = {
  current: "Installed",
  missing: "Not installed",
  outdated: "Out of date",
  unavailable: "Cannot install",
};

type Action = WorkspaceInstaller | "templates";

interface WorkspaceIntegrationPanelProps {
  workspace: WorkspaceTemplates;
}

/**
 * What a workspace has of loregarden: its gates in the repo's pre-commit, the
 * AGENTS.md section its agents read, and a committed templates file.
 *
 * Each action writes into the workspace's repository and leaves the change
 * uncommitted, so each asks first and says which file it touches.
 */
export function WorkspaceIntegrationPanel({ workspace }: WorkspaceIntegrationPanelProps) {
  const queryClient = useQueryClient();
  const key = [...INTEGRATION_KEY, workspace.slug];
  const status = useQuery({
    queryKey: key,
    queryFn: () => localInstancesApi.integration(workspace.slug),
    staleTime: CHECK_STALE_MS,
  });

  const act = useMutation({
    mutationFn: async (action: Action): Promise<WorkspaceIntegration | WorkspaceTemplates> =>
      action === "templates"
        ? localInstancesApi.writeTemplateFile(workspace.slug)
        : localInstancesApi.install(workspace.slug, action),
    onSuccess: (result, action) => {
      if (action === "templates") {
        pushToast({
          tone: "success",
          title: `Wrote ${workspace.template_file}`,
          message: "Review it and commit it with the repository.",
        });
        void queryClient.invalidateQueries({ queryKey: INSTANCES_KEY });
      } else {
        queryClient.setQueryData(key, result);
        pushToast({
          tone: "success",
          title: `${INSTALLERS[action].label} installed in ${workspace.name}`,
          message: `${INSTALLERS[action].file} changed; commit it with the repository.`,
        });
      }
    },
    onError: (error, action) =>
      toastActionFailed(action === "templates" ? "Write templates file" : `Install ${INSTALLERS[action].label}`, error),
  });

  const confirmAndRun = (action: Action, file: string) => {
    if (act.isPending) return;
    if (!window.confirm(`This writes ${file} in ${workspace.repo_root} and leaves it for you to commit. Continue?`)) return;
    act.mutate(action);
  };

  const movable = workspace.entries.filter((e) => e.origin === "stored" && e.spec && !e.shadowed_by);
  const busy = (action: Action) => act.isPending && act.variables === action;

  return (
    <section className="instances-integration" aria-labelledby={`integration-${workspace.slug}`}>
      <header className="instances-section-head">
        <h3 id={`integration-${workspace.slug}`}>{workspace.name} setup</h3>
        <button
          type="button"
          className="btn-secondary"
          disabled={status.isFetching}
          onClick={() => void status.refetch()}
        >
          {status.isFetching ? "Checking…" : "Check again"}
        </button>
      </header>
      {status.error && (
        <p className="instances-error" role="alert">
          Could not check {workspace.name}'s setup: {status.error.message}
        </p>
      )}
      <table className="instances-table">
        <caption className="visually-hidden">What {workspace.name} has installed from loregarden</caption>
        <thead>
          <tr>
            <th scope="col">Part</th>
            <th scope="col">File</th>
            <th scope="col">Status</th>
            <th scope="col">
              <span className="visually-hidden">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody aria-busy={status.isPending}>
          {(["hooks", "docs"] as const).map((installer) => (
            <InstallerRow
              key={installer}
              installer={installer}
              found={status.data?.installers.find((i) => i.installer === installer)}
              loading={status.isPending && !status.error}
              busy={busy(installer)}
              disabled={act.isPending}
              onInstall={() => confirmAndRun(installer, INSTALLERS[installer].file)}
            />
          ))}
          <tr>
            <td>
              <strong>Launch templates</strong>
              <div className="instances-meta">Templates committed with the repo, reviewed like code</div>
            </td>
            <td>
              <code>.loregarden/instances.yaml</code>
            </td>
            <td className={workspace.file_exists && !workspace.file_error ? undefined : "instances-warning"}>
              {workspace.file_error
                ? "Present, but cannot be used — see its templates below"
                : workspace.file_exists
                  ? "Present"
                  : movable.length > 0
                    ? `Not in the repo; ${movable.length} saved here can be written to it`
                    : "Not in the repo. Save a template below, then write it here — or commit the file yourself."}
            </td>
            <td className="instances-actions">
              {!workspace.file_exists && movable.length > 0 && (
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={act.isPending}
                  aria-busy={busy("templates")}
                  title={`Moves ${movable.map((e) => e.name).join(", ")} from saved templates into the file`}
                  onClick={() => confirmAndRun("templates", ".loregarden/instances.yaml")}
                >
                  {busy("templates") ? "Writing…" : "Write file"}
                </button>
              )}
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  );
}

interface InstallerRowProps {
  installer: WorkspaceInstaller;
  found: WorkspaceInstallerStatus | undefined;
  loading: boolean;
  busy: boolean;
  disabled: boolean;
  onInstall: () => void;
}

function InstallerRow({ installer, found, loading, busy, disabled, onInstall }: InstallerRowProps) {
  const { label, file, what } = INSTALLERS[installer];
  const installable = found?.state === "missing" || found?.state === "outdated";
  return (
    <tr>
      <td>
        <strong>{label}</strong>
        <div className="instances-meta">{what}</div>
      </td>
      <td>
        <code>{file}</code>
      </td>
      <td className={found && found.state !== "current" ? "instances-warning" : undefined}>
        {loading ? (
          <div className="local-instances-skeleton" aria-label={`Checking ${label}`} />
        ) : found ? (
          <>
            {STATE_LABEL[found.state]}
            {found.state === "unavailable" && <div className="instances-meta">{found.detail}</div>}
          </>
        ) : (
          "Unknown"
        )}
      </td>
      <td className="instances-actions">
        {installable && (
          <button type="button" className="btn-secondary" disabled={disabled} aria-busy={busy} onClick={onInstall}>
            {busy ? "Installing…" : found?.state === "outdated" ? "Update" : "Install"}
          </button>
        )}
      </td>
    </tr>
  );
}
