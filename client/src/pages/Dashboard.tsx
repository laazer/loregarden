import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api, type StageStatus, type TicketDetail, type TicketTreeNode, type WorkItemType, type WorkflowReassignmentPreview } from "../api/client";
import { ticketPullRequestKey } from "../api/ticketPullRequestApi";
import { canRunStage } from "../lib/stageRunPolicy";
import { DashboardActiveTickets } from "../components/DashboardActiveTickets";
import { DashboardTicketDetailsButton } from "../components/DashboardTicketDetailsButton";
import { PrioBars } from "../components/PrioBars";
import { TicketPaneFilters } from "../components/TicketPaneFilters";
import { ArtifactPaneBody } from "../components/dashboard/ArtifactPaneBody";
import { ArtifactTabBar } from "../components/dashboard/ArtifactTabBar";
import { findAncestorIds, TicketTree } from "../components/TicketTree";
import { findTicketTreeNode } from "../lib/parentTicketTree";
import { AgentsAssembleModal, type AgentsAssembleOptions, orchestrateBody } from "../components/AgentsAssembleModal";
import { ConfirmRunStageModal } from "../components/ConfirmRunStageModal";
import { StageRouteHints } from "../components/StageRouteHints";
import { StageOverflowMenu } from "../components/StageOverflowMenu";
import { WorkflowStageTimeline } from "../components/WorkflowStageTimeline";
import { WorkflowReassignWarning } from "../components/WorkflowReassignWarning";
import {
  isHumanGateStage,
  stageKindLabel,
  stageRunButtonLabel,
} from "../lib/stageDisplay";
import { AddWorkspaceFlow } from "../components/workspaces/AddWorkspaceFlow";
import { CreateWorkItemFlow, type CreateWorkItemRequest } from "../components/dashboard/CreateWorkItemFlow";
import { DashboardWorkspacesPane } from "../components/dashboard/DashboardWorkspacesPane";
import { ImportTicketsFlow } from "../components/dashboard/ImportTicketsFlow";
import { PaneHideButton } from "../components/dashboard/PaneHideButton";
import { WorkflowRunControls } from "../components/dashboard/WorkflowRunControls";
import { WorkflowTicketSettings } from "../components/dashboard/WorkflowTicketSettings";
import { archivedWorkspaceSlugs, withoutArchivedWorkspaces } from "../lib/archivedWorkspaces";
import { DeleteTicketConfirmModal } from "../components/DeleteTicketConfirmModal";
import { RunLogModal } from "../components/RunLogModal";
import { canHaveChildren } from "../lib/workItemHierarchy";
import { errorDetail } from "../utils/errorDetail";
import { hasHumanCriteria } from "../utils/approvalCriteria";
import { WorkflowPaneTicketMeta } from "../components/WorkflowPaneTicketMeta";
import { runtimeFromWorkspace, runtimeSettingsEqual, runtimeSummaryLabel } from "../components/WorkspaceRuntimeFields";
import { TriageModelModal } from "../components/TriageModelModal";
import { UpdateStateModal, type StateUpdateDraft } from "../components/UpdateStateModal";
import { navigateToPage, navigateToTicket, navigateToTicketTab, useArtifactTabFromRoute, useTicketIdFromRoute } from "../lib/useAppNavigation";
import { canonicalArtifactTab, isArtifactTab } from "../lib/appNavigation";
import { useUiStore, type PaneId } from "../state/uiStore";
import { useTicketBranchSave } from "../hooks/useTicketBranchSave";
import { useTicketCommitPush } from "../hooks/useTicketCommitPush";
import { pushToast, toastActionFailed, toastWarning } from "../state/toastStore";
import { buildStageTerminalHandoffCommand } from "../lib/terminalCommands";

const DEFAULT_ORCHESTRATION_RUNTIME: import("../api/client").WorkspaceRuntimeSettings = {
  cli_adapter: "default",
  claude_model: "",
  cursor_model: "",
  codex_model: "",
  lmstudio_base_url: "",
  lmstudio_model: "",
};

function formatDeleteTicketError(error: Error): string {
  return errorDetail(error, "Failed to delete ticket") ?? "Failed to delete ticket";
}

/** Stable while the tree loads, so memos keyed on it do not recompute every render. */
const NO_NODES: TicketTreeNode[] = [];

/** Every ticket once: an initiative's member copies are skipped, the real rows counted. */
function flattenTree(nodes: TicketTreeNode[]): TicketTreeNode[] {
  const out: TicketTreeNode[] = [];
  for (const n of nodes) {
    if (!n.member_link) out.push(n);
    out.push(...flattenTree(n.children));
  }
  return out;
}

function treeHasRunningWorkflow(nodes: TicketTreeNode[]): boolean {
  for (const n of nodes) {
    if (n.workflow_stage_status === "running") return true;
    if (treeHasRunningWorkflow(n.children)) return true;
  }
  return false;
}


/**
 * Say when a start became a wait.
 *
 * The execution slots are shared with the queue board, so a full pool turns
 * "run this" into "queued behind two others" — and the ticket that comes back
 * looks the same either way. Silence here would read as nothing happening,
 * which is exactly the confusion that made the board look broken.
 */
