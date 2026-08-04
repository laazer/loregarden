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

export type UtilityDockEdge = "bottom" | "right";

export const DEFAULT_COPILOT_HEIGHT = 340;
const MIN_COPILOT_HEIGHT = 180;
const MAX_COPILOT_HEIGHT = 720;

export const DEFAULT_COPILOT_WIDTH = 380;
const MIN_COPILOT_WIDTH = 280;
const MAX_COPILOT_WIDTH = 640;

/** Keep a restored or dragged height usable, whatever is in storage. */
export function clampCopilotHeight(value: unknown): number {
  const height = typeof value === "number" && Number.isFinite(value) ? value : DEFAULT_COPILOT_HEIGHT;
  return Math.min(MAX_COPILOT_HEIGHT, Math.max(MIN_COPILOT_HEIGHT, height));
}

/** Keep a restored right-dock width usable, whatever is in storage. */
export function clampCopilotWidth(value: unknown): number {
  const width = typeof value === "number" && Number.isFinite(value) ? value : DEFAULT_COPILOT_WIDTH;
  return Math.min(MAX_COPILOT_WIDTH, Math.max(MIN_COPILOT_WIDTH, width));
}

export function normalizeUtilityDockEdge(value: unknown): UtilityDockEdge {
  return value === "right" ? "right" : "bottom";
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
  /**
   * Which workspace Baxter chat is confined to.
   *
   * Separate from `workspace` (the Console/Home filter, which may be "all")
   * because a chat turn is answered by one workspace's agent and its ticket
   * refs only resolve there.
   */
  chatWorkspaceSlug: string;
  hiveSkin: HiveSkinId;
  hiveSpeedIndex: number;
  copilotOpen: boolean;
  copilotHeight: number;
  copilotWidth: number;
  /**
   * Where the status bar + Copilot unit sits relative to the screen.
   * Persisted so operators keep a preferred edge across reloads.
   */
  utilityDockEdge: UtilityDockEdge;
  /**
   * Whether the dock is also showing a shell.
   *
   * Off by default and remembered. Opening the first time spawns a real login
   * shell; closing only hides the panel — the process stays alive until the
   * screen names a different workspace (see CopilotDock).
   */
  terminalOpen: boolean;
  /**
   * Which branch the branch-triage screen is reviewing. Lifted out of that
   * page so the dock can bind to its conversation — the dock mounts above the
   * routes and cannot see a page's local state.
   */
  branchTriageBranch: string;
  /**
   * Bumped by the global topbar "New chat" action so BaxterChatPage can clear
   * its local thread without owning the chrome.
   */
  baxterChatResetNonce: number;
  /** Whether the Baxter chat history drawer is visible. */
  baxterHistoryOpen: boolean;
  /**
   * Whether the dock's rail is showing the chat archive instead of the openers.
   *
   * Separate from `baxterHistoryOpen`, which drives the chat page's drawer: the
   * two surfaces are never on screen together, and sharing the flag would open
   * a drawer on the page the dock was closed over.
   *
   * Not persisted. It is a momentary detour to pick a thread, and restoring it
   * would reopen the dock onto a list rather than the conversation.
   */
  copilotHistoryOpen: boolean;
  /**
   * The Home Baxter conversation currently on screen, or "" for a fresh one.
   *
   * Persisted: the thread itself lives on the server, and returning to Home
   * with no memory of which one was open would show an empty composer above a
   * conversation the operator was in the middle of.
   */
  baxterChatSessionId: string;
  /**
   * Per-run auto-follow-to-bottom choice for LogsPanel's running-lane views.
   *
   * Not persisted: run ids are meaningless across sessions/reloads of a
   * finished run, so persisting this map would leak stale, unbounded entries
   * (same rationale as branchTriageBranch above).
   */
  autoFollowByRunId: Record<string, boolean>;
  setAutoFollow: (runId: string, value: boolean) => void;
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
  setChatWorkspaceSlug: (slug: string) => void;
  setBranchTriageWorkspaceSlug: (slug: string) => void;
  setBranchTriageBranch: (branch: string) => void;
  requestBaxterChatReset: () => void;
  setBaxterChatSessionId: (id: string) => void;
  setBaxterHistoryOpen: (open: boolean) => void;
  toggleBaxterHistory: () => void;
  setCopilotHistoryOpen: (open: boolean) => void;
  toggleCopilotHistory: () => void;
  setCopilotOpen: (open: boolean) => void;
  setTerminalOpen: (open: boolean) => void;
  toggleCopilot: () => void;
  setCopilotHeight: (height: number) => void;
  setCopilotWidth: (width: number) => void;
  setUtilityDockEdge: (edge: UtilityDockEdge) => void;
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
  | "chatWorkspaceSlug"
  | "hiveSkin"
  | "hiveSpeedIndex"
  | "copilotOpen"
  | "copilotHeight"
  | "copilotWidth"
  | "utilityDockEdge"
  | "terminalOpen"
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
      chatWorkspaceSlug: "",
      hiveSkin: DEFAULT_HIVE_SKIN,
      hiveSpeedIndex: hiveSpeedIndexFor(DEFAULT_HIVE_SPEED_MULTIPLIER),
      copilotOpen: false,
      copilotHeight: DEFAULT_COPILOT_HEIGHT,
      copilotWidth: DEFAULT_COPILOT_WIDTH,
      utilityDockEdge: "bottom",
      terminalOpen: false,
      // Not persisted: which branch is under review is a property of this
      // visit, and restoring a stale one would bind the dock to a
      // conversation the screen is not showing.
      branchTriageBranch: "",
      baxterChatResetNonce: 0,
      baxterHistoryOpen: false,
      copilotHistoryOpen: false,
      baxterChatSessionId: "",
      autoFollowByRunId: {},
      setAutoFollow: (runId, value) =>
        set((state) => ({ autoFollowByRunId: { ...state.autoFollowByRunId, [runId]: value } })),
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
      setChatWorkspaceSlug: (chatWorkspaceSlug) => set({ chatWorkspaceSlug }),
      setBranchTriageWorkspaceSlug: (branchTriageWorkspaceSlug) =>
        set({ branchTriageWorkspaceSlug }),
      setBranchTriageBranch: (branchTriageBranch) => set({ branchTriageBranch }),
      requestBaxterChatReset: () =>
        set((state) => ({
          baxterChatResetNonce: state.baxterChatResetNonce + 1,
          baxterHistoryOpen: false,
          // A new chat is an unsaved one: the row appears in the archive when
          // the first message gives it a name, not before.
          baxterChatSessionId: "",
        })),
      setBaxterChatSessionId: (baxterChatSessionId) => set({ baxterChatSessionId }),
      setBaxterHistoryOpen: (baxterHistoryOpen) => set({ baxterHistoryOpen }),
      toggleBaxterHistory: () =>
        set((state) => ({ baxterHistoryOpen: !state.baxterHistoryOpen })),
      setCopilotHistoryOpen: (copilotHistoryOpen) => set({ copilotHistoryOpen }),
      toggleCopilotHistory: () =>
        set((state) => ({ copilotHistoryOpen: !state.copilotHistoryOpen })),
      setCopilotOpen: (copilotOpen) => set({ copilotOpen }),
      toggleCopilot: () => set({ copilotOpen: !get().copilotOpen }),
      setTerminalOpen: (terminalOpen) => set({ terminalOpen }),
      setCopilotHeight: (height) =>
        set({ copilotHeight: clampCopilotHeight(height) }),
      setCopilotWidth: (width) => set({ copilotWidth: clampCopilotWidth(width) }),
      setUtilityDockEdge: (utilityDockEdge) =>
        set({ utilityDockEdge: normalizeUtilityDockEdge(utilityDockEdge) }),
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
      version: 12,
      migrate: (persistedState, version) => {
        const state = { ...(persistedState as Record<string, unknown>) };
        if (version < 12 && typeof state.baxterChatSessionId !== "string") {
          state.baxterChatSessionId = "";
        }
        if (version < 11 && typeof state.chatWorkspaceSlug !== "string") {
          state.chatWorkspaceSlug = "";
        }
        if (version < 10) {
          state.utilityDockEdge = normalizeUtilityDockEdge(state.utilityDockEdge);
          state.copilotWidth = clampCopilotWidth(state.copilotWidth);
        }
        if (version < 9 && typeof state.terminalOpen !== "boolean") {
          state.terminalOpen = false;
        }
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
        chatWorkspaceSlug: s.chatWorkspaceSlug,
        baxterChatSessionId: s.baxterChatSessionId,
        copilotOpen: s.copilotOpen,
        copilotHeight: s.copilotHeight,
        copilotWidth: s.copilotWidth,
        utilityDockEdge: s.utilityDockEdge,
        terminalOpen: s.terminalOpen,
        hiveSkin: s.hiveSkin,
        hiveSpeedIndex: s.hiveSpeedIndex,
      }),
    },
  ),
);
