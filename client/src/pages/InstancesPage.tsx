import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useMemo, useState } from "react";

import { localInstancesApi } from "../api/localInstancesApi";
import type { LocalInstance, LocalInstanceState } from "../api/localInstancesTypes";
import { WorkspaceSetupCard } from "../components/instances/WorkspaceSetupCard";
import { LocalInstanceLaunchForm } from "../components/LocalInstanceLaunchForm";
import { LocalInstanceRow } from "../components/LocalInstanceRow";
import { PageTopbar } from "../components/TopbarPageSlot";
import { INTEGRATION_KEY, useLocalInstances, WORKSPACE_TEMPLATES_KEY } from "../hooks/useLocalInstances";
import "../components/LocalInstancesModal.css";
import "./InstancesPage.css";

const ALL = "";
const STATES: LocalInstanceState[] = ["starting", "ready", "stalled", "exited"];

/**
 * Every workspace's local instances, launch, and the templates behind them.
 *
 * The topbar modal is the quick view of the same data. Here: filter across
 * workspaces, launch from any workspace's templates, define templates —
 * committed in a workspace's `.loregarden/instances.yaml`, or saved here — and
 * install loregarden's gates and agent instructions into each workspace.
 */
export function InstancesPage() {
  const workspaceFilterId = useId();
  const stateFilterId = useId();
  const [workspace, setWorkspace] = useState(ALL);
  const [state, setState] = useState<LocalInstanceState | typeof ALL>(ALL);
  const queryClient = useQueryClient();
  const { instances, templates, launch, stop, stopping, targetName } = useLocalInstances(true);
  const workspaces = useQuery({
    queryKey: WORKSPACE_TEMPLATES_KEY,
    queryFn: localInstancesApi.workspaceTemplates,
  });

  const shown = useMemo(
    () =>
      (instances.data?.instances ?? []).filter(
        (i) => (workspace === ALL || i.project === workspace) && (state === ALL || i.state === state),
      ),
    [instances.data, workspace, state],
  );
  const groups = useMemo(() => {
    const byProject = new Map<string, LocalInstance[]>();
    for (const instance of shown) byProject.set(instance.project, [...(byProject.get(instance.project) ?? []), instance]);
    return [...byProject.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [shown]);

  const selected = workspaces.data?.find((w) => w.slug === workspace);
  const launchable = (templates.data ?? []).filter((t) => workspace === ALL || t.name.startsWith(`${workspace}/`));
  const total = instances.data?.instances.length ?? 0;

  return (
    <div className="screen-view screen-view--instances">
      <PageTopbar title="Instances">
        <span className="topbar-page-note">Servers and clients on their own ports, for every workspace</span>
        <button
          type="button"
          className="btn-secondary topbar-page-btn"
          disabled={instances.isFetching}
          onClick={() => {
            void instances.refetch();
            void workspaces.refetch();
            void queryClient.invalidateQueries({ queryKey: INTEGRATION_KEY });
          }}
        >
          {instances.isFetching ? "Refreshing…" : "Refresh"}
        </button>
      </PageTopbar>

      <div className="instances-page-body">
        <div className="instances-top">
          <section className="instances-running" aria-labelledby="instances-running-title" aria-busy={instances.isPending}>
            <header className="instances-running-head">
              <h2 id="instances-running-title">
                Running <span className="instances-count">{total}</span>
              </h2>
              <div className="instances-filters">
                <label className="visually-hidden" htmlFor={workspaceFilterId}>
                  Workspace
                </label>
                <select id={workspaceFilterId} className="input" value={workspace} onChange={(e) => setWorkspace(e.target.value)}>
                  <option value={ALL}>All workspaces</option>
                  {(workspaces.data ?? []).map((w) => (
                    <option key={w.slug} value={w.slug}>
                      {w.name}
                    </option>
                  ))}
                </select>
                <label className="visually-hidden" htmlFor={stateFilterId}>
                  State
                </label>
                <select
                  id={stateFilterId}
                  className="input"
                  value={state}
                  onChange={(e) => setState(e.target.value as LocalInstanceState | typeof ALL)}
                >
                  <option value={ALL}>Any state</option>
                  {STATES.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </div>
            </header>
            {instances.error && (
              <p className="instances-error" role="alert">
                Could not load instances: {instances.error.message}
                {instances.data ? " — showing the last list that loaded." : ""}
              </p>
            )}
            {instances.data?.unreadable.map((bad) => (
              <p key={bad.path} className="instances-error">
                Unreadable registry record {bad.path}: {bad.error}
              </p>
            ))}
            {instances.isPending && !instances.error ? (
              <div className="local-instances-list" aria-label="Loading instances">
                <div className="local-instances-skeleton" />
                <div className="local-instances-skeleton" />
              </div>
            ) : groups.length === 0 && instances.data ? (
              <p className="modal-hint">
                {total === 0
                  ? "Nothing is running. Main servers appear once started with `task server`; launch anything else with “Launch an instance”."
                  : "No instance matches these filters."}
              </p>
            ) : (
              groups.map(([project, rows]) => (
                <div key={project} className="instances-group">
                  <h3>{project}</h3>
                  <ul className="local-instances-list">
                    {rows.map((instance) => (
                      <LocalInstanceRow
                        key={instance.id}
                        instance={instance}
                        targetName={targetName(instance)}
                        stopping={stopping.has(instance.id)}
                        onStop={stop}
                      />
                    ))}
                  </ul>
                </div>
              ))
            )}
          </section>

          <section className="instances-launch" aria-labelledby="instances-launch-title">
            <h2 id="instances-launch-title">Launch an instance</h2>
            <p className="instances-meta">
              A server or client on its own port, from a worktree — for trying a branch without touching main.
            </p>
            {templates.error ? (
              <p className="instances-error" role="alert">
                Could not load templates: {templates.error.message}
              </p>
            ) : !templates.data ? (
              <div className="local-instances-skeleton" aria-label="Loading templates" />
            ) : launchable.length === 0 ? (
              <p className="modal-hint">
                {workspace === ALL
                  ? "No workspace has a launchable template yet. Add one under Workspace setup."
                  : `${selected?.name ?? workspace} has no launchable template yet. Add one under Workspace setup.`}
              </p>
            ) : (
              <LocalInstanceLaunchForm
                key={workspace}
                templates={launchable}
                launching={launch.isPending}
                onLaunch={(body) => launch.mutate(body)}
              />
            )}
          </section>
        </div>

        <section className="instances-template-list" aria-labelledby="instances-templates-title" aria-busy={workspaces.isPending}>
          <h2 id="instances-templates-title">Workspace setup</h2>
          <p className="instances-meta">
            What each workspace has of loregarden — gates, agent instructions, launch templates. Open one to install or
            edit.
          </p>
          {workspaces.error ? (
            <p className="instances-error" role="alert">
              Could not load workspace templates: {workspaces.error.message}
            </p>
          ) : workspaces.isPending ? (
            <div className="local-instances-skeleton" aria-label="Loading workspace templates" />
          ) : (workspaces.data ?? []).length === 0 ? (
            <p className="modal-hint">No workspaces yet. Add one from the sidebar, then define its templates here.</p>
          ) : (
            <ul className="instances-setup-list">
              {(workspaces.data ?? [])
                .filter((w) => workspace === ALL || w.slug === workspace)
                .map((w) => (
                  <WorkspaceSetupCard key={w.slug} workspace={w} />
                ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
