import { create } from "zustand";
import { persist } from "zustand/middleware";

import type { TicketState, WorkItemType } from "../api/client";
import {
  clampHiveSpeedIndex,
  DEFAULT_HIVE_SPEED_MULTIPLIER,
  hiveSpeedIndexFor,
} from "../lib/hive/speed";
import { DEFAULT_HIVE_SKIN, normalizeHiveSkinId, resolveHiveSkinId, type HiveSkinId } from "../lib/hive/skins";
import { navigateToPage } from "../lib/useAppNavigation";

export type PaneId = "workspaces" | "tickets" | "workflow" | "artifacts";

export type PaneVisibility = Record<PaneId, boolean>;

export const DEFAULT_COPILOT_HEIGHT = 340;
const MIN_COPILOT_HEIGHT = 180;
const MAX_COPILOT_HEIGHT = 720;

/** Keep a restored or dragged height usable, whatever is in storage. */
export function clampCopilotHeight(value: unknown): number {
  const height = typeof value === "number" && Number.isFinite(value) ? value : DEFAULT_COPILOT_HEIGHT;
  return Math.min(MAX_COPILOT_HEIGHT, Math.max(MIN_COPILOT_HEIGHT, height));
}

interface UiState {
  stateFilters: TicketState[];
  typeFilters: WorkItemType[];
  search: string;
  expandedTicketIds: string[];
  workspace: string;
  inboxOpen: boolean;
  paneVisibility: PaneVisibility;
  editorWorkspace: string;
  editorContextRoot: string;
  editorFilePath: string | null;
  queueWorkspaceSlug: string;
  branchTriageWorkspaceSlug: string;
  hiveSkin: HiveSkinId;
  hiveSpeedIndex: number;
  copilotOpen: boolean;
  copilotHeight: number;
  /**
   * Which branch the branch-triage screen is reviewing. Lifted out of that
   * page so the dock can bind to its conversation — the dock mounts above the
   * routes and cannot see a page's local state.
   */
  branchTriageBranch: string;
  toggleStateFilter: (state: TicketState) => void;
  clearStateFilters: () => void;
  toggleTypeFilter: (type: WorkItemType) => void;
  clearTypeFilters: () => void;
  setSearch: (s: string) => void;
  toggleExpanded: (id: string) => void;
  expandAll: (ids: string[]) => void;
  collapseAll: () => void;
  expandPath: (ids: string[]) => void;
  setWorkspace: (slug: string) => void;
  setInboxOpen: (open: boolean) => void;
  setPaneVisible: (pane: PaneId, visible: boolean) => void;
  togglePane: (pane: PaneId) => void;
  setEditorWorkspace: (slug: string) => void;
  setEditorContextRoot: (root: string) => void;
  setEditorFilePath: (path: string | null) => void;
  setQueueWorkspaceSlug: (slug: string) => void;
  setBranchTriageWorkspaceSlug: (slug: string) => void;
  setBranchTriageBranch: (branch: string) => void;
  setCopilotOpen: (open: boolean) => void;
  toggleCopilot: () => void;
  setCopilotHeight: (height: number) => void;
  setHiveSkin: (skin: HiveSkinId | string) => void;
  setHiveSpeedIndex: (index: number) => void;
  stepHiveSpeed: (delta: -1 | 1) => void;
  openEditorFile: (workspaceSlug: string, filePath: string, contextRoot?: string) => void;
}

type PersistedUiState = Pick<
  UiState,
  | "expandedTicketIds"
  | "workspace"
  | "typeFilters"
  | "stateFilters"
  | "paneVisibility"
  | "editorWorkspace"
  | "editorContextRoot"
  | "queueWorkspaceSlug"
  | "branchTriageWorkspaceSlug"
  | "hiveSkin"
  | "hiveSpeedIndex"
  | "copilotOpen"
  | "copilotHeight"
>;

