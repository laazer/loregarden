import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api, API_BASE, type StageStatus, type TicketDetail, type TicketImportPreviewResponse, type TicketTreeNode, type WorkItemType, type WorkflowStageView } from "../api/client";
import { AppTopbarActions } from "../components/AppTopbarActions";
import { DashboardTicketDetailsButton } from "../components/DashboardTicketDetailsButton";
import { PrioBars } from "../components/PrioBars";
import { TicketPaneFilters } from "../components/TicketPaneFilters";
import { ArtifactView } from "../components/dashboard/ArtifactView";
import { HiveSimulationPanel } from "../components/dashboard/HiveSimulationPanel";
import { LogsPanel } from "../components/LogsPanel";
import { TriagePanel } from "../components/TriagePanel";
import { findAncestorIds, TicketTree } from "../components/TicketTree";
import { AgentsAssembleModal, type AgentsAssembleOptions } from "../components/AgentsAssembleModal";
import { ConfirmRunStageModal } from "../components/ConfirmRunStageModal";
import { StageRouteHints } from "../components/StageRouteHints";
import { StageOverflowMenu } from "../components/StageOverflowMenu";
import { WorkflowRunOverflowMenu } from "../components/WorkflowRunOverflowMenu";
import { WorkflowStageTimeline } from "../components/WorkflowStageTimeline";
import {
  isHumanGateStage,
  stageKindLabel,
  stageRunButtonLabel,
} from "../lib/stageDisplay";
import { CreateWorkItemModal, type CreateWorkItemDraft } from "../components/CreateWorkItemModal";
import { IconCloseButton } from "../components/IconCloseButton";
import { ImportTicketsModal } from "../components/ImportTicketsModal";
import { ImportTicketsConfirmModal } from "../components/ImportTicketsConfirmModal";
import { AddWorkspaceModal, type AddWorkspaceDraft } from "../components/AddWorkspaceModal";
import { addChildActionLabel, canHaveChildren } from "../lib/workItemHierarchy";
import { runtimeFromWorkspace, runtimeSettingsEqual } from "../components/WorkspaceRuntimeFields";
import { STATE_COLORS, STATE_LABELS, UpdateStateModal, type StateUpdateDraft } from "../components/UpdateStateModal";
import { navigateToTicket, navigateToTicketTab, useArtifactTabFromRoute, useTicketIdFromRoute } from "../lib/useAppNavigation";
import { isArtifactTab } from "../lib/appNavigation";
import { useUiStore, type PaneId } from "../state/uiStore";
import { agentsAssembleLabel } from "../lib/workflowHelpers";
import { PANE_LABELS } from "../lib/appTopbarConfig";
import {
  buildOrchestrateTerminalCommand,
  buildStageRunTerminalCommand,
} from "../lib/terminalCommands";

function mergeApprovals(...lists: Array<import("../api/client").Approval[] | undefined>) {
  const seen = new Set<string>();
  const merged: import("../api/client").Approval[] = [];
  for (const list of lists) {
    for (const item of list ?? []) {
      if (seen.has(item.id)) continue;
      seen.add(item.id);
      merged.push(item);
    }
  }
  return merged;
}

function PaneHideButton({
  pane,
  onHide,
  disabled,
  className,
}: {
  pane: PaneId;
  onHide: () => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <IconCloseButton
      className={`pane-hide-btn${className ? ` ${className}` : ""}`}
      title={disabled ? "At least one pane must stay visible" : `Hide ${PANE_LABELS[pane]}`}
      aria-label={`Hide ${PANE_LABELS[pane]}`}
      disabled={disabled}
      onClick={onHide}
    />
  );
}

