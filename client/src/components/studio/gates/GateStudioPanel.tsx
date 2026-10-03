import { useQuery } from "@tanstack/react-query";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
  type ReactElement,
} from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";

import { api } from "../../../api/client";
import type { OrchestrationProfileView } from "../../../api/gateTypes";
import type { StudioWorkflow, WorkspaceSummary } from "../../../api/types";
import {
  gateStudioPath,
  gateStudioTargetFromPath,
  type GateStudioTarget,
} from "../../../lib/appNavigation";
import { describeError, errorStatus } from "../../../state/toastStore";
import { Button } from "../../ui/Button";
import { WorkspaceGatesPanel } from "../WorkspaceGatesPanel";
import "../WorkspaceGatesPanel.css";
import "./GateStudio.css";
import { GateControlDetail } from "./GateControlDetail";
import { GATE_CONTROL_COPY, type GateControlKind } from "./gateControlKinds";
import {
  collectWorkflowControls,
  collectWorkspaceControls,
  findGateControl,
  runsHere,
  type GateControl,
} from "./gateControlModel";

/** The kinds a workflow view lists, in this fixed order. */
const WORKFLOW_KINDS: GateControlKind[] = [
  "workflow_gate_stage",
  "stage_exit_action",
  "agent_handoff_check",
];

type LinkFn = (
  next: GateStudioTarget,
  label: string,
  extra?: { current?: boolean; badge?: string; inline?: boolean },
) => ReactElement;

interface ProfileState {
  data: OrchestrationProfileView | undefined;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
}

/**
 * Gate Studio: what can stop or redirect a handoff in a workspace and its
 * workflows, where each is configured, and what it does (lg-gate-studio-863).
 *
 * Selection lives in the URL, so a deep link renders what a click would. The
 * transition-command editor (#426) is the workspace-wide view and the detail of
 * every transition-command control. While it holds unsaved edits, following a
 * link here asks first, and leaving the page raises the browser's own prompt.
 * Back/forward cannot be intercepted: the app uses BrowserRouter, which has no
 * navigation blocker.
 */