export const useUiStore = create<UiState>()(
  persist(
    (set, get) => ({
      stateFilters: [],
      typeFilters: [],
      search: "",
      expandedTicketIds: [],
      workspace: "all",
      inboxOpen: false,
      paneVisibility: {
        workspaces: true,
        tickets: true,
        workflow: true,
        artifacts: true,
      },
      editorWorkspace: "",
      editorContextRoot: ".",
      editorFilePath: null,
      queueWorkspaceSlug: "",
      branchTriageWorkspaceSlug: "",
      hiveSkin: DEFAULT_HIVE_SKIN,
      hiveSpeedIndex: hiveSpeedIndexFor(DEFAULT_HIVE_SPEED_MULTIPLIER),
      copilotOpen: false,
      copilotHeight: DEFAULT_COPILOT_HEIGHT,
      // Not persisted: which branch is under review is a property of this
      // visit, and restoring a stale one would bind the dock to a
      // conversation the screen is not showing.
      branchTriageBranch: "",
      toggleStateFilter: (state) => {
        const current = get().stateFilters;
        set({
          stateFilters: current.includes(state)
            ? current.filter((value) => value !== state)
            : [...current, state],
        });
      },
      clearStateFilters: () => set({ stateFilters: [] }),
      toggleTypeFilter: (type) => {
        const current = get().typeFilters;
        set({
          typeFilters: current.includes(type)
            ? current.filter((value) => value !== type)
            : [...current, type],
        });
      },
      clearTypeFilters: () => set({ typeFilters: [] }),
      setSearch: (search) => set({ search }),
      toggleExpanded: (id) => {
        const cur = new Set(get().expandedTicketIds);
        if (cur.has(id)) cur.delete(id);
        else cur.add(id);
        set({ expandedTicketIds: [...cur] });
      },
      expandAll: (ids) => set({ expandedTicketIds: ids }),
      collapseAll: () => set({ expandedTicketIds: [] }),
      expandPath: (ids) => {
        const cur = new Set(get().expandedTicketIds);
        for (const id of ids) cur.add(id);
        set({ expandedTicketIds: [...cur] });
      },
      setWorkspace: (workspace) => set({ workspace }),
      setInboxOpen: (inboxOpen) => set({ inboxOpen }),
      setPaneVisible: (pane, visible) =>
        set((state) => {
          if (!visible) {
            const visibleCount = Object.values(state.paneVisibility).filter(Boolean).length;
            if (visibleCount <= 1 && state.paneVisibility[pane]) {
              return state;
            }
          }
          return {
            paneVisibility: { ...state.paneVisibility, [pane]: visible },
          };
        }),
      togglePane: (pane) => {
        const { paneVisibility, setPaneVisible } = get();
        setPaneVisible(pane, !paneVisibility[pane]);
      },
      setEditorWorkspace: (editorWorkspace) => set({ editorWorkspace }),
      setEditorContextRoot: (editorContextRoot) => set({ editorContextRoot }),
      setEditorFilePath: (editorFilePath) => set({ editorFilePath }),
      setQueueWorkspaceSlug: (queueWorkspaceSlug) => set({ queueWorkspaceSlug }),
      setBranchTriageWorkspaceSlug: (branchTriageWorkspaceSlug) =>
        set({ branchTriageWorkspaceSlug }),
      setBranchTriageBranch: (branchTriageBranch) => set({ branchTriageBranch }),
      setCopilotOpen: (copilotOpen) => set({ copilotOpen }),
      toggleCopilot: () => set({ copilotOpen: !get().copilotOpen }),
      setCopilotHeight: (height) =>
        set({ copilotHeight: clampCopilotHeight(height) }),
      setHiveSkin: (hiveSkin) => set({ hiveSkin: resolveHiveSkinId(hiveSkin) }),
      setHiveSpeedIndex: (hiveSpeedIndex) =>
        set({ hiveSpeedIndex: clampHiveSpeedIndex(hiveSpeedIndex) }),
      stepHiveSpeed: (delta) =>
        set((state) => ({
          hiveSpeedIndex: clampHiveSpeedIndex(state.hiveSpeedIndex + delta),
        })),
      openEditorFile: (workspaceSlug, filePath, contextRoot = ".") => {
        set({
          editorWorkspace: workspaceSlug,
          editorContextRoot: contextRoot,
          editorFilePath: filePath,
        });
        navigateToPage("editor");
      },
    }),
    {
      name: "loregarden-ui",
      version: 8,
      migrate: (persistedState, version) => {
        const state = { ...(persistedState as Record<string, unknown>) };
        if (version < 8) {
          if (typeof state.copilotOpen !== "boolean") state.copilotOpen = false;
          state.copilotHeight = clampCopilotHeight(state.copilotHeight);
        }
        if (version < 7 && typeof state.branchTriageWorkspaceSlug !== "string") {
          state.branchTriageWorkspaceSlug = "";
        }
        if (version < 1) {
          const legacyTypeFilter = state.typeFilter;
          if (typeof legacyTypeFilter === "string" && legacyTypeFilter !== "all") {
            state.typeFilters = [legacyTypeFilter];
          } else if (!Array.isArray(state.typeFilters)) {
            state.typeFilters = [];
          }
          delete state.typeFilter;

          const legacyFilter = state.filter;
          if (typeof legacyFilter === "string" && legacyFilter !== "all") {
            state.stateFilters = [legacyFilter];
          } else if (!Array.isArray(state.stateFilters)) {
            state.stateFilters = [];
          }
          delete state.filter;
        }
        if (version < 6) {
          const skin = state.hiveSkin;
          state.hiveSkin =
            typeof skin === "string" ? resolveHiveSkinId(skin) : DEFAULT_HIVE_SKIN;
        }
        if (version < 5) {
          const skin = state.hiveSkin;
          state.hiveSkin =
            typeof skin === "string" ? (normalizeHiveSkinId(skin) ?? DEFAULT_HIVE_SKIN) : DEFAULT_HIVE_SKIN;
        }
        if (version < 4) {
          const skin = state.hiveSkin;
          state.hiveSkin =
            typeof skin === "string" ? (normalizeHiveSkinId(skin) ?? DEFAULT_HIVE_SKIN) : DEFAULT_HIVE_SKIN;
        }
        if (version < 3) {
          state.hiveSpeedIndex = hiveSpeedIndexFor(DEFAULT_HIVE_SPEED_MULTIPLIER);
        }
        return state as PersistedUiState;
      },
      onRehydrateStorage: () => (state) => {
        if (!state) return;
        state.hiveSkin = resolveHiveSkinId(state.hiveSkin);
      },
      partialize: (s) => ({
        expandedTicketIds: s.expandedTicketIds,
        workspace: s.workspace,
        typeFilters: s.typeFilters,
        stateFilters: s.stateFilters,
        paneVisibility: s.paneVisibility,
        editorWorkspace: s.editorWorkspace,
        editorContextRoot: s.editorContextRoot,
        queueWorkspaceSlug: s.queueWorkspaceSlug,
        branchTriageWorkspaceSlug: s.branchTriageWorkspaceSlug,
        copilotOpen: s.copilotOpen,
        copilotHeight: s.copilotHeight,
        hiveSkin: s.hiveSkin,
        hiveSpeedIndex: s.hiveSpeedIndex,
      }),
    },
  ),
);
