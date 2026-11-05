import { useQuery } from "@tanstack/react-query";
import { NavLink } from "react-router-dom";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import { AgentEditor } from "../components/studio/AgentEditor";
import { AgentIndex } from "../components/studio/AgentIndex";
import { NewAgentDialog } from "../components/studio/NewAgentDialog";
import { NewWorkflowDialog } from "../components/studio/NewWorkflowDialog";
import { WorkflowEditor } from "../components/studio/WorkflowEditor";
import { WorkflowIndex } from "../components/studio/WorkflowIndex";
import { PageTopbar } from "../components/TopbarPageSlot";
import { TicketStudioPanel } from "../components/studio/TicketStudioPanel";
import { GateStudioPanel } from "../components/studio/gates/GateStudioPanel";
import { PaneSkeleton } from "../components/ui/PaneSkeleton";
import { IndexLoadError } from "../components/studio/StudioIndex";
import {
  navigateToStudio,
  navigateToStudioAgent,
  navigateToStudioWorkflow,
  useStudioResourceFromRoute,
  useStudioSectionFromRoute,
} from "../lib/useAppNavigation";
import { isStudioNewResource, studioPath } from "../lib/appNavigation";
import { useUiStore } from "../state/uiStore";
import { useRuntimeOptionsQuery } from "../hooks/useRuntimeOptionsQuery";

const SECTIONS = [
  ["agents", "Agents", "Agent Studio"],
  ["workflows", "Workflows", "Workflow Studio"],
  ["tickets", "Tickets", "Ticket Studio"],
  ["gates", "Gates", "Gate Studio"],
] as const;

export function StudioPage() {
  const tab = useStudioSectionFromRoute();
  const studioResourceId = useStudioResourceFromRoute();
  const isNew = isStudioNewResource(studioResourceId);
  const resourceSlug = studioResourceId && !isNew ? studioResourceId : null;

  const agents = useQuery({ queryKey: ["studio-agents"], queryFn: api.studioAgents });
  const showAgentIndex = tab === "agents" && studioResourceId === null;
  const agentRunStats = useQuery({
    queryKey: ["studio-agent-run-stats"],
    queryFn: api.studioAgentRunStats,
    enabled: showAgentIndex,
  });
  const [newAgentOpen, setNewAgentOpen] = useState(false);
  const workflows = useQuery({ queryKey: ["studio-workflows"], queryFn: api.studioWorkflows });
  const showWorkflowIndex = tab === "workflows" && studioResourceId === null;
  const workflowUsage = useQuery({
    queryKey: ["studio-workflow-usage"],
    queryFn: api.studioWorkflowUsage,
    enabled: showWorkflowIndex,
  });
  const [newWorkflowOpen, setNewWorkflowOpen] = useState(false);
  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });
  const runtimeOptions = useRuntimeOptionsQuery({ queryKey: ["runtime-options"] });

  // Inherit the app-wide active workspace so Ticket Studio (and its Smart import)
  // opens on the workspace the user is already working in, not the first in the list.
  // "all" has no single active workspace, so fall back to the first one.
  const activeWorkspace = useUiStore((s) => s.workspace);
  const activeWorkspaceSlug =
    activeWorkspace && activeWorkspace !== "all" ? activeWorkspace : workspaces.data?.[0]?.slug;

  // A slug that names nothing (deleted, mistyped) returns to the index.
  useEffect(() => {
    if (tab === "agents" && resourceSlug && agents.data && !agents.data.some((a) => a.slug === resourceSlug)) {
      navigateToStudio("agents", true);
    }
  }, [tab, resourceSlug, agents.data]);
  useEffect(() => {
    if (
      tab === "workflows" &&
      resourceSlug &&
      workflows.data &&
      !workflows.data.some((w) => w.slug === resourceSlug)
    ) {
      navigateToStudio("workflows", true);
    }
  }, [tab, resourceSlug, workflows.data]);

  const body = () => {
    if (tab === "tickets") {
      return (
        <div className="studio-shell">
          <TicketStudioPanel
            workspaces={workspaces.data ?? []}
            runtimeOptions={runtimeOptions.data}
            workspaceSlug={activeWorkspaceSlug}
          />
        </div>
      );
    }
    if (tab === "gates") {
      return (
        <GateStudioPanel
          workspaces={workspaces.data ?? []}
          workspacesLoading={workspaces.isLoading}
          workspacesError={workspaces.isError ? workspaces.error : null}
          onRetryWorkspaces={() => workspaces.refetch()}
          activeWorkspaceSlug={activeWorkspaceSlug}
        />
      );
    }
    if (showAgentIndex) {
      return (
        <AgentIndex
          agents={agents}
          stats={agentRunStats}
          onNewAgent={() => setNewAgentOpen(true)}
          newAgentOpen={newAgentOpen}
        />
      );
    }
    if (showWorkflowIndex) {
      return (
        <WorkflowIndex
          workflows={workflows}
          usage={workflowUsage}
          onNewWorkflow={() => setNewWorkflowOpen(true)}
          newWorkflowOpen={newWorkflowOpen}
        />
      );
    }
    // An editor needs both lists: an agent's delete refusal names workflows,
    // and a workflow's stages name agents.
    if (agents.isPending || workflows.isPending) return <PaneSkeleton variant="block" label="Loading…" />;
    if (agents.isError) return <IndexLoadError what="agents" query={agents} />;
    if (workflows.isError) return <IndexLoadError what="workflows" query={workflows} />;
    return tab === "agents" ? (
      <AgentEditor slug={resourceSlug} agents={agents.data} workflows={workflows.data} />
    ) : (
      <WorkflowEditor slug={resourceSlug} workflows={workflows.data} agents={agents.data} />
    );
  };

  return (
    <div className="screen-view">
      <PageTopbar title={SECTIONS.find(([section]) => section === tab)?.[2] ?? "Studio"} />

      <div className="studio-subtabs" role="tablist" aria-label="Studio sections">
        {SECTIONS.map(([section, label]) => (
          <NavLink
            key={section}
            to={studioPath(section)}
            role="tab"
            aria-selected={tab === section}
            className={`studio-subtab${tab === section ? " active" : ""}`}
          >
            {label}
          </NavLink>
        ))}
      </div>

      <div className="studio-body">{body()}</div>
      <NewAgentDialog
        open={newAgentOpen}
        onClose={() => setNewAgentOpen(false)}
        onCreated={(slug) => {
          setNewAgentOpen(false);
          navigateToStudioAgent(slug);
        }}
      />
      <NewWorkflowDialog
        open={newWorkflowOpen}
        onClose={() => setNewWorkflowOpen(false)}
        onCreated={(slug) => {
          setNewWorkflowOpen(false);
          navigateToStudioWorkflow(slug);
        }}
        workflows={workflows.data ?? []}
      />
    </div>
  );
}
