import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, API_BASE, isWorkflowWorkItem, type DiffArtifact, type DiffFileSection, type StageStatus, type TicketDetail, type TicketImportPreviewResponse, type TicketTreeNode, type WorkItemType, type WorkflowStageView } from "../api/client";
import { ApprovalCard } from "../components/ApprovalCard";
import { BrandMark } from "../components/BrandMark";
import { DashboardTicketDetailsButton } from "../components/DashboardTicketDetailsButton";
import { LogsPanel } from "../components/LogsPanel";
import { TriagePanel } from "../components/TriagePanel";
import { collectExpandableIds, findAncestorIds, TicketTree } from "../components/TicketTree";
import { AgentsAssembleModal, type AgentsAssembleOptions } from "../components/AgentsAssembleModal";
import { ConfirmRunStageModal } from "../components/ConfirmRunStageModal";
import {
  currentStageRunLabel,
  isAgentStage,
  isHumanGateStage,
  stageAgentSubtitle,
  stageKindLabel,
  stageRunButtonLabel,
} from "../lib/stageDisplay";
import { CopyTerminalCommandButton } from "../components/CopyTerminalCommandButton";
import { CreateWorkItemModal, type CreateWorkItemDraft } from "../components/CreateWorkItemModal";
import { ImportTicketsConfirmModal } from "../components/ImportTicketsConfirmModal";
import { ImportTicketsModal } from "../components/ImportTicketsModal";
import { AddWorkspaceModal, type AddWorkspaceDraft } from "../components/AddWorkspaceModal";
import { addChildActionLabel, canHaveChildren } from "../lib/workItemHierarchy";
import { SettingsModal } from "../components/SettingsModal";
import { MemorySetupModal } from "../components/MemorySetupModal";
import { UsageModal } from "../components/UsageModal";
import { runtimeFromWorkspace, runtimeSettingsEqual } from "../components/WorkspaceRuntimeFields";
import { STATE_COLORS, STATE_LABELS, UpdateStateModal, type StateUpdateDraft } from "../components/UpdateStateModal";
import { useUiStore, type PaneId } from "../state/uiStore";
import { formatApprovalResolveError } from "../utils/approvalErrors";
import { agentsAssembleLabel } from "../lib/workflowHelpers";
import {
  buildOrchestrateTerminalCommand,
  buildStageRunTerminalCommand,
  isAgentWorkflowTicket,
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

const PANE_LABELS: Record<PaneId, string> = {
  workspaces: "Workspaces",
  tickets: "Work items",
  workflow: "Workflow",
  artifacts: "Artifacts",
};

function PaneHideButton({
  pane,
  onHide,
  disabled,
}: {
  pane: PaneId;
  onHide: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      className="pane-hide-btn"
      title={disabled ? "At least one pane must stay visible" : `Hide ${PANE_LABELS[pane]}`}
      aria-label={`Hide ${PANE_LABELS[pane]}`}
      disabled={disabled}
      onClick={onHide}
    >
      ✕
    </button>
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

const TYPE_FILTERS: { id: WorkItemType; label: string }[] = [
  { id: "milestone", label: "Milestones" },
  { id: "feature", label: "Features" },
  { id: "capability", label: "Capabilities" },
  { id: "task", label: "Tasks" },
  { id: "bug", label: "Bugs" },
];

const STATE_FILTER_OPTIONS = ["all", "backlog", "in_progress", "blocked", "done", "wont_do"] as const;

const PRIO_BARS: Record<number, string[]> = {
  1: ["var(--red)", "var(--red)", "var(--red)"],
  2: ["var(--amb)", "var(--amb)", "var(--bd2)"],
  3: ["var(--txm)", "var(--bd2)", "var(--bd2)"],
};

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

function diffFileSections(diff: DiffArtifact): DiffFileSection[] {
  if (diff.sections?.length) {
    return diff.sections;
  }
  if (!diff.lines?.length) {
    return [];
  }

  const sections: DiffFileSection[] = [];
  let current: DiffFileSection | null = null;

  for (const line of diff.lines) {
    const header = line.text.match(/^\+\+\+ b\/(.+)$/);
    if (header) {
      if (current?.lines.length) {
        sections.push(current);
      }
      current = { path: header[1], add: 0, del: 0, lines: [] };
      continue;
    }
    if (!current) {
      current = { path: diff.file || "changes", add: 0, del: 0, lines: [] };
    }
    current.lines.push(line);
    if (line.type === "a") current.add += 1;
    if (line.type === "d") current.del += 1;
  }
  if (current?.lines.length) {
    sections.push(current);
  }
  return sections;
}

function DiffLineView({ line }: { line: DiffFileSection["lines"][number] }) {
  return (
    <div className={`diff-line ${line.type === "a" ? "add" : line.type === "d" ? "del" : ""}`}>
      <span style={{ width: 44, textAlign: "right", paddingRight: 12, color: "var(--txl)" }}>{line.ln}</span>
      <span style={{ width: 15, textAlign: "center" }}>
        {line.type === "a" ? "+" : line.type === "d" ? "−" : line.type === "h" ? "@" : " "}
      </span>
      <span style={{ flex: 1, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{line.text}</span>
    </div>
  );
}

function PrioBars({ priority }: { priority: number }) {
  const bars = PRIO_BARS[priority] ?? PRIO_BARS[3];
  return (
    <div className="prio-bars">
      {bars.map((c, i) => (
        <span key={i} style={{ height: 6 + i * 3, background: c }} />
      ))}
    </div>
  );
}

export function Dashboard() {
  const qc = useQueryClient();
  const {
    selectedTicketId,
    stateFilters,
    typeFilters,
    search,
    expandedTicketIds,
    workspace,
    tab,
    inboxOpen,
    setSelectedTicketId,
    toggleStateFilter,
    clearStateFilters,
    toggleTypeFilter,
    clearTypeFilters,
    setSearch,
    toggleExpanded,
    expandAll,
    collapseAll,
    expandPath,
    setWorkspace,
    setTab,
    setAppPage,
    setInboxOpen,
    paneVisibility,
    setPaneVisible,
  } = useUiStore();

  const { workspaces: showWorkspaces, tickets: showTickets, workflow: showWorkflow, artifacts: showArtifacts } =
    paneVisibility;
  const showSidebar = showWorkspaces || showTickets;
  const visiblePaneCount = Object.values(paneVisibility).filter(Boolean).length;

  const hidePane = (pane: PaneId) => setPaneVisible(pane, false);
  const hiddenPanes = (Object.entries(paneVisibility) as [PaneId, boolean][])
    .filter(([, visible]) => !visible)
    .map(([pane]) => pane);

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

  const selectedId =
    selectedTicketId ??
    flatTickets.find((t) => isWorkflowWorkItem(t.work_item_type))?.id ??
    flatTickets[0]?.id ??
    null;

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

  const approvals = useQuery({
    queryKey: ["approvals"],
    queryFn: () => api.approvals(),
    refetchInterval: 5000,
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
      setTab("pr");
      setRunConfirmStageKey(null);
    },
  });

  const startRun = useMutation({
    mutationFn: (stageKey?: string) => api.startRun(selectedId!, stageKey ? { stage_key: stageKey } : undefined),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ticket", selectedId] });
      qc.invalidateQueries({ queryKey: ["ticket-tree"] });
      qc.invalidateQueries({ queryKey: ["runs", selectedId] });
      setTab("logs");
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

  const resolveApproval = useMutation({
    mutationFn: ({
      id,
      action,
      answers,
      response,
      always_allow,
      allow_for_ticket,
      allow_for_stage,
    }: {
      id: string;
      action: "approve" | "reject";
      answers?: Record<string, string | string[]>;
      response?: string;
      always_allow?: boolean;
      allow_for_ticket?: boolean;
      allow_for_stage?: boolean;
    }) => api.resolveApproval(id, { action, answers, response, always_allow, allow_for_ticket, allow_for_stage }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["approvals"] });
      qc.invalidateQueries({ queryKey: ["ticket"] });
    },
  });

  const [stateModalOpen, setStateModalOpen] = useState(false);
  const [runConfirmStageKey, setRunConfirmStageKey] = useState<string | null>(null);
  const [assembleModalOpen, setAssembleModalOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [usageOpen, setUsageOpen] = useState(false);
  const [settingsWorkspaceSlug, setSettingsWorkspaceSlug] = useState("loregarden");

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

  const usage = useQuery({
    queryKey: ["usage"],
    queryFn: api.usage,
    refetchInterval: usageOpen ? 60_000 : 5 * 60_000,
    staleTime: 30_000,
  });

  const memoryConfig = useQuery({
    queryKey: ["memory-config"],
    queryFn: api.memoryConfig,
    enabled: memoryOpen,
  });

  const setMemoryConfig = useMutation({
    mutationFn: api.setMemoryConfig,
    onSuccess: (data) => {
      qc.setQueryData(["memory-config"], data);
    },
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
        setSelectedTicketId(result.ticket_ids[0]);
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
      setSelectedTicketId(ticket.id);
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

  const openSettings = () => {
    setSettingsWorkspaceSlug(activeWorkspaceSlug);
    setSettingsOpen(true);
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
  const approvalCount = approvals.data?.length ?? 0;

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
      setTab("errors");
    }
  }, [sel?.id, sel?.blocking_issues, sel?.artifacts?.error, setTab]);

  const counts = flatTickets.reduce(
    (acc, t) => {
      if (isWorkflowWorkItem(t.work_item_type)) {
        acc.all += 1;
        acc[t.state] += 1;
      }
      return acc;
    },
    { all: 0, backlog: 0, in_progress: 0, blocked: 0, done: 0, wont_do: 0 } as Record<string, number>,
  );

  const expandedSet = useMemo(() => new Set(expandedTicketIds), [expandedTicketIds]);

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <BrandMark />
          <div>
            <div className="brand-title">loregarden</div>
            <div className="brand-sub">Agent SDLC</div>
          </div>
        </div>
        <div style={{ flex: 1 }} />
        {hiddenPanes.length > 0 && (
          <div className="pane-restore-group">
            {hiddenPanes.map((pane) => (
              <button
                key={pane}
                type="button"
                className="btn-secondary btn-compact pane-restore-btn"
                onClick={() => setPaneVisible(pane, true)}
              >
                Show {PANE_LABELS[pane]}
              </button>
            ))}
          </div>
        )}
        <button type="button" className="btn-secondary" onClick={() => setAppPage("studio")}>
          Studio
        </button>
        <button type="button" className="btn-secondary" onClick={() => setMemoryOpen(true)}>
          Memory
        </button>
        <button
          type="button"
          className={`btn-secondary usage-btn${usage.data?.near_limit && !usageOpen ? " usage-btn-warning" : ""}`}
          onClick={() => setUsageOpen(true)}
          aria-label={
            usage.data?.near_limit
              ? "Usage limits are getting close — open usage details"
              : "Open Claude and Cursor usage"
          }
          style={{ display: "flex", alignItems: "center", gap: 8 }}
        >
          Usage
          {usage.data?.near_limit ? (
            <span className="usage-alert-badge" aria-hidden="true">
              !
            </span>
          ) : null}
        </button>
        <button type="button" className="btn-secondary" onClick={openSettings}>
          Settings
        </button>
        <button
          type="button"
          className={`btn-secondary${approvalCount > 0 && !inboxOpen ? " approvals-btn-pending" : ""}`}
          onClick={() => setInboxOpen(true)}
          style={{ display: "flex", alignItems: "center", gap: 8 }}
        >
          Approvals
          <span
            className="approvals-badge"
            style={{
              minWidth: 19,
              height: 19,
              padding: "0 5px",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              background: approvalCount === 0 ? "var(--grn)" : "var(--red)",
              color: "#fff",
              fontSize: 11,
              fontWeight: 600,
              borderRadius: 10,
              fontFamily: "var(--mono)",
            }}
          >
            {approvalCount}
          </span>
        </button>
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
                className={`list-btn ${workspace === "all" ? "active" : ""}`}
                onClick={() => setWorkspace("all")}
                style={{ display: "flex", alignItems: "center", gap: 11 }}
              >
                <span style={{ fontWeight: 500, flex: 1 }}>All workspaces</span>
                <span className="count-pill">{flatTickets.length}</span>
              </button>
              {workspaces.data?.map((w) => (
                <button
                  key={w.id}
                  className={`list-btn ${workspace === w.slug ? "active" : ""}`}
                  onClick={() => setWorkspace(w.slug)}
                  style={{ display: "flex", flexDirection: "column", alignItems: "stretch", gap: 4 }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
                    <span style={{ fontWeight: 500, flex: 1 }}>{w.name}</span>
                    {w.blocked_count > 0 && (
                      <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--red)" }} />
                    )}
                    <span className="count-pill">{w.ticket_count}</span>
                  </div>
                  {w.workflow_template_slug && (
                    <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--txl)", paddingLeft: 2 }}>
                      {w.workflow_template_slug}
                      {!w.repo_exists && " · repo missing"}
                    </span>
                  )}
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
                <div className="pane-header tickets-pane-header">
                  <div className="tickets-pane-toolbar">
                    <span className="pane-title">Work items</span>
                    <span className="count-pill">{flatTickets.length}</span>
                    <div className="tree-toolbar-actions">
                      <button
                        className="btn-secondary btn-compact"
                        type="button"
                        title={
                          defaultCreateWorkspaceSlug
                            ? "Create a new work item"
                            : "Load workspaces before creating work items"
                        }
                        disabled={!defaultCreateWorkspaceSlug}
                        onClick={openCreateWorkItem}
                      >
                        + New
                      </button>
                      <button
                        className="btn-secondary btn-compact"
                        type="button"
                        title={
                          defaultCreateWorkspaceSlug
                            ? "Import work items from .md, .json, or .yaml files"
                            : "Load workspaces before importing work items"
                        }
                        disabled={!defaultCreateWorkspaceSlug || previewTicketImport.isPending}
                        onClick={openImportTickets}
                      >
                        Import
                      </button>
                      <button
                        className="btn-secondary btn-compact"
                        type="button"
                        title="Expand all branches"
                        onClick={() => expandAll(collectExpandableIds(ticketTree.data ?? []))}
                      >
                        Expand all
                      </button>
                      <button
                        className="btn-secondary btn-compact"
                        type="button"
                        title="Collapse all branches"
                        onClick={() => collapseAll()}
                      >
                        Collapse all
                      </button>
                      <PaneHideButton
                        pane="tickets"
                        onHide={() => hidePane("tickets")}
                        disabled={visiblePaneCount <= 1}
                      />
                    </div>
                  </div>
              <input
                className="ticket-search"
                placeholder="Search title or id…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              <div className="filter-row type-filters">
                <button
                  className="btn-secondary btn-compact"
                  style={{
                    borderColor: typeFilters.length === 0 ? "var(--ac)" : undefined,
                    color: typeFilters.length === 0 ? "var(--ac2)" : undefined,
                  }}
                  type="button"
                  onClick={() => clearTypeFilters()}
                >
                  All types
                </button>
                {TYPE_FILTERS.map((f) => (
                  <button
                    key={f.id}
                    className="btn-secondary btn-compact"
                    style={{
                      borderColor: typeFilters.includes(f.id) ? "var(--ac)" : undefined,
                      color: typeFilters.includes(f.id) ? "var(--ac2)" : undefined,
                    }}
                    type="button"
                    onClick={() => toggleTypeFilter(f.id)}
                  >
                    {f.label}
                  </button>
                ))}
              </div>
              <div className="state-filters">
                {STATE_FILTER_OPTIONS.map((f) => {
                  const active =
                    f === "all" ? stateFilters.length === 0 : stateFilters.includes(f);
                  return (
                  <button
                    key={f}
                    className="btn-secondary btn-compact"
                    style={{
                      borderColor: active ? "var(--ac)" : undefined,
                      color: active ? "var(--ac2)" : undefined,
                    }}
                    type="button"
                    onClick={() => {
                      if (f === "all") {
                        clearStateFilters();
                      } else {
                        toggleStateFilter(f);
                      }
                    }}
                  >
                    {f === "all" ? "All" : STATE_LABELS[f]}{" "}
                    <span className="filter-count">{counts[f]}</span>
                  </button>
                  );
                })}
              </div>
            </div>
                <div className="scroll-list">
                  {ticketTree.data?.length ? (
                    <TicketTree
                      nodes={ticketTree.data}
                      selectedId={selectedId}
                      expandedIds={expandedSet}
                      onSelect={setSelectedTicketId}
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
            <span className="pane-title">Workflow</span>
            {sel && (
              <span className="count-pill" style={{ maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {sel.external_id}
              </span>
            )}
            <div style={{ flex: 1 }} />
            {sel && (
              <DashboardTicketDetailsButton ticketId={sel.id} ticket={sel} />
            )}
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
                  <PrioBars priority={sel.priority} />
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
                  </div>
                </div>
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
                      onClick={() => setTab("errors")}
                    >
                      View Errors
                    </button>
                  </div>
                )}

                <div style={{ marginTop: 24 }}>
                  <div className="state-label" style={{ marginBottom: 16 }}>
                    Workflow lifecycle
                  </div>
                  {sel.stages.map((s) => {
                    const runCheck = canRunStage(sel, s);
                    const isRunningThis = isStageRunning(s.key);
                    return (
                    <div key={s.key} className="stage-row" style={{ paddingBottom: 18 }}>
                      <div
                        className={`stage-dot ${s.status}`}
                        style={{ marginTop: 3 }}
                      />
                      <div style={{ flex: 1 }}>
                        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                          <span style={{ fontWeight: s.status === "running" ? 600 : 400 }}>{s.name}</span>
                          {s.optional && (
                            <span className="count-pill" style={{ fontSize: 9 }}>
                              optional
                            </span>
                          )}
                          <button
                            type="button"
                            className="btn-secondary btn-compact stage-run-btn"
                            disabled={!runCheck.allowed || workflowBusy || startRun.isPending}
                            title={runCheck.reason}
                            onClick={() => requestStageRun(s.key)}
                          >
                            {stageRunButtonLabel(s, isRunningThis)}
                          </button>
                          {isAgentWorkflowTicket(sel) && isAgentStage(s) && (
                            <CopyTerminalCommandButton
                              className="btn-secondary btn-compact stage-run-btn"
                              command={buildStageRunTerminalCommand(sel, s, API_BASE)}
                              title={`Copy terminal command to run ${s.name}`}
                            />
                          )}
                          <span
                            className="count-pill"
                            style={{
                              marginLeft: "auto",
                              color:
                                s.status === "running"
                                  ? "var(--bll)"
                                  : s.status === "done"
                                    ? "var(--grl)"
                                    : "var(--txl)",
                            }}
                          >
                            {s.status}
                          </span>
                        </div>
                        {stageKindLabel(s) ? (
                          <div style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--txm)", marginTop: 5 }}>
                            {stageKindLabel(s)}
                          </div>
                        ) : null}
                        {stageAgentSubtitle(s) && (
                          <div style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--txm)", marginTop: 5 }}>
                            {stageAgentSubtitle(s)}
                          </div>
                        )}
                        {s.note && (
                          <div
                            style={{
                              marginTop: 7,
                              fontSize: 11.5,
                              padding: "8px 10px",
                              background: "var(--bg2)",
                              border: "1px solid var(--bd)",
                              borderRadius: 8,
                            }}
                          >
                            {s.note}
                          </div>
                        )}
                      </div>
                    </div>
                    );
                  })}
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
                {isAgentWorkflowTicket(sel) && (
                  <CopyTerminalCommandButton
                    command={buildOrchestrateTerminalCommand(sel, API_BASE)}
                    title="Copy terminal command to orchestrate this ticket"
                  />
                )}
                {(() => {
                  const cursorStage = sel.stages.find((s) => s.key === sel.workflow_stage_key);
                  const cursorRun = cursorStage ? canRunStage(sel, cursorStage) : { allowed: false, reason: "No cursor stage" };
                  const runningCursor = isStageRunning(sel.workflow_stage_key);
                  return (
                <button
                  className="btn-secondary"
                  disabled={!selectedId || workflowBusy || startRun.isPending || !cursorRun.allowed}
                  onClick={() => requestStageRun(sel.workflow_stage_key)}
                  title={cursorRun.reason}
                >
                  {currentStageRunLabel(cursorStage, runningCursor)}
                </button>
                  );
                })()}
                <button
                  className="btn-secondary"
                  disabled={!selectedId || advance.isPending}
                  onClick={() => advance.mutate()}
                >
                  Advance stage
                </button>
                <div style={{ flex: 1 }} />
                <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--txl)" }}>
                  {sel.run_code || "—"}
                </span>
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
            {(["diff", "errors", "triage", "logs", "tests", "context", "pr"] as const).map((t) => (
              <button
                key={t}
                className={`tab-btn ${tab === t ? "active" : ""}`}
                onClick={() => setTab(t)}
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
            <div style={{ flex: 1 }} />
            <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--txl)" }}>
              {tab === "triage" ? "operator channel · ticket context" : "truth layer · execution output only"}
            </span>
            <PaneHideButton
              pane="artifacts"
              onHide={() => hidePane("artifacts")}
              disabled={visiblePaneCount <= 1}
            />
          </div>
          <div style={{ flex: 1, overflow: "auto", minHeight: 0 }}>
            {tab === "triage" ? (
              <TriagePanel
                ticket={sel}
                runtimeOptions={runtimeOptions.data}
                onResolved={() => {
                  qc.invalidateQueries({ queryKey: ["triage", selectedId] });
                  qc.invalidateQueries({ queryKey: ["ticket", selectedId] });
                  qc.invalidateQueries({ queryKey: ["runs", selectedId] });
                }}
              />
            ) : tab === "logs" && sel ? (
              <LogsPanel
                ticket={sel}
                runtimeOptions={runtimeOptions.data}
                onResolved={() => {
                  qc.invalidateQueries({ queryKey: ["triage", selectedId] });
                  qc.invalidateQueries({ queryKey: ["ticket", selectedId] });
                  qc.invalidateQueries({ queryKey: ["runs", selectedId] });
                }}
              />
            ) : (
              <ArtifactView tab={tab} ticket={sel} runs={ticketRuns.data ?? []} />
            )}
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

      <SettingsModal
        open={settingsOpen}
        workspaceSlug={settingsWorkspaceSlug}
        workspaces={workspaces.data ?? []}
        runtimeOptions={runtimeOptions.data}
        isSaving={setRuntime.isPending}
        onClose={() => setSettingsOpen(false)}
        onWorkspaceChange={setSettingsWorkspaceSlug}
        onSave={async (slug, runtime) => {
          await setRuntime.mutateAsync({ slug, runtime });
        }}
      />

      <MemorySetupModal
        open={memoryOpen}
        data={memoryConfig.data}
        isLoading={memoryConfig.isLoading}
        isSaving={setMemoryConfig.isPending}
        errorMessage={
          setMemoryConfig.error
            ? (() => {
                try {
                  const parsed = JSON.parse(setMemoryConfig.error.message) as { detail?: string };
                  return parsed.detail ?? setMemoryConfig.error.message;
                } catch {
                  return setMemoryConfig.error.message;
                }
              })()
            : undefined
        }
        onClose={() => {
          if (setMemoryConfig.isPending) return;
          setMemoryConfig.reset();
          setMemoryOpen(false);
        }}
        onRefresh={() => void memoryConfig.refetch()}
        onSave={async (config) => {
          await setMemoryConfig.mutateAsync(config);
        }}
      />

      <UsageModal
        open={usageOpen}
        snapshot={usage.data}
        isLoading={usage.isFetching}
        error={usage.error}
        onClose={() => setUsageOpen(false)}
        onRefresh={() => void usage.refetch()}
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

      {inboxOpen && (
        <>
          <div className="inbox-overlay" onClick={() => setInboxOpen(false)} />
          <aside className="inbox-panel">
            <div style={{ padding: "18px 20px", borderBottom: "1px solid var(--bd)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span className="pane-title">Global Approval Inbox</span>
                <span className="count-pill">{approvals.data?.length ?? 0}</span>
                <div style={{ flex: 1 }} />
                <button className="btn-secondary" onClick={() => setInboxOpen(false)}>
                  ✕
                </button>
              </div>
            </div>
            <div style={{ flex: 1, overflowY: "auto", padding: 16 }}>
              {resolveApproval.isError && (
                <div
                  style={{
                    fontSize: 11.5,
                    color: "var(--rdl)",
                    marginBottom: 12,
                    padding: "8px 10px",
                    borderRadius: 8,
                    background: "rgba(240,96,63,.08)",
                    border: "1px solid rgba(240,96,63,.25)",
                  }}
                >
                  {formatApprovalResolveError(resolveApproval.error)}
                </div>
              )}
              {approvals.data?.map((a) => (
                <ApprovalCard
                  key={a.id}
                  approval={a}
                  onApprove={(payload) =>
                    resolveApproval.mutate({ id: a.id, action: "approve", ...payload })
                  }
                  onReject={() => resolveApproval.mutate({ id: a.id, action: "reject" })}
                  onInspect={() => {
                    setSelectedTicketId(a.ticket_id);
                    setInboxOpen(false);
                    setTab("diff");
                  }}
                  isSubmitting={resolveApproval.isPending && resolveApproval.variables?.id === a.id}
                />
              ))}
              {!approvals.data?.length && (
                <div style={{ textAlign: "center", color: "var(--txm)", padding: 40 }}>
                  Inbox zero — nothing needs your attention
                </div>
              )}
            </div>
          </aside>
        </>
      )}
    </div>
  );
}

function ArtifactView({
  tab,
  ticket,
  runs = [],
}: {
  tab: string;
  ticket?: TicketDetail;
  runs?: {
    id: string;
    run_code: string;
    status: string;
    command: string;
    agent_id?: string;
    stage_key?: string;
    stderr?: string;
  }[];
}) {
  if (!ticket) {
    return <div style={{ padding: 40, color: "var(--txl)", textAlign: "center" }}>No ticket selected</div>;
  }
  const art = ticket.artifacts ?? {};

  if (tab === "diff") {
    const diff = art.diff;
    if (!diff) {
      return (
        <EmptyArtifacts label="No diff captured yet">
          Shows git changes in the workspace repo (vs main) after agent runs, or when you open this tab.
        </EmptyArtifacts>
      );
    }
    return (
      <div>
        <div className="diff-summary-bar">
          <span>{diff.files}</span>
          {diff.range && (
            <span style={{ marginLeft: 12, opacity: 0.85 }}>vs {diff.range}</span>
          )}
          <span style={{ marginLeft: 12, color: "var(--grl)" }}>{diff.add}</span>
          <span style={{ marginLeft: 8, color: "var(--rdl)" }}>{diff.del}</span>
        </div>
        {diffFileSections(diff).map((section) => (
          <section key={section.path} className="diff-file-block">
            <div className="diff-file-header">
              <span className="diff-file-path" title={section.path}>
                {section.path}
              </span>
              <span className="diff-file-stats">
                <span style={{ color: "var(--grl)" }}>+{section.add}</span>
                <span style={{ color: "var(--rdl)" }}>−{section.del}</span>
              </span>
            </div>
            <div style={{ padding: "4px 0 8px" }}>
              {section.lines.map((line, i) => (
                <DiffLineView key={`${section.path}-${i}`} line={line} />
              ))}
            </div>
          </section>
        ))}
      </div>
    );
  }

  if (tab === "errors") {
    const errorArt = art.error;
    const failedRuns = runs.filter((r) => r.status === "failed");
    const hasContent = Boolean(ticket.blocking_issues || errorArt || failedRuns.length);
    if (!hasContent) return <EmptyArtifacts label="No errors recorded" />;

    return (
      <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 14 }}>
        {(ticket.blocking_issues || errorArt?.message) && (
          <div
            className="state-card"
            style={{
              borderColor: "rgba(240,96,63,.35)",
              background: "rgba(240,96,63,.08)",
            }}
          >
            <div className="state-label" style={{ color: "var(--rdl)" }}>
              Blocking issue
            </div>
            <pre
              style={{
                margin: "8px 0 0",
                fontFamily: "var(--mono)",
                fontSize: 12,
                lineHeight: 1.6,
                whiteSpace: "pre-wrap",
                color: "var(--tx)",
              }}
            >
              {errorArt?.message || ticket.blocking_issues}
            </pre>
          </div>
        )}
        {errorArt && (
          <div className="state-card">
            <div className="state-label">Failed run</div>
            <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--txm)", marginTop: 6 }}>
              {errorArt.run_code} · {errorArt.agent_id} · {errorArt.stage_key}
            </div>
            {errorArt.command && (
              <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--txl)", marginTop: 8, wordBreak: "break-all" }}>
                {errorArt.command}
              </div>
            )}
          </div>
        )}
        {failedRuns.map((run) => (
          <div key={run.id} className="state-card">
            <div className="state-label">{run.run_code}</div>
            <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--txm)", marginTop: 6 }}>
              {run.agent_id ?? "—"} · {run.stage_key ?? "—"}
            </div>
            {run.stderr && (
              <pre
                style={{
                  margin: "8px 0 0",
                  fontFamily: "var(--mono)",
                  fontSize: 11,
                  lineHeight: 1.55,
                  whiteSpace: "pre-wrap",
                  color: "var(--rdl)",
                }}
              >
                {run.stderr}
              </pre>
            )}
          </div>
        ))}
      </div>
    );
  }

  if (tab === "tests") {
    const tests = art.tests;
    if (!tests) {
      return (
        <EmptyArtifacts label="No test results yet">
          Populated when a testing stage run completes (static_qa, test_breaker, test_designer) with pytest-style output.
        </EmptyArtifacts>
      );
    }
    return (
      <div style={{ padding: 16 }}>
        <div className="state-card" style={{ marginBottom: 14 }}>
          <strong>{tests.summary}</strong>
          {tests.cmd && (
            <div
              style={{
                marginTop: 8,
                fontFamily: "var(--mono)",
                fontSize: 11,
                color: "var(--txl)",
                wordBreak: "break-all",
                lineHeight: 1.5,
              }}
            >
              {tests.cmd}
            </div>
          )}
        </div>
        {tests.rows?.map((row, i) => (
          <div key={i} className="list-btn" style={{ marginBottom: 4 }}>
            <span style={{ color: row.status === "pass" ? "var(--grl)" : "var(--rdl)" }}>{row.status}</span>{" "}
            <span style={{ fontFamily: "var(--mono)" }}>{row.name}</span>
            {row.msg && <div style={{ color: "var(--rdl)", fontSize: 11, marginTop: 4 }}>{row.msg}</div>}
          </div>
        ))}
      </div>
    );
  }

  if (tab === "pr") {
    const pr = art.pr;
    if (!pr) {
      return (
        <EmptyArtifacts label="No pull request opened">
          Open a PR from the approval step when human sign-off is required.
        </EmptyArtifacts>
      );
    }
    return (
      <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 14 }}>
        <div className="state-card">
          <div className="state-label">Pull request</div>
          <div style={{ fontWeight: 600, marginTop: 8 }}>{pr.title}</div>
          {pr.number && (
            <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--txm)", marginTop: 6 }}>
              #{pr.number} · {pr.branch}
            </div>
          )}
          <a
            href={pr.url}
            target="_blank"
            rel="noreferrer"
            style={{ display: "inline-block", marginTop: 12, color: "var(--ac2)", fontSize: 13 }}
          >
            {pr.url}
          </a>
        </div>
        {pr.body && (
          <pre
            style={{
              margin: 0,
              padding: 12,
              borderRadius: 10,
              border: "1px solid var(--bd)",
              background: "var(--bg2)",
              fontFamily: "var(--mono)",
              fontSize: 11,
              lineHeight: 1.55,
              whiteSpace: "pre-wrap",
            }}
          >
            {pr.body}
          </pre>
        )}
      </div>
    );
  }

  if (tab !== "context") {
    return <EmptyArtifacts />;
  }

  const sections = art.context ?? [];
  const runRows = runs;
  if (!sections.length && !runRows.length) return <EmptyArtifacts />;
  return (
    <div style={{ padding: 16 }}>
      {runRows.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div className="state-label">Agent runs</div>
          {runRows.map((r) => (
            <div key={r.id} className="list-btn" style={{ marginBottom: 4, fontFamily: "var(--mono)", fontSize: 11 }}>
              {r.run_code} · {r.status} · {r.command.slice(0, 60)}
            </div>
          ))}
        </div>
      )}
      {sections.map((sec, i) => (
        <div key={i} style={{ marginBottom: 16 }}>
          <div className="state-label">{sec.title}</div>
          {sec.rows?.map((r, j) => (
            <div key={j} style={{ display: "flex", gap: 12, padding: "9px 0", borderBottom: "1px solid var(--bd)" }}>
              <span style={{ width: 130, color: "var(--txm)" }}>{r.k}</span>
              <span style={{ fontFamily: "var(--mono)", flex: 1 }}>{r.v}</span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

function EmptyArtifacts({
  label = "No artifacts yet",
  children,
}: {
  label?: string;
  children?: ReactNode;
}) {
  return (
    <div style={{ height: "100%", minHeight: 340, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", color: "var(--txl)", gap: 12, padding: 24 }}>
      <div style={{ fontFamily: "var(--dp)", fontSize: 14, color: "var(--txm)" }}>{label}</div>
      {children ? (
        <div style={{ fontSize: 12.5, textAlign: "center", maxWidth: 320, lineHeight: 1.55 }}>{children}</div>
      ) : (
        label === "No artifacts yet" && (
          <div style={{ fontSize: 12.5, textAlign: "center", maxWidth: 280 }}>Artifacts appear when agent runs complete</div>
        )
      )}
    </div>
  );
}
