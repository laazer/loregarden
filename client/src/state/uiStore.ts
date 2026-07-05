import { create } from "zustand";
import { persist } from "zustand/middleware";

import type { TicketState, WorkItemType } from "../api/client";

type Tab = "diff" | "logs" | "tests" | "context" | "errors" | "triage";
type AppPage = "dashboard" | "studio";

export type PaneId = "workspaces" | "tickets" | "workflow" | "artifacts";

export type PaneVisibility = Record<PaneId, boolean>;

interface UiState {
  selectedTicketId: string | null;
  filter: TicketState | "all";
  typeFilter: WorkItemType | "all";
  search: string;
  expandedTicketIds: string[];
  workspace: string;
  tab: Tab;
  appPage: AppPage;
  inboxOpen: boolean;
  paneVisibility: PaneVisibility;
  setSelectedTicketId: (id: string | null) => void;
  setFilter: (f: TicketState | "all") => void;
  setTypeFilter: (t: WorkItemType | "all") => void;
  setSearch: (s: string) => void;
  toggleExpanded: (id: string) => void;
  expandAll: (ids: string[]) => void;
  collapseAll: () => void;
  expandPath: (ids: string[]) => void;
  setWorkspace: (slug: string) => void;
  setTab: (tab: Tab) => void;
  setAppPage: (page: AppPage) => void;
  setInboxOpen: (open: boolean) => void;
  setPaneVisible: (pane: PaneId, visible: boolean) => void;
  togglePane: (pane: PaneId) => void;
}

export const useUiStore = create<UiState>()(
  persist(
    (set, get) => ({
      selectedTicketId: null,
      filter: "all",
      typeFilter: "all",
      search: "",
      expandedTicketIds: [],
      workspace: "all",
      tab: "diff",
      appPage: "dashboard",
      inboxOpen: false,
      paneVisibility: {
        workspaces: true,
        tickets: true,
        workflow: true,
        artifacts: true,
      },
      setSelectedTicketId: (id) => set({ selectedTicketId: id }),
      setFilter: (filter) => set({ filter }),
      setTypeFilter: (typeFilter) => set({ typeFilter }),
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
      setTab: (tab) => set({ tab }),
      setAppPage: (appPage) => set({ appPage }),
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
    }),
    {
      name: "loregarden-ui",
      partialize: (s) => ({
        expandedTicketIds: s.expandedTicketIds,
        workspace: s.workspace,
        typeFilter: s.typeFilter,
        paneVisibility: s.paneVisibility,
      }),
    },
  ),
);
