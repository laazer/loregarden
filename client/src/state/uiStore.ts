import { create } from "zustand";
import { persist } from "zustand/middleware";

import type { TicketState, WorkItemType } from "../api/client";

type Tab = "diff" | "logs" | "tests" | "context";

interface UiState {
  selectedTicketId: string | null;
  filter: TicketState | "all";
  typeFilter: WorkItemType | "all";
  cycleFilter: string | "all";
  search: string;
  expandedTicketIds: string[];
  workspace: string;
  tab: Tab;
  inboxOpen: boolean;
  setSelectedTicketId: (id: string | null) => void;
  setFilter: (f: TicketState | "all") => void;
  setTypeFilter: (t: WorkItemType | "all") => void;
  setCycleFilter: (id: string | "all") => void;
  setSearch: (s: string) => void;
  toggleExpanded: (id: string) => void;
  expandAll: (ids: string[]) => void;
  collapseAll: () => void;
  expandPath: (ids: string[]) => void;
  setWorkspace: (slug: string) => void;
  setTab: (tab: Tab) => void;
  setInboxOpen: (open: boolean) => void;
}

export const useUiStore = create<UiState>()(
  persist(
    (set, get) => ({
      selectedTicketId: null,
      filter: "all",
      typeFilter: "all",
      cycleFilter: "all",
      search: "",
      expandedTicketIds: [],
      workspace: "all",
      tab: "diff",
      inboxOpen: false,
      setSelectedTicketId: (id) => set({ selectedTicketId: id }),
      setFilter: (filter) => set({ filter }),
      setTypeFilter: (typeFilter) => set({ typeFilter }),
      setCycleFilter: (cycleFilter) => set({ cycleFilter }),
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
      setInboxOpen: (inboxOpen) => set({ inboxOpen }),
    }),
    {
      name: "loregarden-ui",
      partialize: (s) => ({
        expandedTicketIds: s.expandedTicketIds,
        workspace: s.workspace,
        typeFilter: s.typeFilter,
        cycleFilter: s.cycleFilter,
      }),
    },
  ),
);