function flattenTree(nodes: TicketTreeNode[]): TicketTreeNode[] {
  const out: TicketTreeNode[] = [];
  for (const n of nodes) {
    out.push(n);
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

function canRunStage(
  ticket: TicketDetail,
  stage: WorkflowStageView,
): { allowed: boolean; reason: string } {
  if (stage.key === "done") {
    if (ticket.state === "done") {
      return { allowed: false, reason: "Ticket already complete" };
    }
    if (stage.status === "done") {
      return { allowed: false, reason: "Ticket already complete" };
    }
  }
  if (stage.status === "wont_do") {
    return { allowed: false, reason: "Stage marked won't do" };
  }
  if (ticket.state === "done" || ticket.state === "wont_do") {
    return { allowed: false, reason: `Ticket is ${STATE_LABELS[ticket.state]}` };
  }
  if (ticket.workflow_stage_status === "awaiting") {
    return { allowed: false, reason: "Resolve approval before running another stage" };
  }
  if (
    ticket.workflow_stage_status === "running" &&
    stage.key !== ticket.workflow_stage_key
  ) {
    return { allowed: false, reason: "Current stage is still running" };
  }
  if (ticket.state === "blocked") {
    const retryable =
      stage.status === "blocked" ||
      stage.status === "done" ||
      (stage.key === ticket.workflow_stage_key &&
        (ticket.workflow_stage_status === "blocked" || ticket.workflow_stage_status === "running"));
    if (!retryable) {
      return { allowed: false, reason: "Resolve the blocked stage before running another" };
    }
  }
  const verb =
    stage.key === "done"
      ? "Complete"
      : stage.status === "done" || stage.status === "blocked"
        ? "Re-run"
        : "Run";
  return { allowed: true, reason: `${verb} ${stage.name}` };
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
    setSearch,
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

  const artifactTabRefs = useRef<Partial<Record<string, HTMLButtonElement>>>({});

  useEffect(() => {
    artifactTabRefs.current[artifactTab]?.scrollIntoView({ block: "nearest", inline: "center" });
  }, [artifactTab]);

  const wsParam = workspace === "all" ? undefined : workspace;

  const ticketTree = useQuery({
    queryKey: ["ticket-tree", workspace, stateFilters, typeFilters, search],
    queryFn: () =>
      api.ticketTree({
        workspace: wsParam,
        state: stateFilters.length ? stateFilters : undefined,
        work_item_type: typeFilters.length ? typeFilters : undefined,
        search: search.trim() || undefined,
      }),
    refetchInterval: (query) =>
      treeHasRunningWorkflow(query.state.data ?? []) ? 1000 : 5000,
  });

  const [createWorkItemOpen, setCreateWorkItemOpen] = useState(false);
  const [createTargetWorkspace, setCreateTargetWorkspace] = useState("");
  const [createParentTicket, setCreateParentTicket] = useState<{
    id: string;
    title: string;
    type: WorkItemType;
    workspaceSlug: string;
  } | null>(null);
  const [addWorkspaceOpen, setAddWorkspaceOpen] = useState(false);
  const [importConfirmOpen, setImportConfirmOpen] = useState(false);
  const [importPickerOpen, setImportPickerOpen] = useState(false);
  const [importPreview, setImportPreview] = useState<TicketImportPreviewResponse | null>(null);
  const [importTargetWorkspace, setImportTargetWorkspace] = useState("");

  const createTickets = useQuery({
    queryKey: ["tickets", "create", createTargetWorkspace],
    queryFn: () => api.tickets({ workspace: createTargetWorkspace }),
    enabled: createWorkItemOpen && !!createTargetWorkspace,
  });

  const flatTickets = useMemo(
    () => flattenTree(ticketTree.data ?? []),
    [ticketTree.data],
  );

  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces });
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
      navigateToTicket(routeTicketId, { tab: "diff", replace: true });
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

  const orchestrate = useMutation({
    mutationFn: ({
      ticketId,
      options,
    }: {
      ticketId: string;
      options?: {
        stop_at_stage_key?: string;
        auto_approve?: boolean;
      };
    }) => api.orchestrate(ticketId, options),
    onSuccess: (_data, { ticketId }) => {
      qc.invalidateQueries({ queryKey: ["ticket", ticketId] });
      qc.invalidateQueries({ queryKey: ["ticket-tree"] });
      setAssembleModalOpen(false);
    },
  });

  const openPr = useMutation({
    mutationFn: (ticketId: string) => api.openPr(ticketId),
    onSuccess: (_data, ticketId) => {
      qc.invalidateQueries({ queryKey: ["ticket", ticketId] });
      navigateToTicketTab(ticketId, "pr");
      setRunConfirmStageKey(null);
    },
  });

  const startRun = useMutation({
    mutationFn: (stageKey?: string) => api.startRun(selectedId!, stageKey ? { stage_key: stageKey } : undefined),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ticket", selectedId] });
      qc.invalidateQueries({ queryKey: ["ticket-tree"] });
      qc.invalidateQueries({ queryKey: ["runs", selectedId] });
      navigateToTicketTab(selectedId!, "logs");
      setRunConfirmStageKey(null);
    },
    onError: () => {
      setRunConfirmStageKey(null);
    },
  });

  const advance = useMutation({
    mutationFn: () => api.advance(selectedId!),
    onSuccess: () => {
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
    mutationFn: (body: {
      from_stage_key: string;
      next_stage_key: string;
      next_agent?: string;
      blocking_issues?: string;
    }) => api.routeWorkflow(selectedId!, body),
    onSuccess: invalidateTicketQueries,
  });

  const patchStageWorkflow = useMutation({
    mutationFn: (body: Parameters<typeof api.updateTicket>[1]) => api.updateTicket(selectedId!, body),
    onSuccess: invalidateTicketQueries,
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
  const [runConfirmStageKey, setRunConfirmStageKey] = useState<string | null>(null);
  const [assembleModalOpen, setAssembleModalOpen] = useState(false);

  const saveStateFromModal = useMutation({
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

  const setTemplate = useMutation({
    mutationFn: ({ slug, template }: { slug: string; template: string }) =>
      api.setWorkspaceTemplate(slug, template),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workspaces"] });
      qc.invalidateQueries({ queryKey: ["workspace-workflow"] });
      qc.invalidateQueries({ queryKey: ["ticket"] });
    },
  });

  const setTicketTemplate = useMutation({
    mutationFn: ({ ticketId, template }: { ticketId: string; template: string }) =>
      api.updateTicket(ticketId, { workflow_template_slug: template }),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["ticket", vars.ticketId] });
      qc.invalidateQueries({ queryKey: ["tickets"] });
      qc.invalidateQueries({ queryKey: ["ticket-tree"] });
    },
  });

  const runtimeOptions = useQuery({
    queryKey: ["runtime-options"],
    queryFn: api.runtimeOptions,
  });

  const setRuntime = useMutation({
    mutationFn: ({
      slug,
      runtime,
    }: {
      slug: string;
      runtime: { cli_adapter: string; claude_model: string; cursor_model: string; lmstudio_base_url: string; lmstudio_model: string };
    }) => api.setWorkspaceRuntime(slug, runtime),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["workspaces"] });
      qc.invalidateQueries({ queryKey: ["workspace-runtime", vars.slug] });
    },
  });

  const createWorkspace = useMutation({
    mutationFn: (draft: AddWorkspaceDraft) =>
      api.createWorkspace({
        slug: draft.slug,
        name: draft.name,
        repo_path: draft.repo_path,
        workflow_template_slug: draft.workflow_template_slug,
        orchestration_profile_slug: draft.orchestration_profile_slug || undefined,
      }),
    onSuccess: (created) => {
      qc.invalidateQueries({ queryKey: ["workspaces"] });
      qc.invalidateQueries({ queryKey: ["workspace-workflow"] });
      setWorkspace(created.slug);
      setAddWorkspaceOpen(false);
    },
  });

  const previewTicketImport = useMutation({
    mutationFn: ({
      workspaceSlug,
      filePaths,
    }: {
      workspaceSlug: string;
      filePaths: string[];
    }) =>
      api.previewTicketImportPaths({
        workspace_slug: workspaceSlug,
        file_paths: filePaths,
      }),
    onSuccess: (preview) => {
      setImportPreview(preview);
      setImportPickerOpen(false);
      setImportConfirmOpen(true);
    },
  });

  const importTickets = useMutation({
    mutationFn: ({
      workspaceSlug,
      tickets,
    }: {
      workspaceSlug: string;
      tickets: TicketImportPreviewResponse["tickets"];
    }) =>
      api.importTickets({
        workspace_slug: workspaceSlug,
        tickets,
      }),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["ticket-tree"] });
      qc.invalidateQueries({ queryKey: ["tickets"] });
      if (result.ticket_ids.length > 0) {
        navigateToTicket(result.ticket_ids[0], true);
      }
      if (result.errors.length > 0) {
        setImportPreview((current) =>
          current
            ? {
                ...current,
                tickets: [],
                total: 0,
                errors: result.errors,
                warnings: [],
                show_preview: false,
              }
            : current,
        );
        return;
      }
      setImportConfirmOpen(false);
      setImportPreview(null);
      importTickets.reset();
    },
  });

  const createWorkItem = useMutation({
    mutationFn: ({
      draft,
      workspaceSlug,
    }: {
      draft: CreateWorkItemDraft;
      workspaceSlug: string;
    }) =>
      api.createTicket({
        workspace_slug: workspaceSlug,
        title: draft.title.trim(),
        work_item_type: draft.work_item_type,
        parent_ticket_id: draft.parent_ticket_id || null,
        description: draft.description.trim(),
        acceptance_criteria: draft.acceptance_criteria
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean),
        priority: draft.priority,
      }),
    onSuccess: (ticket) => {
      qc.invalidateQueries({ queryKey: ["ticket-tree"] });
      qc.invalidateQueries({ queryKey: ["tickets"] });
      navigateToTicket(ticket.id, true);
      if (ticket.parent_ticket_id && ticketTree.data) {
        const ancestors = findAncestorIds(ticketTree.data, ticket.parent_ticket_id);
        expandPath([...ancestors, ticket.parent_ticket_id]);
      }
      setCreateWorkItemOpen(false);
      setCreateParentTicket(null);
    },
  });

  const sel = detail.data;
  const runConfirmStage = sel?.stages.find((s) => s.key === runConfirmStageKey) ?? null;

  const activeWorkspaceSlug =
    workspace === "all" ? (sel?.workspace_slug ?? workspaces.data?.[0]?.slug ?? "loregarden") : workspace;
  const defaultCreateWorkspaceSlug =
    sel?.workspace_slug ?? workspaces.data?.[0]?.slug ?? "loregarden";
  const activeWorkspaceRecord = workspaces.data?.find((w) => w.slug === activeWorkspaceSlug);
  const activeWorkspaceRuntime = runtimeFromWorkspace(activeWorkspaceRecord);
  const importWorkspaceSlug = importTargetWorkspace || defaultCreateWorkspaceSlug;
  const importWorkspaceRecord = workspaces.data?.find((w) => w.slug === importWorkspaceSlug);
  const importBrowsePath = importWorkspaceRecord?.repo_path?.trim() || ".";

  const openCreateWorkItem = () => {
    const slug = workspace === "all" ? defaultCreateWorkspaceSlug : workspace;
    setCreateParentTicket(null);
    setCreateTargetWorkspace(slug);
    createWorkItem.reset();
    setCreateWorkItemOpen(true);
  };

  const openImportTickets = () => {
    const slug = workspace === "all" ? defaultCreateWorkspaceSlug : workspace;
    if (!slug) return;
    setImportTargetWorkspace(slug);
    previewTicketImport.reset();
    importTickets.reset();
    setImportPickerOpen(true);
  };

  const handleImportPathsContinue = async (filePaths: string[]) => {
    const slug = importTargetWorkspace || defaultCreateWorkspaceSlug;
    if (!slug || filePaths.length === 0) return;
    try {
      await previewTicketImport.mutateAsync({ workspaceSlug: slug, filePaths });
    } catch {
      setImportPreview({
        tickets: [],
        errors: ["Failed to read or parse the selected files. Check the format and try again."],
        warnings: [],
        total: 0,
        by_type: {},
        formats: [],
        show_preview: false,
      });
      setImportPickerOpen(false);
      setImportConfirmOpen(true);
    }
  };

  const previewTicketImportError =
    previewTicketImport.error instanceof Error
      ? (() => {
          try {
            const parsed = JSON.parse(previewTicketImport.error.message) as { detail?: string };
            return parsed.detail ?? previewTicketImport.error.message;
          } catch {
            return previewTicketImport.error.message;
          }
        })()
      : null;

  const openCreateSubTicket = (parent: {
    id: string;
    title: string;
    work_item_type: WorkItemType;
    workspace_slug?: string;
  }) => {
    if (!canHaveChildren(parent.work_item_type)) return;
    const slug =
      parent.workspace_slug ||
      (workspace !== "all" ? workspace : defaultCreateWorkspaceSlug);
    if (!slug) return;
    setCreateParentTicket({
      id: parent.id,
      title: parent.title,
      type: parent.work_item_type,
      workspaceSlug: slug,
    });
    setCreateTargetWorkspace(slug);
    createWorkItem.reset();
    setCreateWorkItemOpen(true);
  };

  const createWorkItemError =
    createWorkItem.error instanceof Error
      ? (() => {
          try {
            const parsed = JSON.parse(createWorkItem.error.message) as { detail?: string };
            return parsed.detail ?? createWorkItem.error.message;
          } catch {
            return createWorkItem.error.message;
          }
        })()
      : null;

  const createWorkspaceError =
    createWorkspace.error instanceof Error
      ? (() => {
          try {
            const parsed = JSON.parse(createWorkspace.error.message) as { detail?: string };
            return parsed.detail ?? createWorkspace.error.message;
          } catch {
            return createWorkspace.error.message;
          }
        })()
      : null;

  const openAddWorkspace = () => {
    createWorkspace.reset();
    setAddWorkspaceOpen(true);
  };

  const requestStageRun = (stageKey: string) => setRunConfirmStageKey(stageKey);
  const confirmStageRun = async (runtime: typeof activeWorkspaceRuntime) => {
    if (!runConfirmStageKey) return;
    try {
      if (!runtimeSettingsEqual(runtime, activeWorkspaceRuntime)) {
        await setRuntime.mutateAsync({ slug: activeWorkspaceSlug, runtime });
      }
      await startRun.mutateAsync(runConfirmStageKey);
    } catch {
      // Modal stays open; mutation error state clears on retry.
    }
  };

  const confirmAssemble = async (options: AgentsAssembleOptions) => {
    if (!selectedId || !sel) return;
    try {
      if (!runtimeSettingsEqual(options.runtime, activeWorkspaceRuntime)) {
        await setRuntime.mutateAsync({ slug: activeWorkspaceSlug, runtime: options.runtime });
      }
      if (options.branch !== (sel.branch || "")) {
        await api.updateTicket(selectedId, { branch: options.branch });
      }
      await orchestrate.mutateAsync({
        ticketId: selectedId,
        options: {
          stop_at_stage_key: options.stopAtStageKey || undefined,
          auto_approve: options.autoApprove,
        },
      });
    } catch {
      // Modal stays open on error.
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
    (startRun.isPending && startRun.variables === stageKey);

  const triage = useQuery({
    queryKey: ["triage", selectedId],
    queryFn: () => api.triage(selectedId!),
    enabled: !!selectedId,
    retry: 1,
    refetchInterval: (query) => {
      const pending = query.state.data?.pending_approvals?.length ?? 0;
      return pending > 0 ? 2000 : 8000;
    },
  });

  const ticketApprovals = useQuery({
    queryKey: ["approvals", selectedId],
    queryFn: () => api.approvals(selectedId!),
    enabled: !!selectedId,
    refetchInterval: 2000,
  });

  const triagePendingCount =
    mergeApprovals(triage.data?.pending_approvals, ticketApprovals.data).length;

  const hasRunErrors = Boolean(
    sel?.blocking_issues ||
      sel?.artifacts?.error ||
      ticketRuns.data?.some((r) => r.status === "failed" && r.stderr),
  );

  const lastAutoTabTicketId = useRef<string | null>(null);

  useEffect(() => {
    if (!sel?.id) return;
    if (lastAutoTabTicketId.current === sel.id) return;
    lastAutoTabTicketId.current = sel.id;

    if (sel.blocking_issues || sel.artifacts?.error) {
      navigateToTicketTab(sel.id, "errors", true);
    }
  }, [sel?.id, sel?.blocking_issues, sel?.artifacts?.error]);

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
    <div className="screen-view screen-view--ide">
      <header className="topbar ide-topbar">
        <div className="ide-topbar-brand">
          <div className="brand-title">loregarden</div>
          <div className="brand-sub">Agent SDLC · Console</div>
        </div>
        <label className="topbar-search">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
            <circle cx="11" cy="11" r="7" />
            <path d="m20 20-3.5-3.5" />
          </svg>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search tickets, agents, runs…"
            aria-label="Search tickets"
          />
          <kbd>⌘K</kbd>
        </label>
        <div className="topbar-spacer" />
        <AppTopbarActions />
      </header>

      <div className="main-panes">
        {showSidebar && (
          <aside
            className={`sidebar ${showWorkspaces && showTickets ? "" : "sidebar-single-pane"}`.trim()}
          >
            {showWorkspaces && (
              <div className={`workspaces-pane ${showTickets ? "" : "pane-fill"}`.trim()}>
                <div className="pane-header">
                  <span className="pane-title">Workspaces</span>
                  <span className="count-pill">{(workspaces.data?.length ?? 0) + 1}</span>
                  <div style={{ flex: 1 }} />
                  <button
                    type="button"
                    className="btn-secondary btn-compact"
                    onClick={openAddWorkspace}
                    title="Add workspace"
                  >
                    + Add
                  </button>
                  <PaneHideButton
                    pane="workspaces"
                    onHide={() => hidePane("workspaces")}
                    disabled={visiblePaneCount <= 1}
                  />
                </div>
            <div className="scroll-list">
              <button
                type="button"
                className={`workspace-btn list-btn ${workspace === "all" ? "active" : ""}`}
                onClick={() => setWorkspace("all")}
              >
                <span className="workspace-icon workspace-icon--all" aria-hidden>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <rect x="3" y="3" width="7" height="7" rx="1.5" />
                    <rect x="14" y="3" width="7" height="7" rx="1.5" />
                    <rect x="3" y="14" width="7" height="7" rx="1.5" />
                    <rect x="14" y="14" width="7" height="7" rx="1.5" />
                  </svg>
                </span>
                <span className="workspace-copy">
                  <span className="workspace-name">All workspaces</span>
                  <span className="workspace-meta">Every repo</span>
                </span>
                <span className="count-pill">{flatTickets.length}</span>
              </button>
              {workspaces.data?.map((w) => (
                <button
                  key={w.id}
                  type="button"
                  className={`workspace-btn list-btn ${workspace === w.slug ? "active" : ""}`}
                  onClick={() => setWorkspace(w.slug)}
                >
                  <span
                    className="workspace-icon"
                    style={{ background: "rgba(45,212,167,.14)", color: "var(--ac2)" }}
                    aria-hidden
                  >
                    {w.name.charAt(0).toUpperCase()}
                  </span>
                  <span className="workspace-copy">
                    <span className="workspace-name">{w.name}</span>
                    <span className="workspace-meta">
                      {w.workflow_template_slug || "No workflow"}
                      {!w.repo_exists ? " · repo missing" : ""}
                    </span>
                  </span>
                  {w.blocked_count > 0 ? (
                    <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--red)", flex: "none" }} />
                  ) : null}
                  <span className="count-pill">{w.ticket_count}</span>
                </button>
              ))}
              {workspace !== "all" && workflowTemplates.data && (
                <div style={{ padding: "8px 4px 0" }}>
                  <div className="state-label" style={{ marginBottom: 6 }}>
                    Workflow template
                  </div>
                  <select
                    className="btn-secondary"
                    style={{ width: "100%", fontSize: 12 }}
                    value={
                      workspaces.data?.find((w) => w.slug === workspace)?.workflow_template_slug ?? ""
                    }
                    onChange={(e) =>
                      setTemplate.mutate({ slug: workspace, template: e.target.value })
                    }
                  >
                    {workflowTemplates.data.map((t) => (
                      <option key={t.slug} value={t.slug}>
                        {t.name} ({t.stage_count} stages)
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </div>
              </div>
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
                        disabled={!defaultCreateWorkspaceSlug || previewTicketImport.isPending}
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
                <div className="scroll-list">
                  {ticketTree.data?.length ? (
                    <TicketTree
                      nodes={ticketTree.data}
                      selectedId={selectedId}
                      expandedIds={expandedSet}
                      onSelect={selectTicket}
                      onToggle={toggleExpanded}
                      onAddChild={openCreateSubTicket}
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
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8, alignItems: "center" }}>
                  <span className="count-pill">{sel.workspace_slug}</span>
                  <span className="count-pill">{sel.work_item_type}</span>
                  {canHaveChildren(sel.work_item_type) && (
                    <button
                      type="button"
                      className="btn-secondary btn-compact"
                      title={addChildActionLabel(sel.work_item_type)}
                      onClick={() => openCreateSubTicket(sel)}
                    >
                      + Sub-item
                    </button>
                  )}
                </div>
                {workflowTemplates.data && workflowTemplates.data.length > 0 && (
                  <div style={{ marginBottom: 16 }}>
                    <div className="state-label" style={{ marginBottom: 6 }}>
                      Workflow template
                    </div>
                    <select
                      className="btn-secondary"
                      style={{ width: "100%", maxWidth: 360, fontSize: 12 }}
                      value={sel.workflow_template_slug || ""}
                      disabled={workflowBusy || setTicketTemplate.isPending}
                      onChange={(e) => {
                        if (!selectedId || e.target.value === sel.workflow_template_slug) return;
                        setTicketTemplate.mutate({ ticketId: selectedId, template: e.target.value });
                      }}
                    >
                      <option value="">No workflow</option>
                      {workflowTemplates.data.map((t) => (
                        <option key={t.slug} value={t.slug}>
                          {t.name} ({t.stage_count} stages)
                        </option>
                      ))}
                    </select>
                    {sel.workflow_template_slug &&
                      workspaceWorkflow.data?.template_slug &&
                      sel.workflow_template_slug !== workspaceWorkflow.data.template_slug && (
                        <div style={{ fontSize: 11, color: "var(--txm)", marginTop: 6 }}>
                          Workspace default: {workspaceWorkflow.data.template_name}
                        </div>
                      )}
                  </div>
                )}
                <div style={{ marginBottom: 16 }}>
                  <div className="state-label" style={{ marginBottom: 6 }}>
                    Branch
                  </div>
                  <div style={{ display: "flex", gap: 8, alignItems: "center", maxWidth: 360 }}>
                    <input
                      className="btn-secondary"
                      style={{ flex: 1, minWidth: 0, fontSize: 12, boxSizing: "border-box" }}
                      value={sel.branch || ""}
                      placeholder={`loregarden/${sel.external_id}`}
                      onChange={(e) => {
                        if (!selectedId) return;
                        qc.setQueryData(["ticket", selectedId], (current: TicketDetail | undefined) =>
                          current ? { ...current, branch: e.target.value } : current,
                        );
                      }}
                      onBlur={(e) => {
                        if (!selectedId || e.target.value === (detail.data?.branch ?? "")) return;
                        api.updateTicket(selectedId, { branch: e.target.value.trim() }).then(() => {
                          qc.invalidateQueries({ queryKey: ["ticket", selectedId] });
                        });
                      }}
                    />
                    <button
                      type="button"
                      className="btn-secondary btn-compact"
                      title="Set branch to main"
                      onClick={() => {
                        if (!selectedId) return;
                        qc.setQueryData(["ticket", selectedId], (current: TicketDetail | undefined) =>
                          current ? { ...current, branch: "main" } : current,
                        );
                        if ((detail.data?.branch ?? "") !== "main") {
                          api.updateTicket(selectedId, { branch: "main" }).then(() => {
                            qc.invalidateQueries({ queryKey: ["ticket", selectedId] });
                          });
                        }
                      }}
                    >
                      Use main
                    </button>
                  </div>
                </div>
                <div className="dual-state">
                  <div className="state-card">
                    <div className="state-label">Ticket state · WHAT</div>
                    <div style={{ fontWeight: 600, color: STATE_COLORS[sel.state] }}>
                      {STATE_LABELS[sel.state]}
                    </div>
                    {sel.state_locked && (
                      <span className="count-pill" style={{ marginTop: 8, fontSize: 10 }}>
                        locked
                      </span>
                    )}
                  </div>
                  <div className="state-card">
                    <div className="state-label">Workflow · HOW</div>
                    <div style={{ fontWeight: 600 }}>
                      {sel.workflow_stage_name || sel.workflow_stage_key || "—"}
                    </div>
                    <div style={{ fontSize: 11, color: "var(--txm)", marginTop: 4 }}>
                      {sel.workflow_stage_status.replace("_", " ")}
                    </div>
                    {sel.next_agent?.trim() && (
                      <div style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--txm)", marginTop: 6 }}>
                        next agent · {sel.next_agent}
                      </div>
                    )}
                  </div>
                </div>
                {sel.blocking_issues?.trim() && (
                  <div
                    style={{
                      marginTop: 12,
                      padding: "10px 12px",
                      borderRadius: 11,
                      background: "rgba(199,125,45,.08)",
                      border: "1px solid rgba(199,125,45,.28)",
                      fontSize: 12,
                      color: "var(--orl, #c77d2d)",
                    }}
                  >
                    <div style={{ fontWeight: 600, marginBottom: 4 }}>Rework required</div>
                    <div style={{ whiteSpace: "pre-wrap" }}>{sel.blocking_issues}</div>
                  </div>
                )}
                <button
                  type="button"
                  className="btn-secondary"
                  style={{ marginTop: 10 }}
                  onClick={() => setStateModalOpen(true)}
                >
                  Update state…
                </button>

                {hasRunErrors && (
                  <div
                    style={{
                      marginTop: 16,
                      padding: "10px 12px",
                      borderRadius: 11,
                      background: "rgba(240,96,63,.1)",
                      border: "1px solid rgba(240,96,63,.3)",
                      fontSize: 12,
                      color: "var(--rdl)",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      gap: 12,
                    }}
                  >
                    <span>Run or workflow issue recorded</span>
                    <button
                      type="button"
                      className="btn-secondary btn-compact"
                      onClick={() => selectedId && navigateToTicketTab(selectedId, "errors")}
                    >
                      View Errors
                    </button>
                  </div>
                )}

                <div style={{ marginTop: 24 }}>
                  <div className="state-label workflow-lifecycle-label">
                    Workflow lifecycle
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
                              void copyTerminalCommand(buildStageRunTerminalCommand(sel, s, API_BASE))
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
              </div>
              <div className="run-controls">
                <button
                  className="btn-primary"
                  disabled={
                    !selectedId ||
                    (orchestrate.isPending && orchestrate.variables?.ticketId === selectedId)
                  }
                  onClick={() => setAssembleModalOpen(true)}
                >
                  {agentsAssembleLabel(
                    sel,
                    orchestrate.isPending && orchestrate.variables?.ticketId === selectedId,
                  )}
                </button>
                <div style={{ flex: 1 }} />
                {(() => {
                  const cursorStage = sel.stages.find((s) => s.key === sel.workflow_stage_key);
                  const cursorRun = cursorStage ? canRunStage(sel, cursorStage) : { allowed: false, reason: "No cursor stage" };
                  const runningCursor = isStageRunning(sel.workflow_stage_key);
                  return (
                    <WorkflowRunOverflowMenu
                      ticket={sel}
                      orchestrateCommand={buildOrchestrateTerminalCommand(sel, API_BASE)}
                      cursorStage={cursorStage}
                      cursorRun={cursorRun}
                      runningCursor={runningCursor}
                      workflowBusy={workflowBusy}
                      startRunPending={startRun.isPending}
                      advancePending={advance.isPending}
                      onRunCurrentStage={() => requestStageRun(sel.workflow_stage_key)}
                      onAdvance={() => advance.mutate()}
                    />
                  );
                })()}
              </div>
            </>
          ) : (
            <div style={{ padding: 40, color: "var(--txl)" }}>Select a ticket</div>
          )}
        </main>
        )}

        {showArtifacts && (
        <section className={`artifacts-pane ${showWorkflow ? "" : "pane-fill"}`.trim()}>
          <div className="tab-bar">
            <div className="tab-bar-scroll" role="tablist" aria-label="Artifact views">
              {(["diff", "errors", "triage", "logs", "tests", "hive", "context", "pr"] as const).map((t) => (
                <button
                  key={t}
                  ref={(el) => {
                    if (el) artifactTabRefs.current[t] = el;
                  }}
                  role="tab"
                  aria-selected={artifactTab === t}
                  className={`tab-btn ${artifactTab === t ? "active" : ""}`}
                  onClick={() => selectedId && navigateToTicketTab(selectedId, t)}
                  style={
                    t === "errors" && hasRunErrors
                      ? { color: "var(--rdl)" }
                      : t === "triage" && triagePendingCount > 0
                        ? { color: "var(--amb)" }
                        : undefined
                  }
                >
                  {t.charAt(0).toUpperCase() + t.slice(1)}
                  {t === "errors" && hasRunErrors && (
                    <span
                      style={{
                        marginLeft: 6,
                        minWidth: 8,
                        height: 8,
                        borderRadius: "50%",
                        background: "var(--red)",
                        display: "inline-block",
                      }}
                    />
                  )}
                  {t === "triage" && triagePendingCount > 0 && (
                    <span className="count-pill" style={{ marginLeft: 6, fontSize: 9 }}>
                      {triagePendingCount}
                    </span>
                  )}
                  {t === "pr" && sel?.artifacts?.pr && (
                    <span
                      style={{
                        marginLeft: 6,
                        minWidth: 8,
                        height: 8,
                        borderRadius: "50%",
                        background: "var(--ac2)",
                        display: "inline-block",
                      }}
                    />
                  )}
                </button>
              ))}
            </div>
            <div className="tab-bar-actions">
              <PaneHideButton
                pane="artifacts"
                onHide={() => hidePane("artifacts")}
                disabled={visiblePaneCount <= 1}
              />
            </div>
          </div>
          <div style={{ flex: 1, overflow: "auto", minHeight: 0 }}>
            {artifactTab === "triage" ? (
              <TriagePanel
                ticket={sel}
                runtimeOptions={runtimeOptions.data}
                onResolved={() => {
                  qc.invalidateQueries({ queryKey: ["triage", selectedId] });
                  qc.invalidateQueries({ queryKey: ["ticket", selectedId] });
                  qc.invalidateQueries({ queryKey: ["runs", selectedId] });
                }}
              />
            ) : artifactTab === "logs" && sel ? (
              <LogsPanel
                ticket={sel}
                runtimeOptions={runtimeOptions.data}
                onResolved={() => {
                  qc.invalidateQueries({ queryKey: ["triage", selectedId] });
                  qc.invalidateQueries({ queryKey: ["ticket", selectedId] });
                  qc.invalidateQueries({ queryKey: ["runs", selectedId] });
                }}
              />
            ) : artifactTab === "hive" && sel ? (
              <HiveSimulationPanel ticket={sel} />
            ) : (
              <ArtifactView
                tab={artifactTab}
                ticket={sel}
                runs={ticketRuns.data ?? []}
                onOpenEditorFile={(filePath) =>
                  openEditorFile(sel?.workspace_slug ?? activeWorkspaceSlug, filePath)
                }
              />
            )}
          </div>
        </section>
        )}
      </div>

      <footer className="status-bar">
        {sel?.branch ? (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--txl)" strokeWidth="2" aria-hidden>
              <circle cx="6" cy="6" r="3" />
              <circle cx="6" cy="18" r="3" />
              <path d="M6 9v6" />
              <circle cx="18" cy="6" r="3" />
              <path d="M18 9a9 9 0 0 1-9 9" />
            </svg>
            {sel.branch}
          </span>
        ) : null}
        {sel?.run_code ? (
          <span style={{ fontFamily: "var(--mono)", color: "var(--txl)" }}>{sel.run_code}</span>
        ) : null}
        <span className="status-bar-live">
          <span className="status-bar-live-dot" />
          agents online
        </span>
        <div style={{ flex: 1 }} />
        <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--txl)" }}>
          {sel?.workflow_template_slug || "truth layer · execution output"}
        </span>
      </footer>

      <UpdateStateModal
        open={stateModalOpen}
        ticket={sel ?? null}
        workflowStages={workspaceWorkflow.data?.stages ?? []}
        isSaving={saveStateFromModal.isPending}
        onClose={() => setStateModalOpen(false)}
        onSave={(draft, original) => saveStateFromModal.mutateAsync({ draft, original })}
      />

      <CreateWorkItemModal
        open={createWorkItemOpen}
        workspaceSlug={createTargetWorkspace}
        workspacePicker={workspace === "all" && !createParentTicket}
        workspaces={(workspaces.data ?? []).map((w) => ({ slug: w.slug, name: w.name }))}
        onWorkspaceSlugChange={setCreateTargetWorkspace}
        tickets={createTickets.data ?? []}
        selectedTicketId={selectedId}
        ticketTree={ticketTree.data ?? []}
        parentTicketId={createParentTicket?.id ?? null}
        parentTicketTitle={createParentTicket?.title}
        parentTicketType={createParentTicket?.type ?? null}
        lockParent={!!createParentTicket}
        isSaving={createWorkItem.isPending}
        errorMessage={createWorkItemError}
        onClose={() => {
          createWorkItem.reset();
          setCreateParentTicket(null);
          setCreateWorkItemOpen(false);
        }}
        onCreate={async (draft) => {
          await createWorkItem.mutateAsync({ draft, workspaceSlug: createTargetWorkspace });
        }}
      />

      <ImportTicketsModal
        open={importPickerOpen}
        workspaceSlug={importWorkspaceSlug}
        initialBrowsePath={importBrowsePath}
        isLoading={previewTicketImport.isPending}
        errorMessage={previewTicketImportError}
        onClose={() => {
          if (previewTicketImport.isPending) return;
          setImportPickerOpen(false);
          previewTicketImport.reset();
        }}
        onContinue={handleImportPathsContinue}
      />

      <ImportTicketsConfirmModal
        open={importConfirmOpen}
        workspaceSlug={importTargetWorkspace || defaultCreateWorkspaceSlug}
        preview={importPreview}
        isImporting={importTickets.isPending}
        importError={
          importTickets.error instanceof Error
            ? (() => {
                try {
                  const parsed = JSON.parse(importTickets.error.message) as { detail?: string };
                  return parsed.detail ?? importTickets.error.message;
                } catch {
                  return importTickets.error.message;
                }
              })()
            : null
        }
        onClose={() => {
          if (importTickets.isPending) return;
          setImportConfirmOpen(false);
          setImportPreview(null);
          importTickets.reset();
        }}
        onConfirm={async (tickets) => {
          if (tickets.length === 0) return;
          const slug = importTargetWorkspace || defaultCreateWorkspaceSlug;
          await importTickets.mutateAsync({
            workspaceSlug: slug,
            tickets,
          });
        }}
      />

      <AddWorkspaceModal
        open={addWorkspaceOpen}
        templates={workflowTemplates.data ?? []}
        existingSlugs={(workspaces.data ?? []).map((w) => w.slug)}
        isSaving={createWorkspace.isPending}
        errorMessage={createWorkspaceError ?? undefined}
        onClose={() => {
          createWorkspace.reset();
          setAddWorkspaceOpen(false);
        }}
        onCreate={async (draft) => {
          await createWorkspace.mutateAsync(draft);
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
    </div>
  );
}

