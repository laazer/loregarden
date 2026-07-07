import { create } from "zustand";
import { persist } from "zustand/middleware";

import type { TicketState, WorkItemType } from "../api/client";
import { navigateToPage } from "../lib/useAppNavigation";

export type PaneId = "workspaces" | "tickets" | "workflow" | "artifacts";

export type PaneVisibility = Record<PaneId, boolean>;

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
  openEditorFile: (workspaceSlug: string, filePath: string, contextRoot?: string) => void;
}

type PersistedUiState = Pick<
  UiState,
  "expandedTicketIds" | "workspace" | "typeFilters" | "stateFilters" | "paneVisibility" | "editorWorkspace" | "editorContextRoot" | "queueWorkspaceSlug"
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
      version: 1,
      migrate: (persistedState, version) => {
        const state = { ...(persistedState as Record<string, unknown>) };
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
        return state as PersistedUiState;
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
      }),
    },
  ),
);
