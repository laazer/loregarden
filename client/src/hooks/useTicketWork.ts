import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import {
  api,
  type StageStatus,
  type TicketDetail,
  type TicketTreeNode,
  type WorkflowReassignmentPreview,
  type WorkspaceRuntimeSettings,
} from "../api/client";
import { ticketPullRequestKey } from "../api/ticketPullRequestApi";
import { type AgentsAssembleOptions, orchestrateBody } from "../components/AgentsAssembleModal";
import type { StateUpdateDraft } from "../components/UpdateStateModal";
import { runtimeFromWorkspace, runtimeSettingsEqual } from "../components/WorkspaceRuntimeFields";
import { archivedWorkspaceSlugs } from "../lib/archivedWorkspaces";
import { notifyIfQueued } from "../lib/notifyIfQueued";
import { findTicketTreeNode } from "../lib/parentTicketTree";
import { navigateToPage, navigateToTicketTab } from "../lib/useAppNavigation";
import { toastWarning } from "../state/toastStore";
import { useRuntimeOptionsQuery } from "./useRuntimeOptionsQuery";
import { useTicketBranchSave } from "./useTicketBranchSave";
import { useTicketCommitPush } from "./useTicketCommitPush";

export const DEFAULT_ORCHESTRATION_RUNTIME: WorkspaceRuntimeSettings = {
  cli_adapter: "default",
  claude_model: "",
  cursor_model: "",
  codex_model: "",
  lmstudio_base_url: "",
  lmstudio_model: "",
};

const NO_NODES: TicketTreeNode[] = [];

function treeHasRunningWorkflow(nodes: TicketTreeNode[]): boolean {
  for (const n of nodes) {
    if (n.workflow_stage_status === "running") return true;
    if (treeHasRunningWorkflow(n.children)) return true;
  }
  return false;
}

/**
 * Everything the ticket page reads and does for one ticket: its detail, runs,
 * outputs and approvals, the mutations its controls fire, and which of its
 * dialogs is open. The page lays these out; this owns them.
 */