export function GateStudioPanel({
  workspaces,
  workspacesLoading,
  workspacesError,
  onRetryWorkspaces,
  activeWorkspaceSlug,
}: {
  workspaces: WorkspaceSummary[];
  workspacesLoading: boolean;
  /** A failed workspace list is not an empty one: said, with a retry. */
  workspacesError: unknown;
  onRetryWorkspaces: () => void;
  /** The app-wide workspace, where a bare /studio/gates opens. */
  activeWorkspaceSlug?: string;
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const target = gateStudioTargetFromPath(location.pathname);
  const [dirty, setDirty] = useState(false);
  const [pending, setPending] = useState<GateStudioTarget | null>(null);
  const banner = useRef<HTMLDivElement>(null);
  const keepEditing = useRef<HTMLButtonElement>(null);

  // The banner renders above the editor, which is usually scrolled past by the
  // time an edit is made; bring it into view and onto the keyboard, or the
  // blocked click looks like a link that does nothing.
  useEffect(() => {
    if (!pending) return;
    // `scrollIntoView?.` because jsdom has no implementation of it.
    banner.current?.scrollIntoView?.({ block: "start" });
    keepEditing.current?.focus({ preventScroll: true });
  }, [pending]);

  const workspace = workspaces.find((ws) => ws.slug === target.workspaceSlug) ?? null;
  const profile = useQuery({
    queryKey: ["orchestration-profile", target.workspaceSlug],
    queryFn: () => api.orchestrationProfile(target.workspaceSlug ?? ""),
    enabled: Boolean(workspace),
  });
  const workflows = useQuery({ queryKey: ["studio-workflows"], queryFn: api.studioWorkflows });
  const agents = useQuery({ queryKey: ["studio-agents"], queryFn: api.studioAgents });

  useEffect(() => {
    if (!dirty) return undefined;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const guard = useCallback(
    (next: GateStudioTarget, event: MouseEvent) => {
      if (!dirty) return;
      event.preventDefault();
      setPending(next);
    },
    [dirty],
  );

  const sortedWorkflows = useMemo(() => {
    const list = workflows.data ?? [];
    if (!workspace) return list;
    return [...list].sort((a, b) => Number(runsHere(b, workspace)) - Number(runsHere(a, workspace)));
  }, [workflows.data, workspace]);
  const workflow = sortedWorkflows.find((wf) => wf.slug === target.workflowSlug) ?? null;

  const workspaceControls = useMemo(
    () => collectWorkspaceControls(workspace, profile.data ?? null),
    [workspace, profile.data],
  );
  const workflowControls = useMemo(
    () =>
      collectWorkflowControls({
        workspace,
        profile: profile.data ?? null,
        workflow,
        agents: agents.data ?? null,
      }),
    [workspace, profile.data, workflow, agents.data],
  );

  if (!target.workspaceSlug && workspaces.length > 0) {
    const opening = workspaces.find((ws) => ws.slug === activeWorkspaceSlug) ?? workspaces[0];
    return <Navigate replace to={gateStudioPath({ ...target, workspaceSlug: opening.slug })} />;
  }

  const link: LinkFn = (next, label, extra) => (
    <Link
      to={gateStudioPath(next)}
      className={`gate-studio-link${extra?.inline ? " gate-studio-link--inline" : ""}${extra?.current ? " active" : ""}`}
      aria-current={extra?.current ? "page" : undefined}
      onClick={(event) => guard(next, event)}
    >
      <span>{label}</span>
      {extra?.badge && <span className="gate-studio-badge">{extra.badge}</span>}
    </Link>
  );

  return (
    <div className="studio-shell">
      <aside className="studio-library-rail gate-studio-rail" aria-label="Gate Studio navigation">
        <div className="studio-library-section-label">Workspaces</div>
        {workspacesLoading ? (
          <p className="studio-preview-hint">Loading workspaces…</p>
        ) : workspacesError ? (
          <div className="gate-load-error" role="alert">
            <p>Couldn't load the workspaces: {describeError(workspacesError, "the request failed")}</p>
            <Button variant="secondary" onClick={onRetryWorkspaces}>
              Try again
            </Button>
          </div>
        ) : workspaces.length === 0 ? (
          <p className="studio-preview-hint">No workspaces yet — add one from the Workspaces page.</p>
        ) : (
          <nav className="gate-studio-list" aria-label="Workspaces">
            {workspaces.map((ws) => (
              <div key={ws.slug}>
                {link({ workspaceSlug: ws.slug, workflowSlug: null, controlId: null }, ws.name || ws.slug, {
                  current: ws.slug === target.workspaceSlug,
                })}
              </div>
            ))}
          </nav>
        )}

        {workspace && (
          <>
            <div className="studio-library-section-label">Workspace-wide</div>
            <nav className="gate-studio-list" aria-label={`Workspace-wide controls for ${workspace.slug}`}>
              {link({ workspaceSlug: workspace.slug, workflowSlug: null, controlId: null }, "Transition commands", {
                current: !target.workflowSlug && !target.controlId,
              })}
            </nav>
            <div className="studio-library-section-label">Workflows</div>
            <WorkflowRail
              workspace={workspace}
              workflows={sortedWorkflows}
              loading={workflows.isLoading}
              error={workflows.isError ? workflows.error : null}
              onRetry={() => workflows.refetch()}
              currentSlug={target.workflowSlug}
              link={link}
            />
          </>
        )}
      </aside>

      <div className="studio-editor studio-editor--gates">
        <div className="studio-editor-inner studio-editor-inner--gates">
          {pending && (
            <div ref={banner} className="gate-banner" role="alert">
              <span>
                You have unsaved gate changes for {target.workspaceSlug}. Leave and discard them?
              </span>
              <div className="gate-banner-actions">
                <Button ref={keepEditing} variant="secondary" onClick={() => setPending(null)}>
                  Keep editing
                </Button>
                <Button
                  variant="secondary"
                  className="gate-btn-danger"
                  onClick={() => {
                    setDirty(false);
                    setPending(null);
                    navigate(gateStudioPath(pending));
                  }}
                >
                  Discard and leave
                </Button>
              </div>
            </div>
          )}
          <Breadcrumb
            target={target}
            workflow={workflow}
            controlTitle={
              target.controlId
                ? findGateControl(target.workflowSlug ? workflowControls : workspaceControls, target.controlId)
                    ?.title ?? target.controlId
                : null
            }
            link={link}
          />
          <GateStudioMain
            target={target}
            workspace={workspace}
            workspacesLoading={workspacesLoading}
            workspacesFailed={Boolean(workspacesError)}
            profile={profile}
            workflow={workflow}
            workflowsLoading={workflows.isLoading}
            workspaceControls={workspaceControls}
            workflowControls={workflowControls}
            agentsLoading={agents.isLoading}
            agentsError={agents.isError ? agents.error : null}
            onDirtyChange={setDirty}
            link={link}
          />
        </div>
      </div>
    </div>
  );
}

function WorkflowRail({
  workspace,
  workflows,
  loading,
  error,
  onRetry,
  currentSlug,
  link,
}: {
  workspace: WorkspaceSummary;
  workflows: StudioWorkflow[];
  loading: boolean;
  error: unknown;
  onRetry: () => void;
  currentSlug: string | null;
  link: LinkFn;
}) {
  if (loading) return <p className="studio-preview-hint">Loading workflows for {workspace.slug}…</p>;
  if (error) {
    return (
      <div className="gate-load-error" role="alert">
        <p>
          Couldn't load the workflows for {workspace.slug}: {describeError(error, "the request failed")}
        </p>
        <Button variant="secondary" onClick={onRetry}>
          Try again
        </Button>
      </div>
    );
  }
  if (workflows.length === 0) {
    return <p className="studio-preview-hint">No workflows yet — create one in Workflow Studio.</p>;
  }
  return (
    <nav className="gate-studio-list" aria-label={`Workflows for ${workspace.slug}`}>
      {workflows.map((wf) => (
        <div key={wf.slug}>
          {link({ workspaceSlug: workspace.slug, workflowSlug: wf.slug, controlId: null }, wf.name || wf.slug, {
            current: wf.slug === currentSlug,
            badge: runsHere(wf, workspace) ? "Runs here" : undefined,
          })}
        </div>
      ))}
    </nav>
  );
}

function Breadcrumb({
  target,
  workflow,
  controlTitle,
  link,
}: {
  target: GateStudioTarget;
  workflow: StudioWorkflow | null;
  controlTitle: string | null;
  link: LinkFn;
}) {
  if (!target.workspaceSlug) return null;
  const scope = { workspaceSlug: target.workspaceSlug, workflowSlug: target.workflowSlug, controlId: null };
  return (
    <nav className="gate-studio-breadcrumb" aria-label="Breadcrumb">
      {link({ workspaceSlug: target.workspaceSlug, workflowSlug: null, controlId: null }, target.workspaceSlug)}
      <span aria-hidden="true">›</span>
      {link(scope, target.workflowSlug ? workflow?.name || target.workflowSlug : "Workspace-wide")}
      {controlTitle && (
        <>
          <span aria-hidden="true">›</span>
          <span aria-current="page">{controlTitle}</span>
        </>
      )}
    </nav>
  );
}

function GateStudioMain({
  target,
  workspace,
  workspacesLoading,
  workspacesFailed,
  profile,
  workflow,
  workflowsLoading,
  workspaceControls,
  workflowControls,
  agentsLoading,
  agentsError,
  onDirtyChange,
  link,
}: {
  target: GateStudioTarget;
  workspace: WorkspaceSummary | null;
  workspacesLoading: boolean;
  workspacesFailed: boolean;
  profile: ProfileState;
  workflow: StudioWorkflow | null;
  workflowsLoading: boolean;
  workspaceControls: GateControl[];
  workflowControls: GateControl[];
  agentsLoading: boolean;
  agentsError: unknown;
  onDirtyChange: (dirty: boolean) => void;
  link: LinkFn;
}) {
  if (workspacesLoading) return <p className="studio-preview-hint">Loading workspaces…</p>;
  if (workspacesFailed) {
    return <p className="studio-preview-hint">Gate Studio needs the workspace list — see the error beside it.</p>;
  }
  if (!target.workspaceSlug) {
    return <p className="studio-preview-hint">No workspaces yet — add one from the Workspaces page.</p>;
  }
  if (!workspace) {
    return (
      <div className="gate-load-error" role="alert">
        <p>No workspace named {target.workspaceSlug}. Pick one from the list.</p>
      </div>
    );
  }
  if (profile.isError && errorStatus(profile.error) === 403) {
    return (
      <div className="gate-load-error" role="alert">
        <p>You don't have permission to read the gate settings for {workspace.slug}.</p>
      </div>
    );
  }
  if (target.workflowSlug && !workflow) {
    return workflowsLoading ? (
      <p className="studio-preview-hint">Loading workflow {target.workflowSlug}…</p>
    ) : (
      <div className="gate-load-error" role="alert">
        <p>No workflow named {target.workflowSlug} for {workspace.slug}. Pick one from the list.</p>
      </div>
    );
  }

  if (target.controlId) {
    return (
      <ControlView
        target={target}
        workspace={workspace}
        profile={profile}
        controls={target.workflowSlug ? workflowControls : workspaceControls}
        onDirtyChange={onDirtyChange}
        link={link}
      />
    );
  }

  if (!target.workflowSlug) {
    return (
      <div className="gate-studio-sections">
        <h2 className="gate-studio-title">What can stop a handoff anywhere in {workspace.slug}</h2>
        <p className="gate-hint">{GATE_CONTROL_COPY.workspace_transition_command.explanation}</p>
        <WorkspaceGatesPanel workspaceSlug={workspace.slug} onDirtyChange={onDirtyChange} />
      </div>
    );
  }

  const workflowSlug = target.workflowSlug;
  return (
    <div className="gate-studio-sections">
      <h2 className="gate-studio-title">What can stop a handoff in {workflow?.name || workflowSlug}</h2>
      <p className="gate-hint">
        Transition commands also run at every transition —{" "}
        {link({ workspaceSlug: workspace.slug, workflowSlug: null, controlId: null }, "see the workspace-wide view", {
          inline: true,
        })}
        .
      </p>
      {WORKFLOW_KINDS.map((kind) => (
        <ControlSection
          key={kind}
          kind={kind}
          controls={workflowControls.filter((control) => control.kind === kind)}
          loading={kind === "agent_handoff_check" && agentsLoading}
          error={kind === "agent_handoff_check" ? agentsError : null}
          workspaceSlug={workspace.slug}
          workflowSlug={workflowSlug}
          link={link}
        />
      ))}
    </div>
  );
}

function ControlView({
  target,
  workspace,
  profile,
  controls,
  onDirtyChange,
  link,
}: {
  target: GateStudioTarget;
  workspace: WorkspaceSummary;
  profile: ProfileState;
  controls: GateControl[];
  onDirtyChange: (dirty: boolean) => void;
  link: LinkFn;
}) {
  if (!target.workflowSlug && profile.isLoading) {
    return <p className="studio-preview-hint">Loading gate settings for {workspace.slug}…</p>;
  }
  const control = target.controlId ? findGateControl(controls, target.controlId) : null;
  if (!control) {
    return (
      <div className="gate-load-error" role="alert">
        <p>
          Nothing named {target.controlId} in{" "}
          {target.workflowSlug ?? `the workspace-wide controls of ${workspace.slug}`} — it may have
          been removed.
        </p>
        {link({ workspaceSlug: workspace.slug, workflowSlug: target.workflowSlug, controlId: null }, "Back to the list")}
      </div>
    );
  }
  if (control.kind === "workspace_transition_command") {
    return (
      <>
        <GateControlDetail control={control} workflowSlug={null} />
        <WorkspaceGatesPanel workspaceSlug={workspace.slug} onDirtyChange={onDirtyChange} />
      </>
    );
  }
  return <GateControlDetail control={control} workflowSlug={target.workflowSlug} />;
}

function ControlSection({
  kind,
  controls,
  loading,
  error,
  workspaceSlug,
  workflowSlug,
  link,
}: {
  kind: GateControlKind;
  controls: GateControl[];
  loading: boolean;
  error: unknown;
  workspaceSlug: string;
  workflowSlug: string;
  link: LinkFn;
}) {
  const copy = GATE_CONTROL_COPY[kind];
  const headingId = `gate-section-${kind}`;
  return (
    <section className="gate-card" aria-labelledby={headingId}>
      <div className="gate-card-head">
        <h3 id={headingId}>{copy.plural}</h3>
        <span className={`gate-studio-tone gate-studio-tone--${copy.tone}`}>{copy.enforcementBadge}</span>
      </div>
      {loading ? (
        <p className="studio-preview-hint">Loading the agents {workflowSlug} runs…</p>
      ) : error ? (
        <p className="gate-load-error" role="alert">
          Couldn't load the agents for {workflowSlug}: {describeError(error, "the request failed")}
        </p>
      ) : controls.length === 0 ? (
        <p className="studio-preview-hint">{copy.emptyText}</p>
      ) : (
        <ul className="gate-studio-controls">
          {controls.map((control) => (
            <li key={control.id}>
              {link({ workspaceSlug, workflowSlug, controlId: control.id }, control.title)}
              <span className="gate-studio-value">{control.value}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