function notifyIfQueued(detail: { admission?: { admitted: boolean; message: string } | null }) {
  const admission = detail?.admission;
  if (!admission || admission.admitted) return;
  pushToast({
    tone: "info",
    title: "Queued — all slots busy",
    message: admission.message,
  });
}

export function Dashboard() {
  const qc = useQueryClient();
  const routeTicketId = useTicketIdFromRoute();
  const { artifactTab: rawArtifactTab } = useParams<{ artifactTab?: string }>();
  const artifactTab = useArtifactTabFromRoute();
  const {
    stateFilters,
    typeFilters,
    search,
    expandedTicketIds,
    workspace,
    toggleStateFilter,
    clearStateFilters,
    toggleTypeFilter,
    clearTypeFilters,
    toggleExpanded,
    expandPath,
    setWorkspace,
    paneVisibility,
    setPaneVisible,
    openEditorFile,
  } = useUiStore();

  const { workspaces: showWorkspaces, tickets: showTickets, workflow: showWorkflow, artifacts: showArtifacts } =
    paneVisibility;
  const showSidebar = showWorkspaces || showTickets;
  const visiblePaneCount = Object.values(paneVisibility).filter(Boolean).length;

  const hidePane = (pane: PaneId) => setPaneVisible(pane, false);

  const wsParam = workspace === "all" ? undefined : workspace;

  const ticketTree = useQuery({
    queryKey: ["ticket-tree", workspace, stateFilters, typeFilters, search],
    queryFn: () =>
      api.ticketTree({
        workspace: wsParam,
        state: stateFilters.length ? stateFilters : undefined,
        work_item_type: typeFilters.length ? typeFilters : undefined,
        search: search.trim() || undefined,
        include_members: true,
      }),
    refetchInterval: (query) =>
      treeHasRunningWorkflow(query.state.data ?? []) ? 1000 : 5000,
  });


  const [createRequest, setCreateRequest] = useState<CreateWorkItemRequest | null>(null);
  const [importSlug, setImportSlug] = useState<string | null>(null);
  const [addWorkspaceOpen, setAddWorkspaceOpen] = useState(false);

  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });
  const archivedSlugs = useMemo(() => archivedWorkspaceSlugs(workspaces.data ?? []), [workspaces.data]);
  const activeWorkspaces = useMemo(
    () => (workspaces.data ?? []).filter((w) => !archivedSlugs.has(w.slug)),
    [workspaces.data, archivedSlugs],
  );
  // Archiving a workspace takes it out of the Console: its items leave "All workspaces" too.
  // Lookups below still use the full list, so a deep link into one still resolves.
  const visibleTree = useMemo(
    () => withoutArchivedWorkspaces(ticketTree.data ?? NO_NODES, archivedSlugs),
    [ticketTree.data, archivedSlugs],
  );
  const flatTickets = useMemo(() => flattenTree(visibleTree), [visibleTree]);

  useEffect(() => {
    if (workspace !== "all" && archivedSlugs.has(workspace)) setWorkspace("all");
  }, [workspace, archivedSlugs, setWorkspace]);
  const workflowTemplates = useQuery({
    queryKey: ["workflow-templates"],
    queryFn: api.workflowTemplates,
  });

  const selectedId = routeTicketId ?? flatTickets[0]?.id ?? null;

  const selectTicket = useCallback(
    (id: string) => {
      navigateToTicket(id, { tab: artifactTab });
    },
    [artifactTab],
  );

  useEffect(() => {
    if (!routeTicketId || !rawArtifactTab) return;
    if (!isArtifactTab(rawArtifactTab)) {
      // A retired tab (/logs, /errors, /artifacts…) lands on the view that holds it now.
      navigateToTicket(routeTicketId, { tab: canonicalArtifactTab(rawArtifactTab) ?? "diff", replace: true });
    }
  }, [routeTicketId, rawArtifactTab]);

  useEffect(() => {
    setRunConfirmStageKey(null);
  }, [selectedId]);

  useEffect(() => {
    if (!selectedId || !ticketTree.data?.length) return;
    const ancestors = findAncestorIds(ticketTree.data, selectedId);
    if (ancestors.length) expandPath(ancestors);
  }, [selectedId, ticketTree.data, expandPath]);

  const ticketRuns = useQuery({
    queryKey: ["runs", selectedId],
    queryFn: () => api.runs(selectedId!),
    enabled: !!selectedId,
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
    queryKey: ["ticket", selectedId],
    queryFn: () => api.ticket(selectedId!),
    enabled: !!selectedId,
    refetchInterval: (query) => {
      const status = query.state.data?.workflow_stage_status;
      return hasActiveRun || status === "running" || status === "awaiting" ? 1000 : 3000;
    },
  });

  const artifactsFeed = useQuery({
    queryKey: ["ticket-artifacts", selectedId],
    queryFn: () => api.ticketArtifacts(selectedId!),
    enabled: !!selectedId,
    refetchInterval: () =>
      hasActiveRun || detail.data?.workflow_stage_status === "running" ? 2000 : false,
  });

  // Shares its key with the Approvals tab, so opening the tab reuses this fetch.
  const ticketApprovals = useQuery({
    queryKey: ["approvals", selectedId],
    queryFn: () => api.approvals(selectedId!),
    enabled: !!selectedId,
    refetchInterval: 5000,
  });
  const humanApprovalCount = (ticketApprovals.data ?? []).filter(hasHumanCriteria).length;

  const orchestrate = useMutation({
    meta: { errorTitle: "Start orchestration" },
    mutationFn: ({
      ticketId,
      options,
    }: {
      ticketId: string;
      options?: Parameters<typeof api.orchestrate>[1];
    }) => api.orchestrate(ticketId, options),
    onSuccess: (data, { ticketId }) => {
      notifyIfQueued(data);
      qc.invalidateQueries({ queryKey: ["ticket", ticketId] });
      qc.invalidateQueries({ queryKey: ["ticket-tree"] });
      setAssembleModalOpen(false);
    },
  });

  const openPr = useMutation({
    meta: { errorTitle: "Open pull request" },
    mutationFn: (ticketId: string) => api.openPr(ticketId),
    onSuccess: (_data, ticketId) => {
      qc.invalidateQueries({ queryKey: ["ticket", ticketId] });
      qc.invalidateQueries({ queryKey: ticketPullRequestKey(ticketId) });
      navigateToTicketTab(ticketId, "pr");
      setRunConfirmStageKey(null);
    },
  });

  const commitPush = useTicketCommitPush();

  const startRun = useMutation({
    meta: { errorTitle: "Start run" },
    mutationFn: (vars?: {
      stageKey?: string;
      autoApprove?: boolean;
      timeoutSeconds?: number;
      slotNumber?: number | null;
    }) =>
      api.startRun(selectedId!, {
        stage_key: vars?.stageKey,
        auto_approve: vars?.autoApprove,
        timeout_seconds: vars?.timeoutSeconds,
        slot_number: vars?.slotNumber ?? null,
      }),
    onSuccess: (data) => {
      notifyIfQueued(data);
      qc.invalidateQueries({ queryKey: ["ticket", selectedId] });
      qc.invalidateQueries({ queryKey: ["ticket-tree"] });
      qc.invalidateQueries({ queryKey: ["runs", selectedId] });
      navigateToTicketTab(selectedId!, "timeline");
      setRunConfirmStageKey(null);
    },
    onError: () => {
      setRunConfirmStageKey(null);
    },
  });

  const stopTicket = useMutation({
    meta: { errorTitle: "Stop ticket" },
    mutationFn: () => api.stopTicket(selectedId!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ticket", selectedId] });
      qc.invalidateQueries({ queryKey: ["ticket-tree"] });
      qc.invalidateQueries({ queryKey: ["runs", selectedId] });
    },
  });

  const setCompatibilityPosture = useMutation({
    meta: { errorTitle: "Set compatibility posture" },
    mutationFn: (posture: string) =>
      api.updateTicket(selectedId!, { compatibility_posture: posture }),
    onSuccess: () => {
      // Descendants inherit, so a change here can move any of their resolved values.
      qc.invalidateQueries({ queryKey: ["ticket", selectedId] });
      qc.invalidateQueries({ queryKey: ["tickets"] });
      qc.invalidateQueries({ queryKey: ["ticket-tree"] });
    },
  });

  const invalidateTicketQueries = () => {
    qc.invalidateQueries({ queryKey: ["ticket", selectedId] });
    qc.invalidateQueries({ queryKey: ["tickets"] });
    qc.invalidateQueries({ queryKey: ["ticket-tree"] });
  };

  const routeWorkflow = useMutation({
    meta: { errorTitle: "Route workflow" },
    mutationFn: (body: {
      from_stage_key: string;
      next_stage_key: string;
      next_agent?: string;
      blocking_issues?: string;
    }) => api.routeWorkflow(selectedId!, body),
    onSuccess: invalidateTicketQueries,
  });

  const patchStageWorkflow = useMutation({
    meta: { errorTitle: "Update ticket" },
    mutationFn: (body: Parameters<typeof api.updateTicket>[1]) => api.updateTicket(selectedId!, body),
    onSuccess: invalidateTicketQueries,
  });

  const deleteTicket = useMutation({
    meta: { errorTitle: "Delete ticket" },
    mutationFn: (ticketId: string) => api.deleteTicket(ticketId),
    onSuccess: (_result, ticketId) => {
      qc.removeQueries({ queryKey: ["ticket", ticketId] });
      qc.invalidateQueries({ queryKey: ["tickets"] });
      qc.invalidateQueries({ queryKey: ["ticket-tree"] });
      setDeleteTicketTarget(null);
      if (selectedId === ticketId) {
        navigateToPage("dashboard", true);
      }
    },
  });

  const copyTerminalCommand = async (command: string) => {
    if (!command.trim()) return;
    try {
      await navigator.clipboard.writeText(command);
    } catch {
      const textarea = document.createElement("textarea");
      textarea.value = command;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      document.body.removeChild(textarea);
    }
  };

  const [stateModalOpen, setStateModalOpen] = useState(false);
  const [ticketModelModalOpen, setTicketModelModalOpen] = useState(false);
  const [runConfirmStageKey, setRunConfirmStageKey] = useState<string | null>(null);
  const [assembleModalOpen, setAssembleModalOpen] = useState(false);
  const [deleteTicketTarget, setDeleteTicketTarget] = useState<TicketDetail | null>(null);
  const [logRunId, setLogRunId] = useState<string | null>(null);

  const saveStateFromModal = useMutation({
    meta: { errorTitle: "Update ticket state" },
    mutationFn: async ({
      draft,
      original,
    }: {
      draft: StateUpdateDraft;
      original: StateUpdateDraft;
    }) => {
      if (!selectedId) return;

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
        if (original.stageStatuses[key] !== status) {
          stageUpdates[key] = status;
        }
      }
      if (Object.keys(stageUpdates).length > 0) {
        patch.stage_updates = stageUpdates;
      }

      if (Object.keys(patch).length > 0) {
        await api.updateTicket(selectedId, patch);
      }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ticket", selectedId] });
      qc.invalidateQueries({ queryKey: ["ticket-tree"] });
      qc.invalidateQueries({ queryKey: ["tickets"] });
      setStateModalOpen(false);
    },
  });

  const [pendingWorkflow, setPendingWorkflow] = useState<{
    template: string;
    preview: WorkflowReassignmentPreview;
  } | null>(null);

  const setTicketTemplate = useMutation({
    meta: { errorTitle: "Set ticket workflow" },
    mutationFn: ({ ticketId, template }: { ticketId: string; template: string }) =>
      api.updateTicket(ticketId, { workflow_template_slug: template }),
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
  const requestWorkflowChange = async (ticketId: string, template: string) => {
    if (!template) {
      setTicketTemplate.mutate({ ticketId, template });
      return;
    }
    try {
      const preview = await api.previewWorkflowReassignment(ticketId, template);
      if (preview.destructive) {
        setPendingWorkflow({ template, preview });
        return;
      }
    } catch (error) {
      // Advisory, so the change proceeds — but this is the only warning that it can discard stages.
      toastWarning("Could not check what this workflow change would discard", error, "Applying it anyway — progress may be reset");
    }
    setTicketTemplate.mutate({ ticketId, template });
  };

  const { save: saveTicketBranch, saveOrThrow: saveTicketBranchOrThrow } = useTicketBranchSave();
  const runtimeOptions = useQuery({
    queryKey: ["runtime-options", workspace],
    queryFn: () => api.runtimeOptions({ workspace }),
  });

  const setRuntime = useMutation({
    meta: { errorTitle: "Save runtime settings" },
    mutationFn: ({
      slug,
      runtime,
    }: {
      slug: string;
      runtime: { cli_adapter: string; claude_model: string; cursor_model: string; codex_model?: string; lmstudio_base_url: string; lmstudio_model: string };
    }) => api.setWorkspaceRuntime(slug, runtime),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["workspaces"] });
      qc.invalidateQueries({ queryKey: ["workspace-runtime", vars.slug] });
    },
  });

  // The ticket's Model settings dialog reports what *this ticket's* runs use —
  // its own pin over its own workspace. The page-level query above is scoped to
  // the sidebar's workspace (often "all"), which can only name the global fallback.
  const ticketRuntimeOptions = useQuery({
    queryKey: ["runtime-options", "ticket", selectedId],
    queryFn: () => api.runtimeOptions({ ticket: selectedId ?? "" }),
    enabled: ticketModelModalOpen && Boolean(selectedId),
  });

  const setTicketRuntime = useMutation({
    meta: { errorTitle: "Save ticket runtime" },
    mutationFn: (runtime: import("../api/client").WorkspaceRuntimeSettings) => {
      if (!selectedId) throw new Error("No ticket selected");
      return api.setTicketRuntime(selectedId, runtime);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ticket", selectedId] });
      qc.invalidateQueries({ queryKey: ["runtime-options", "ticket", selectedId] });
    },
  });

  const sel = detail.data;
  const runConfirmStage = sel?.stages.find((s) => s.key === runConfirmStageKey) ?? null;
  // The child list describes the selected ticket, so it must not inherit the sidebar's filter —
  // otherwise a milestone reports child_count 5 (an unfiltered DB count) and lists only the 1
  // child that survived the filter. When nothing is filtered the sidebar tree is already
  // complete, so reuse it rather than fetching a second copy.
  const filtersNarrowTree =
    stateFilters.length > 0 || typeFilters.length > 0 || Boolean(search.trim());

  const unfilteredTree = useQuery({
    queryKey: ["ticket-tree", workspace, "unfiltered"],
    queryFn: () => api.ticketTree({ workspace: wsParam, include_members: true }),
    enabled: filtersNarrowTree && Boolean(selectedId),
    refetchInterval: (query) => (treeHasRunningWorkflow(query.state.data ?? []) ? 1000 : 5000),
  });

  const childSource = filtersNarrowTree ? unfilteredTree.data : ticketTree.data;
  const selChildren = useMemo(
    () => (selectedId && childSource ? (findTicketTreeNode(childSource, selectedId)?.children ?? []) : []),
    [childSource, selectedId],
  );

  const activeWorkspaceSlug =
    workspace === "all" ? (sel?.workspace_slug || activeWorkspaces[0]?.slug || "loregarden") : workspace;
  const defaultCreateWorkspaceSlug =
    sel?.workspace_slug || activeWorkspaces[0]?.slug || "loregarden";
  const activeWorkspaceRecord = workspaces.data?.find((w) => w.slug === activeWorkspaceSlug);
  const activeWorkspaceRuntime = runtimeFromWorkspace(activeWorkspaceRecord);
  const openCreateWorkItem = () => {
    setCreateRequest({ workspaceSlug: workspace === "all" ? defaultCreateWorkspaceSlug : workspace, parent: null });
  };

  const openImportTickets = () => {
    const slug = workspace === "all" ? defaultCreateWorkspaceSlug : workspace;
    if (slug) setImportSlug(slug);
  };

  const openCreateSubTicket = (parent: {
    id: string;
    title: string;
    work_item_type: WorkItemType;
    workspace_slug?: string;
  }) => {
    if (!canHaveChildren(parent.work_item_type)) return;
    const slug = parent.workspace_slug || (workspace !== "all" ? workspace : defaultCreateWorkspaceSlug);
    if (!slug) return;
    setCreateRequest({
      workspaceSlug: slug,
      parent: { id: parent.id, title: parent.title, type: parent.work_item_type },
    });
  };

  const requestStageRun = (stageKey: string) => setRunConfirmStageKey(stageKey);
  const confirmStageRun = async (
    runtime: typeof activeWorkspaceRuntime,
    autoApprove: boolean,
    timeoutSeconds: number | undefined,
    slotNumber: number | null,
  ) => {
    if (!runConfirmStageKey) return;
    try {
      if (!runtimeSettingsEqual(runtime, activeWorkspaceRuntime)) {
        await setRuntime.mutateAsync({ slug: activeWorkspaceSlug, runtime });
      }
      await startRun.mutateAsync({
        stageKey: runConfirmStageKey,
        autoApprove,
        timeoutSeconds,
        slotNumber,
      });
    } catch {
      // silent-ok: setRuntime/startRun carry meta.errorTitle; the modal stays open to retry
    }
  };

  const confirmAssemble = async (options: AgentsAssembleOptions) => {
    if (!selectedId || !sel) return;
    try {
      if (!runtimeSettingsEqual(options.runtime, activeWorkspaceRuntime)) {
        await setRuntime.mutateAsync({ slug: activeWorkspaceSlug, runtime: options.runtime });
      }
      if (options.branch !== (sel.branch || "")) {
        await saveTicketBranchOrThrow(selectedId, options.branch);
      }
      await orchestrate.mutateAsync({ ticketId: selectedId, options: orchestrateBody(options) });
    } catch {
      // silent-ok: every step above reports itself; the modal stays open to retry
    }
  };

  const workspaceWorkflow = useQuery({
    queryKey: ["workspace-workflow", activeWorkspaceSlug],
    queryFn: () => api.workspaceWorkflow(activeWorkspaceSlug),
    enabled: !!activeWorkspaceSlug && activeWorkspaceSlug !== "all",
  });

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
    sel?.blocking_issues ||
      sel?.artifacts?.error ||
      (latestRun?.status === "failed" && latestRun.stderr),
  );

  const lastAutoTabTicketId = useRef<string | null>(null);

  useEffect(() => {
    if (!sel?.id) return;
    if (lastAutoTabTicketId.current === sel.id) return;
    lastAutoTabTicketId.current = sel.id;

    // Respect explicit artifact tabs in the URL (e.g. /tickets/:id/logs).
    if (artifactTab !== "diff") return;

    if (sel.blocking_issues || sel.artifacts?.error) {
      navigateToTicketTab(sel.id, "timeline", true);
    }
  }, [sel?.id, sel?.blocking_issues, sel?.artifacts?.error, artifactTab]);

  const counts = flatTickets.reduce(
    (acc, t) => {
      acc.all += 1;
      acc[t.state] += 1;
      return acc;
    },
    { all: 0, backlog: 0, in_progress: 0, blocked: 0, done: 0, wont_do: 0 } as Record<string, number>,
  );

  const expandedSet = useMemo(() => new Set(expandedTicketIds), [expandedTicketIds]);

  return (
    <div className="screen-view">
      {/* Three tickets can hold slots at once now that each runs in its own
          worktree; a page built around one selection would show only one. */}
      <DashboardActiveTickets selectedTicketId={sel?.id} onSelect={selectTicket} />
      <div className="main-panes">
        {showSidebar && (
          <aside
            className={`sidebar ${showWorkspaces && showTickets ? "" : "sidebar-single-pane"}`.trim()}
          >
            {showWorkspaces && (
              <DashboardWorkspacesPane
                workspaces={activeWorkspaces}
                archivedCount={archivedSlugs.size}
                selected={workspace}
                allTicketCount={flatTickets.length}
                workflowTemplates={workflowTemplates.data}
                fill={!showTickets}
                hideDisabled={visiblePaneCount <= 1}
                onSelect={setWorkspace}
                onAdd={() => setAddWorkspaceOpen(true)}
                onHide={() => hidePane("workspaces")}
              />
            )}

            {showTickets && (
              <div className={`tickets-pane ${showWorkspaces ? "" : "pane-fill"}`.trim()}>
                <PaneHideButton
                  className="pane-hide-btn pane-hide-btn--corner"
                  pane="tickets"
                  onHide={() => hidePane("tickets")}
                  disabled={visiblePaneCount <= 1}
                />
                <div className="pane-header tickets-pane-header">
                  <div className="tickets-pane-title-row">
                    <div className="tickets-pane-heading">
                      <span className="pane-title">Work items</span>
                      <span className="count-pill">{flatTickets.length}</span>
                    </div>
                    <span className="tickets-pane-sort">by priority</span>
                  </div>
                  <div className="tickets-pane-actions">
                    <div className="tickets-pane-primary-actions">
                      <button
                        className="btn-secondary btn-compact btn-icon-label"
                        type="button"
                        title={
                          defaultCreateWorkspaceSlug
                            ? "Create a new work item"
                            : "Load workspaces before creating work items"
                        }
                        disabled={!defaultCreateWorkspaceSlug}
                        onClick={openCreateWorkItem}
                      >
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
                          <path d="M12 5v14M5 12h14" />
                        </svg>
                        New
                      </button>
                      <button
                        className="btn-secondary btn-compact btn-icon-label"
                        type="button"
                        title={
                          defaultCreateWorkspaceSlug
                            ? "Import work items from .md, .json, or .yaml files"
                            : "Load workspaces before importing work items"
                        }
                        disabled={!defaultCreateWorkspaceSlug || importSlug !== null}
                        onClick={openImportTickets}
                      >
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
                          <path d="M12 3v12" />
                          <path d="m8 11 4 4 4-4" />
                          <path d="M5 21h14" />
                        </svg>
                        Import
                      </button>
                    </div>
                  </div>
                  <TicketPaneFilters
                    typeFilters={typeFilters}
                    stateFilters={stateFilters}
                    stateCounts={counts}
                    onToggleType={toggleTypeFilter}
                    onToggleState={toggleStateFilter}
                    onClearTypes={clearTypeFilters}
                    onClearStates={clearStateFilters}
                  />
                </div>
                <div className="scroll-list lg-primitive-ticket-list--v6">
                  {visibleTree.length ? (
                    <TicketTree
                      nodes={visibleTree}
                      selectedId={selectedId}
                      expandedIds={expandedSet}
                      onSelect={selectTicket}
                      onToggle={toggleExpanded}
                      onAddChild={openCreateSubTicket}
                      presentation="v6"
                    />
                  ) : (
                    <div className="empty-tree">No work items match filters</div>
                  )}
                </div>
              </div>
            )}
          </aside>
        )}

        {showWorkflow && (
        <main className={`workflow-pane ${showArtifacts ? "" : "pane-fill"}`.trim()}>
          <div className="workflow-pane-header">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--ac)" strokeWidth="2" aria-hidden>
              <circle cx="6" cy="6" r="2.5" />
              <circle cx="6" cy="18" r="2.5" />
              <path d="M6 8.5v7" />
              <path d="M18 6H9M18 6a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM18 6v6a6 6 0 0 1-6 6" />
            </svg>
            <span className="pane-title workflow-pane-label">Workflow</span>
            {sel && (
              <span className="count-pill" style={{ maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {sel.external_id}
              </span>
            )}
            <div style={{ flex: 1 }} />
            {selectedId && <DashboardTicketDetailsButton ticketId={selectedId} />}
            <PaneHideButton
              pane="workflow"
              onHide={() => hidePane("workflow")}
              disabled={visiblePaneCount <= 1}
            />
          </div>
          {sel ? (
            <>
              <div style={{ flex: 1, overflowY: "auto", padding: "20px 22px" }}>
                <div style={{ display: "flex", gap: 12, marginBottom: 14 }}>
                  <PrioBars priority={sel.priority} size="md" />
                  <h1 style={{ margin: 0, fontFamily: "var(--dp)", fontSize: 19, fontWeight: 600 }}>
                    {sel.title}
                  </h1>
                </div>
                <WorkflowPaneTicketMeta
                  ticket={sel}
                  hasRunErrors={hasRunErrors}
                  onOpenParent={selectTicket}
                  onAddChild={openCreateSubTicket}
                />
                <button
                  type="button"
                  className="btn-secondary"
                  style={{ marginBottom: 16 }}
                  onClick={() => setStateModalOpen(true)}
                >
                  Update state…
                </button>
                {sel.child_count > 0 || selChildren.length > 0 ? (
                  <div>
                    <div className="state-label workflow-lifecycle-label" style={{ marginBottom: 6 }}>
                      Child tickets
                    </div>
                    <div className="lg-primitive-ticket-list--v6">
                      <TicketTree
                        nodes={selChildren}
                        selectedId={selectedId}
                        expandedIds={expandedSet}
                        onSelect={selectTicket}
                        onToggle={toggleExpanded}
                        onAddChild={openCreateSubTicket}
                        showExternalId
                        presentation="v6"
                      />
                    </div>
                  </div>
                ) : (
                <>
                <WorkflowTicketSettings
                  ticket={sel}
                  workflowTemplates={workflowTemplates.data}
                  workspaceWorkflow={workspaceWorkflow.data}
                  workflowBusy={workflowBusy}
                  templatePending={setTicketTemplate.isPending}
                  postureSaving={setCompatibilityPosture.isPending}
                  onWorkflowChange={(template) => {
                    if (selectedId) void requestWorkflowChange(selectedId, template);
                  }}
                  onSaveBranch={(branch) => saveTicketBranch(sel.id, branch)}
                  onPostureChange={(posture) => setCompatibilityPosture.mutate(posture)}
                />
                <div style={{ marginTop: 24 }}>
                  <div
                    className="workflow-lifecycle-label"
                    style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}
                  >
                    <span className="state-label" style={{ margin: 0 }}>
                      Workflow lifecycle
                    </span>
                    <button
                      type="button"
                      className="btn-secondary btn-compact"
                      onClick={() => setTicketModelModalOpen(true)}
                      title="Model settings for this ticket's runs"
                    >
                      Model ·{" "}
                      {runtimeSummaryLabel(
                        sel.orchestration_runtime ?? DEFAULT_ORCHESTRATION_RUNTIME,
                        runtimeOptions.data,
                      )}
                    </button>
                  </div>
                  <WorkflowStageTimeline
                    stages={sel.stages}
                    currentStageKey={sel.workflow_stage_key}
                    renderStageActions={(s) => {
                      const runCheck = canRunStage(sel, s);
                      const isRunningThis = isStageRunning(s.key);
                      return (
                        <>
                          <button
                            type="button"
                            className="btn-secondary btn-compact stage-run-btn"
                            disabled={!runCheck.allowed || workflowBusy || startRun.isPending}
                            title={runCheck.reason}
                            onClick={() => requestStageRun(s.key)}
                          >
                            {stageRunButtonLabel(s, isRunningThis)}
                          </button>
                          <StageOverflowMenu
                            ticket={sel}
                            stage={s}
                            runCheck={runCheck}
                            isRunning={isRunningThis}
                            workflowBusy={workflowBusy || routeWorkflow.isPending || patchStageWorkflow.isPending}
                            onRun={requestStageRun}
                            onCopyTerminal={() =>
                              void buildStageTerminalHandoffCommand(sel, s)
                                .then(copyTerminalCommand)
                                .catch((err) =>
                                  toastActionFailed("Copy terminal command", err),
                                )
                            }
                            onSetCursor={(stageKey) =>
                              patchStageWorkflow.mutate({
                                workflow_stage_key: stageKey,
                                workflow_stage_status: "pending",
                              })
                            }
                            onRouteUpstream={(fromStageKey, toStageKey, nextAgent) =>
                              routeWorkflow.mutate({
                                from_stage_key: fromStageKey,
                                next_stage_key: toStageKey,
                                next_agent: nextAgent,
                              })
                            }
                            onStageStatus={(stageKey, status) =>
                              patchStageWorkflow.mutate({ stage_key: stageKey, stage_status: status })
                            }
                            onEditState={() => setStateModalOpen(true)}
                          />
                        </>
                      );
                    }}
                    renderStageExtras={(s) => (
                      <>
                        {stageKindLabel(s) ? (
                          <div className="workflow-stage-kind">{stageKindLabel(s)}</div>
                        ) : null}
                        <StageRouteHints
                          stage={s}
                          transitions={sel.workflow_transitions ?? []}
                          stages={sel.stages}
                        />
                        {s.note ? (
                          <div
                            className="workflow-stage-note"
                            style={{
                              color:
                                s.status === "blocked"
                                  ? "var(--rdl)"
                                  : s.status === "awaiting"
                                    ? "var(--aml)"
                                    : "var(--txm)",
                            }}
                          >
                            {s.note}
                          </div>
                        ) : null}
                      </>
                    )}
                  />
                </div>
                </>
                )}
              </div>
              <WorkflowRunControls
                ticket={sel}
                workflowTemplates={workflowTemplates.data}
                hasActiveRun={hasActiveRun}
                workflowBusy={workflowBusy}
                startRunPending={startRun.isPending}
                assemblePending={orchestrate.isPending && orchestrate.variables?.ticketId === selectedId}
                pausePending={stopTicket.isPending}
                isStageRunning={isStageRunning}
                onAssemble={() => setAssembleModalOpen(true)}
                onPause={() => stopTicket.mutate()}
                onRerun={requestStageRun}
                onDelete={() => setDeleteTicketTarget(sel)}
              />
            </>
          ) : (
            <div style={{ padding: 40, color: "var(--txl)" }}>Select a ticket</div>
          )}
        </main>
        )}

        {showArtifacts && (
        <section className={`artifacts-pane ${showWorkflow ? "" : "pane-fill"}`.trim()}>
          <div className="tab-bar">
            <ArtifactTabBar
              artifactTab={artifactTab}
              selectedId={selectedId}
              hasRunErrors={hasRunErrors}
              outputCount={artifactsFeed.data?.items.filter((item) => !item.system).length ?? 0}
              approvalCount={humanApprovalCount}
              hasPr={!!sel?.artifacts?.pr}
            />
            <div className="tab-bar-actions">
              <PaneHideButton
                pane="artifacts"
                onHide={() => hidePane("artifacts")}
                disabled={visiblePaneCount <= 1}
              />
            </div>
          </div>
          <div className="artifact-tab-body">
            <ArtifactPaneBody
              artifactTab={artifactTab}
              ticket={sel}
              runs={ticketRuns.data ?? []}
              hasActiveRun={hasActiveRun}
              pendingApprovals={humanApprovalCount}
              selectedId={selectedId}
              activeWorkspaceSlug={activeWorkspaceSlug}
              isOpeningPr={openPr.isPending}
              isCommittingPush={commitPush.isPending}
              onOpenRunLog={setLogRunId}
              onOpenEditorFile={openEditorFile}
              onOpenPr={selectedId ? () => openPr.mutate(selectedId) : undefined}
              onCommitPush={selectedId ? () => commitPush.mutate(selectedId) : undefined}
            />
          </div>
        </section>
        )}
      </div>

      <UpdateStateModal
        open={stateModalOpen}
        ticket={sel ?? null}
        workflowStages={workspaceWorkflow.data?.stages ?? []}
        isSaving={saveStateFromModal.isPending}
        onClose={() => setStateModalOpen(false)}
        onSave={(draft, original) => saveStateFromModal.mutateAsync({ draft, original })}
      />

      <TriageModelModal
        open={ticketModelModalOpen}
        runtime={sel?.orchestration_runtime ?? DEFAULT_ORCHESTRATION_RUNTIME}
        runtimeOptions={ticketRuntimeOptions.data}
        runtimeOptionsError={ticketRuntimeOptions.error}
        isSaving={setTicketRuntime.isPending}
        scopeLabel="Workflow"
        subtitle="Choose a provider, then pick a model for this ticket's agent runs"
        onClose={() => setTicketModelModalOpen(false)}
        onSave={async (runtime) => {
          await setTicketRuntime.mutateAsync(runtime);
        }}
      />

      {createRequest && (
        <CreateWorkItemFlow
          request={createRequest}
          workspaceIsAll={workspace === "all"}
          workspaces={activeWorkspaces}
          selectedTicketId={selectedId}
          ticketTree={visibleTree}
          onCreated={(ticket) => {
            navigateToTicket(ticket.id, { replace: true });
            if (ticket.parent_ticket_id && ticketTree.data) {
              expandPath([...findAncestorIds(ticketTree.data, ticket.parent_ticket_id), ticket.parent_ticket_id]);
            }
          }}
          onClose={() => setCreateRequest(null)}
        />
      )}

      {importSlug && (
        <ImportTicketsFlow
          workspaceSlug={importSlug}
          browsePath={workspaces.data?.find((w) => w.slug === importSlug)?.repo_path?.trim() || "."}
          onClose={() => setImportSlug(null)}
        />
      )}

      {addWorkspaceOpen && (
        <AddWorkspaceFlow
          existingSlugs={(workspaces.data ?? []).map((w) => w.slug)}
          onClose={() => setAddWorkspaceOpen(false)}
          onCreated={(created) => {
            setWorkspace(created.slug);
            setAddWorkspaceOpen(false);
          }}
        />
      )}

      <WorkflowReassignWarning
        preview={pendingWorkflow?.preview ?? null}
        isPending={setTicketTemplate.isPending}
        onCancel={() => setPendingWorkflow(null)}
        onConfirm={() => {
          if (!selectedId || !pendingWorkflow) return;
          setTicketTemplate.mutate({
            ticketId: selectedId,
            template: pendingWorkflow.template,
          });
        }}
      />

      <ConfirmRunStageModal
        open={!!runConfirmStageKey}
        ticket={sel ?? null}
        stage={runConfirmStage}
        workspaceSlug={activeWorkspaceSlug}
        workspaceRuntime={activeWorkspaceRuntime}
        runtimeOptions={runtimeOptions.data}
        isRunning={startRun.isPending || workflowBusy}
        isSavingRuntime={setRuntime.isPending}
        isOpeningPr={openPr.isPending}
        onClose={() => setRunConfirmStageKey(null)}
        onConfirm={confirmStageRun}
        onOpenPr={
          selectedId && runConfirmStage && isHumanGateStage(runConfirmStage)
            ? () => openPr.mutate(selectedId)
            : undefined
        }
      />

      <AgentsAssembleModal
        open={assembleModalOpen}
        ticket={sel ?? null}
        workspaceRuntime={activeWorkspaceRuntime}
        runtimeOptions={runtimeOptions.data}
        stages={sel?.stages ?? []}
        isRunning={orchestrate.isPending}
        isSavingRuntime={setRuntime.isPending}
        onClose={() => setAssembleModalOpen(false)}
        onConfirm={confirmAssemble}
      />

      <DeleteTicketConfirmModal
        open={!!deleteTicketTarget}
        ticket={deleteTicketTarget}
        isDeleting={deleteTicket.isPending}
        error={
          deleteTicket.error instanceof Error
            ? formatDeleteTicketError(deleteTicket.error)
            : null
        }
        onClose={() => {
          if (deleteTicket.isPending) return;
          setDeleteTicketTarget(null);
          deleteTicket.reset();
        }}
        onConfirm={() => deleteTicketTarget && deleteTicket.mutate(deleteTicketTarget.id)}
      />

      <RunLogModal runId={logRunId} onClose={() => setLogRunId(null)} />
    </div>
  );
}