export function useTicketWork(ticketId: string | null) {
  const qc = useQueryClient();

  const ticketRuns = useQuery({
    queryKey: ["runs", ticketId],
    queryFn: () => api.runs(ticketId!),
    enabled: !!ticketId,
    refetchInterval: (query) => {
      const hasActive = query.state.data?.some(
        (r) => r.status === "running" || r.status === "awaiting_permission",
      );
      return hasActive ? 1000 : 5000;
    },
  });

  const hasActiveRun =
    ticketRuns.data?.some((r) => r.status === "running" || r.status === "awaiting_permission") ?? false;

  const detail = useQuery({
    queryKey: ["ticket", ticketId],
    queryFn: () => api.ticket(ticketId!),
    enabled: !!ticketId,
    refetchInterval: (query) => {
      const status = query.state.data?.workflow_stage_status;
      return hasActiveRun || status === "running" || status === "awaiting" ? 1000 : 3000;
    },
  });
  const sel = detail.data;

  const artifactsFeed = useQuery({
    queryKey: ["ticket-artifacts", ticketId],
    queryFn: () => api.ticketArtifacts(ticketId!),
    enabled: !!ticketId,
    refetchInterval: () => (hasActiveRun || sel?.workflow_stage_status === "running" ? 2000 : false),
  });

  // Shares its key with the Approvals tab, so opening the tab reuses this fetch.
  const ticketApprovals = useQuery({
    queryKey: ["approvals", ticketId],
    queryFn: () => api.approvals(ticketId!),
    enabled: !!ticketId,
    refetchInterval: 5000,
  });
  // Every pending approval waits on a person, not only the ones with criteria:
  // counting just those hid a pending human_action (a merge to resolve by hand)
  // from the timeline, though the Review button it leads to lists them all.
  const pendingApprovalCount = ticketApprovals.data?.length ?? 0;

  const workflowTemplates = useQuery({ queryKey: ["workflow-templates"], queryFn: api.workflowTemplates });
  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });
  const activeWorkspaces = useMemo(() => {
    const archived = archivedWorkspaceSlugs(workspaces.data ?? []);
    return (workspaces.data ?? []).filter((w) => !archived.has(w.slug));
  }, [workspaces.data]);

  // The ticket's own workspace decides its runtime and stage list, whatever the
  // Console's sidebar happens to be scoped to.
  const activeWorkspaceSlug = sel?.workspace_slug || activeWorkspaces[0]?.slug || "loregarden";
  const activeWorkspaceRecord = workspaces.data?.find((w) => w.slug === activeWorkspaceSlug);
  const activeWorkspaceRuntime = runtimeFromWorkspace(activeWorkspaceRecord);

  // A child list describes the ticket, so it reads the unfiltered tree of its workspace.
  const workspaceTree = useQuery({
    queryKey: ["ticket-tree", activeWorkspaceSlug, "unfiltered"],
    queryFn: () => api.ticketTree({ workspace: activeWorkspaceSlug, include_members: true }),
    enabled: Boolean(ticketId) && Boolean(sel),
    refetchInterval: (query) => (treeHasRunningWorkflow(query.state.data ?? []) ? 1000 : 5000),
  });
  const children = useMemo(
    () => (ticketId && workspaceTree.data ? (findTicketTreeNode(workspaceTree.data, ticketId)?.children ?? NO_NODES) : NO_NODES),
    [workspaceTree.data, ticketId],
  );

  const workspaceWorkflow = useQuery({
    queryKey: ["workspace-workflow", activeWorkspaceSlug],
    queryFn: () => api.workspaceWorkflow(activeWorkspaceSlug),
    enabled: !!activeWorkspaceSlug,
  });

  // AC19. The stage whose terminal handoff is being prepared, or null. The call
  // merges the worktree onto its target branch, so it is seconds long, and each
  // click opens an `AgentRun` — a second one would have the first killed by
  // `fail_interrupted_runs`.
  const [copyTerminalStageKey, setCopyTerminalStageKey] = useState<string | null>(null);
  const [stateModalOpen, setStateModalOpen] = useState(false);
  const [ticketModelModalOpen, setTicketModelModalOpen] = useState(false);
  const [runConfirmStageKey, setRunConfirmStageKey] = useState<string | null>(null);
  const [assembleModalOpen, setAssembleModalOpen] = useState(false);
  const [deleteTicketTarget, setDeleteTicketTarget] = useState<TicketDetail | null>(null);
  const [logRunId, setLogRunId] = useState<string | null>(null);
  const [pendingWorkflow, setPendingWorkflow] = useState<{
    template: string;
    preview: WorkflowReassignmentPreview;
  } | null>(null);

  useEffect(() => {
    setRunConfirmStageKey(null);
  }, [ticketId]);

  const invalidateTicketQueries = () => {
    qc.invalidateQueries({ queryKey: ["ticket", ticketId] });
    qc.invalidateQueries({ queryKey: ["tickets"] });
    qc.invalidateQueries({ queryKey: ["ticket-tree"] });
  };

  const orchestrate = useMutation({
    meta: { errorTitle: "Start orchestration" },
    mutationFn: ({ ticketId: id, options }: { ticketId: string; options?: Parameters<typeof api.orchestrate>[1] }) =>
      api.orchestrate(id, options),
    onSuccess: (data, { ticketId: id }) => {
      notifyIfQueued(data);
      qc.invalidateQueries({ queryKey: ["ticket", id] });
      qc.invalidateQueries({ queryKey: ["ticket-tree"] });
      setAssembleModalOpen(false);
    },
  });

  const openPr = useMutation({
    meta: { errorTitle: "Open pull request" },
    mutationFn: (id: string) => api.openPr(id),
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: ["ticket", id] });
      qc.invalidateQueries({ queryKey: ticketPullRequestKey(id) });
      navigateToTicketTab(id, "pr");
      setRunConfirmStageKey(null);
    },
  });

  const commitPush = useTicketCommitPush();

  const startRun = useMutation({
    meta: { errorTitle: "Start run" },
    mutationFn: (vars?: { stageKey?: string; autoApprove?: boolean; timeoutSeconds?: number; slotNumber?: number | null }) =>
      api.startRun(ticketId!, {
        stage_key: vars?.stageKey,
        auto_approve: vars?.autoApprove,
        timeout_seconds: vars?.timeoutSeconds,
        slot_number: vars?.slotNumber ?? null,
      }),
    onSuccess: (data) => {
      notifyIfQueued(data);
      qc.invalidateQueries({ queryKey: ["ticket", ticketId] });
      qc.invalidateQueries({ queryKey: ["ticket-tree"] });
      qc.invalidateQueries({ queryKey: ["runs", ticketId] });
      navigateToTicketTab(ticketId!, "timeline");
      setRunConfirmStageKey(null);
    },
    onError: () => {
      setRunConfirmStageKey(null);
    },
  });

  const stopTicket = useMutation({
    meta: { errorTitle: "Stop ticket" },
    mutationFn: () => api.stopTicket(ticketId!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ticket", ticketId] });
      qc.invalidateQueries({ queryKey: ["ticket-tree"] });
      qc.invalidateQueries({ queryKey: ["runs", ticketId] });
    },
  });

  const setCompatibilityPosture = useMutation({
    meta: { errorTitle: "Set compatibility posture" },
    mutationFn: (posture: string) => api.updateTicket(ticketId!, { compatibility_posture: posture }),
    // Descendants inherit, so a change here can move any of their resolved values.
    onSuccess: invalidateTicketQueries,
  });

  const routeWorkflow = useMutation({
    meta: { errorTitle: "Route workflow" },
    mutationFn: (body: { from_stage_key: string; next_stage_key: string; next_agent?: string; blocking_issues?: string }) =>
      api.routeWorkflow(ticketId!, body),
    onSuccess: invalidateTicketQueries,
  });

  const patchStageWorkflow = useMutation({
    meta: { errorTitle: "Update ticket" },
    mutationFn: (body: Parameters<typeof api.updateTicket>[1]) => api.updateTicket(ticketId!, body),
    onSuccess: invalidateTicketQueries,
  });

  const deleteTicket = useMutation({
    meta: { errorTitle: "Delete ticket" },
    mutationFn: (id: string) => api.deleteTicket(id),
    onSuccess: (_result, id) => {
      qc.removeQueries({ queryKey: ["ticket", id] });
      qc.invalidateQueries({ queryKey: ["tickets"] });
      qc.invalidateQueries({ queryKey: ["ticket-tree"] });
      setDeleteTicketTarget(null);
      if (ticketId === id) navigateToPage("dashboard", true);
    },
  });

  const saveStateFromModal = useMutation({
    meta: { errorTitle: "Update ticket state" },
    mutationFn: async ({ draft, original }: { draft: StateUpdateDraft; original: StateUpdateDraft }) => {
      if (!ticketId) return;
      const patch: Parameters<typeof api.updateTicket>[1] = {};
      if (draft.state !== original.state) {
        patch.state = draft.state;
        patch.auto_state = false;
      } else if (draft.stateLocked !== original.stateLocked) {
        patch.auto_state = !draft.stateLocked;
      }
      if (draft.workflowStageKey !== original.workflowStageKey) {
        patch.workflow_stage_key = draft.workflowStageKey;
      }
      if (draft.workflowStageStatus !== original.workflowStageStatus) {
        patch.workflow_stage_status = draft.workflowStageStatus;
      }
      const stageUpdates: Record<string, StageStatus> = {};
      for (const [key, status] of Object.entries(draft.stageStatuses)) {
        if (original.stageStatuses[key] !== status) stageUpdates[key] = status;
      }
      if (Object.keys(stageUpdates).length > 0) patch.stage_updates = stageUpdates;
      if (Object.keys(patch).length > 0) await api.updateTicket(ticketId, patch);
    },
    onSuccess: () => {
      invalidateTicketQueries();
      setStateModalOpen(false);
    },
  });

  const setTicketTemplate = useMutation({
    meta: { errorTitle: "Set ticket workflow" },
    mutationFn: ({ ticketId: id, template }: { ticketId: string; template: string }) =>
      api.updateTicket(id, { workflow_template_slug: template }),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["ticket", vars.ticketId] });
      qc.invalidateQueries({ queryKey: ["tickets"] });
      qc.invalidateQueries({ queryKey: ["ticket-tree"] });
      setPendingWorkflow(null);
    },
  });

  // Ask what the change would cost before making it. Only a change that would
  // discard progress prompts; assigning a workflow to a ticket that has not
  // started applies straight away.
  const requestWorkflowChange = async (id: string, template: string) => {
    if (!template) {
      setTicketTemplate.mutate({ ticketId: id, template });
      return;
    }
    try {
      const preview = await api.previewWorkflowReassignment(id, template);
      if (preview.destructive) {
        setPendingWorkflow({ template, preview });
        return;
      }
    } catch (error) {
      // Advisory, so the change proceeds — but this is the only warning that it can discard stages.
      toastWarning("Could not check what this workflow change would discard", error, "Applying it anyway — progress may be reset");
    }
    setTicketTemplate.mutate({ ticketId: id, template });
  };

  const { save: saveTicketBranch, saveOrThrow: saveTicketBranchOrThrow } = useTicketBranchSave();

  const runtimeOptions = useRuntimeOptionsQuery({
    queryKey: ["runtime-options", activeWorkspaceSlug],
    params: { workspace: activeWorkspaceSlug },
  });

  const setRuntime = useMutation({
    meta: { errorTitle: "Save runtime settings" },
    mutationFn: ({ slug, runtime }: { slug: string; runtime: WorkspaceRuntimeSettings }) =>
      api.setWorkspaceRuntime(slug, runtime),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["workspaces"] });
      qc.invalidateQueries({ queryKey: ["workspace-runtime", vars.slug] });
    },
  });

  // The ticket's Model settings dialog reports what *this ticket's* runs use —
  // its own pin over its own workspace.
  const ticketRuntimeOptions = useRuntimeOptionsQuery({
    queryKey: ["runtime-options", "ticket", ticketId],
    params: { ticket: ticketId ?? "" },
    enabled: ticketModelModalOpen && Boolean(ticketId),
  });

  const setTicketRuntime = useMutation({
    meta: { errorTitle: "Save ticket runtime" },
    mutationFn: (runtime: WorkspaceRuntimeSettings) => {
      if (!ticketId) throw new Error("No ticket selected");
      return api.setTicketRuntime(ticketId, runtime);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ticket", ticketId] });
      qc.invalidateQueries({ queryKey: ["runtime-options", "ticket", ticketId] });
    },
  });

  const confirmStageRun = async (
    runtime: WorkspaceRuntimeSettings,
    autoApprove: boolean,
    timeoutSeconds: number | undefined,
    slotNumber: number | null,
  ) => {
    if (!runConfirmStageKey) return;
    try {
      if (!runtimeSettingsEqual(runtime, activeWorkspaceRuntime)) {
        await setRuntime.mutateAsync({ slug: activeWorkspaceSlug, runtime });
      }
      await startRun.mutateAsync({ stageKey: runConfirmStageKey, autoApprove, timeoutSeconds, slotNumber });
    } catch {
      // silent-ok: setRuntime/startRun carry meta.errorTitle; the modal stays open to retry
    }
  };

  const confirmAssemble = async (options: AgentsAssembleOptions) => {
    if (!ticketId || !sel) return;
    try {
      if (!runtimeSettingsEqual(options.runtime, activeWorkspaceRuntime)) {
        await setRuntime.mutateAsync({ slug: activeWorkspaceSlug, runtime: options.runtime });
      }
      if (options.branch !== (sel.branch || "")) {
        await saveTicketBranchOrThrow(ticketId, options.branch);
      }
      await orchestrate.mutateAsync({ ticketId, options: orchestrateBody(options) });
    } catch {
      // silent-ok: every step above reports itself; the modal stays open to retry
    }
  };

  const workflowBusy =
    sel?.workflow_stage_status === "awaiting" ||
    hasActiveRun ||
    (sel?.workflow_stage_status === "running" && startRun.isPending);
  const isStageRunning = (stageKey: string) =>
    (sel?.workflow_stage_key === stageKey && workflowBusy) ||
    (startRun.isPending && startRun.variables?.stageKey === stageKey);

  // The latest run only (the list is newest first): a failure that a later run
  // has already moved past is history on the Timeline, not "Run failed" now.
  const latestRun = ticketRuns.data?.[0];
  const hasRunErrors = Boolean(
    sel?.blocking_issues || sel?.artifacts?.error || (latestRun?.status === "failed" && latestRun.stderr),
  );

  return {
    ticketId,
    sel,
    detail,
    ticketRuns,
    hasActiveRun,
    artifactsFeed,
    pendingApprovalCount,
    workflowTemplates,
    activeWorkspaces,
    activeWorkspaceSlug,
    activeWorkspaceRuntime,
    children,
    workspaceTree: workspaceTree.data ?? NO_NODES,
    workspaceWorkflow,
    copyTerminalStageKey,
    setCopyTerminalStageKey,
    stateModalOpen,
    setStateModalOpen,
    ticketModelModalOpen,
    setTicketModelModalOpen,
    runConfirmStageKey,
    setRunConfirmStageKey,
    assembleModalOpen,
    setAssembleModalOpen,
    deleteTicketTarget,
    setDeleteTicketTarget,
    logRunId,
    setLogRunId,
    pendingWorkflow,
    setPendingWorkflow,
    orchestrate,
    openPr,
    commitPush,
    startRun,
    stopTicket,
    setCompatibilityPosture,
    routeWorkflow,
    patchStageWorkflow,
    deleteTicket,
    saveStateFromModal,
    setTicketTemplate,
    requestWorkflowChange,
    saveTicketBranch,
    runtimeOptions,
    setRuntime,
    ticketRuntimeOptions,
    setTicketRuntime,
    confirmStageRun,
    confirmAssemble,
    workflowBusy,
    isStageRunning,
    hasRunErrors,
  };
}

export type TicketWork = ReturnType<typeof useTicketWork>;
