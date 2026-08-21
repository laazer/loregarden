/**
 * The hover-expanding sidebar: Tools, then pinned view tabs, then the rest.
 *
 * **Tools is not stored.** It is the static page catalog, rendered directly.
 * The seven built-in pages are the application's own navigation, and reaching
 * them must not depend on rows a bad write could empty — a sidebar that lost
 * its pages offers no control that brings them back. Nothing seeds it, and it
 * cannot drift from the app's routes.
 *
 * Three things about its shape are load-bearing rather than incidental:
 *
 *   - **Expansion overlays, it never pushes.** The `nav` keeps its 60px box and
 *     an unchanging class list; only `data-expanded` moves, and the panel inside
 *     it is positioned out of flow. Nothing beside it can reflow because the
 *     pointer crossed the rail.
 *   - **Names are text in the row in both states**, hidden visually by CSS while
 *     collapsed. A `title` tooltip would satisfy an accessible-name query and
 *     still leave the name unreadable to a screen reader on a collapsed rail.
 *   - **Expansion answers focus as well as hover**, so the rail is usable
 *     without a pointer, and it never traps focus once expanded.
 *
 * The workspace slug arrives resolved: `uiStore.workspace` is `"all"` until a
 * workspace is chosen, and every view route 404s on it.
 */

import { useCallback, useId, useState, type FocusEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { useSidebarTabs } from "../hooks/useSidebarTabs";
import { pageFromPath, viewIdFromPath, viewPath } from "../lib/appNavigation";
import type { SidebarEntry, ViewSummary } from "../lib/viewsApi";
import { BrandMark } from "./BrandMark";
import { DeleteViewConfirmModal } from "./DeleteViewConfirmModal";
import { NewViewModal } from "./NewViewModal";
import { BaxterAvatar } from "./chat/BaxterAvatar";
import { SIDEBAR_PAGES } from "./appSidebarPages";
import {
  ControlIcon,
  ToolRow,
  ViewRow,
  type DragHandlers,
  type MoveHandlers,
} from "./appSidebarRows";
import "./AppSidebar.css";

export function AppSidebar({
  workspaceSlug,
  onOpenSettings,
}: {
  /** A concrete slug — never `"all"`, which 404s against every view route. */
  workspaceSlug: string;
  onOpenSettings: () => void;
}) {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const activeViewId = viewIdFromPath(pathname);
  const activePage = pageFromPath(pathname);
  const tabs = useSidebarTabs(workspaceSlug);

  const [hovered, setHovered] = useState(false);
  const [focusWithin, setFocusWithin] = useState(false);
  const [editingViewId, setEditingViewId] = useState("");
  const [draggingId, setDraggingId] = useState("");
  const [newViewOpen, setNewViewOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<ViewSummary | null>(null);
  const expanded = hovered || focusWithin;

  const toolsHeadingId = useId();
  const pinnedHeadingId = useId();
  const tabsHeadingId = useId();

  const { entries, viewsById, isReady, swapEntries, dropEntry } = tabs;

  /**
   * The neighbour a move actually swaps with.
   *
   * Ranking is one list across both tab sections, but they are *drawn*
   * separately. The neighbour in the full list can therefore belong to the
   * other section, and swapping with it re-ranks a row the user is not looking
   * at while leaving the section they are looking at byte-identical — a real
   * PATCH that reads as a no-op. So the neighbour is the nearest entry drawn in
   * the same section, and the permutation sent still spans both.
   *
   * `entry_kind` no longer separates the sections — both hold views — so the
   * partition is `pinned`, and leftover `page` entries from before Tools became
   * static are excluded rather than treated as unpinned tabs: they are drawn
   * nowhere, and a move that swapped with one would look like nothing happened.
   */
  const sectionNeighbour = useCallback(
    (entry: SidebarEntry, offset: number): SidebarEntry | undefined => {
      const index = entries.indexOf(entry);
      if (index < 0) return undefined;
      for (let at = index + offset; at >= 0 && at < entries.length; at += offset) {
        const other = entries[at];
        if (other.entry_kind === "view" && other.pinned === entry.pinned) return other;
      }
      return undefined;
    },
    [entries],
  );

  const moveHandlers = useCallback(
    (entry: SidebarEntry): MoveHandlers => {
      const previous = sectionNeighbour(entry, -1);
      const next = sectionNeighbour(entry, 1);
      return {
        canMoveUp: previous !== undefined,
        canMoveDown: next !== undefined,
        onMoveUp: () => {
          if (previous) swapEntries(entry.id, previous.id);
        },
        onMoveDown: () => {
          if (next) swapEntries(entry.id, next.id);
        },
      };
    },
    [sectionNeighbour, swapEntries],
  );

  /**
   * Whether a drop of the dragged row onto this one is a move at all.
   *
   * The pointer path needs `sectionNeighbour`'s rule for the same reason the
   * arrow buttons do: ranking spans both tab sections, so splicing across them
   * re-ranks rows in a section the user is not pointing at and leaves the one
   * they are pointing at unchanged — a real PATCH that reads as nothing having
   * happened. Pinning is what moves a tab between sections, and it is a
   * control of its own.
   */
  const sameSection = useCallback(
    (entryId: string, otherId: string): boolean => {
      const one = entries.find((entry) => entry.id === entryId);
      const other = entries.find((entry) => entry.id === otherId);
      if (!one || !other) return false;
      return one.entry_kind === "view" && other.entry_kind === "view" && one.pinned === other.pinned;
    },
    [entries],
  );

  const dragHandlers = useCallback(
    (entryId: string): DragHandlers => ({
      draggable: true,
      onDragStart: () => setDraggingId(entryId),
      onDragEnd: () => setDraggingId(""),
      // Not offered rather than accepted and dropped: a cursor that says the
      // drop will land is the part the user reads.
      onDragOver: (event) => {
        if (draggingId && sameSection(draggingId, entryId)) event.preventDefault();
      },
      onDrop: () => {
        if (draggingId && sameSection(draggingId, entryId)) dropEntry(draggingId, entryId);
        setDraggingId("");
      },
    }),
    [draggingId, dropEntry, sameSection],
  );

  const { createView, duplicateView, closeView, resetCreateView } = tabs;

  const openNewView = useCallback(() => {
    // A refusal the user walked away from is not this form's news.
    resetCreateView();
    setNewViewOpen(true);
  }, [resetCreateView]);

  /**
   * Land on the created view — never before. The id is the server's, and an
   * optimistic hop goes to a URL with no view behind it.
   */
  const onCreated = useCallback(
    (view: ViewSummary) => {
      setNewViewOpen(false);
      navigate(viewPath(view.id));
    },
    [navigate],
  );

  const confirmDelete = useCallback(() => {
    const view = pendingDelete;
    if (!view) return;
    closeView(view.id, () => {
      setPendingDelete(null);
      // Only when it is the view on screen: a blanket redirect kicks the user
      // off a view they were reading because a different tab was closed.
      if (activeViewId === view.id) navigate("/");
    });
  }, [pendingDelete, closeView, activeViewId, navigate]);

  const handleBlur = (event: FocusEvent<HTMLElement>) => {
    // Focus moving between rows is not focus leaving the rail.
    if (event.currentTarget.contains(event.relatedTarget)) return;
    setFocusWithin(false);
  };

  const rows = isReady ? entries : [];

  /**
   * One section's rows, in the server's order.
   *
   * Both sections read the same ranked list and differ only by `pinned`, so
   * they are drawn by one function — two near-identical loops is where the
   * sections drift apart.
   *
   * The kind test is the one that says what a tab section holds. A leftover
   * `page` entry would also fall out of the lookup below, since its `view_id`
   * is `""` and no view is keyed on that — but that is a property of the wire
   * shape rather than a rule anyone stated, and it is not what should be
   * keeping the app's own pages out of a list of views.
   */
  const viewRows = (pinned: boolean) =>
    rows.map((entry) => {
      if (entry.entry_kind !== "view" || entry.pinned !== pinned) return null;
      const view = viewsById.get(entry.view_id);
      if (!view) return null;
      return (
        <ViewRow
          key={entry.id}
          view={view}
          active={activeViewId === view.id}
          editing={editingViewId === view.id}
          pinned={entry.pinned}
          move={moveHandlers(entry)}
          drag={dragHandlers(entry.id)}
          onStartRename={() => setEditingViewId(view.id)}
          onCancelRename={() => setEditingViewId("")}
          onRename={(title) => {
            setEditingViewId("");
            if (title && title !== view.title) tabs.renameView(view.id, title);
          }}
          onDuplicate={() => duplicateView(view, onCreated)}
          duplicateDisabled={tabs.isDuplicatingView}
          onTogglePin={() => tabs.setEntryPinned(entry.id, !entry.pinned)}
          onClose={() => setPendingDelete(view)}
        />
      );
    });

  return (
    <>
      <nav
        className="app-sidebar"
        aria-label="Main navigation"
        data-expanded={expanded ? "true" : "false"}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        onFocus={() => setFocusWithin(true)}
        onBlur={handleBlur}
      >
        <div className="app-sidebar-panel">
          <div className="app-sidebar-brand">
            <BrandMark />
          </div>

          <div className="app-sidebar-section">
            <div className="app-sidebar-section-head">
              <span className="app-sidebar-section-title app-sidebar-reveal" id={toolsHeadingId}>
                Tools
              </span>
            </div>
            {/* Straight from the catalog: no query, no readiness gate, nothing
                a failed read or an empty table could shorten. */}
            <ul className="app-sidebar-list" aria-labelledby={toolsHeadingId}>
              {SIDEBAR_PAGES.map((page) => (
                <ToolRow
                  key={page.key}
                  page={page}
                  active={activeViewId === null && page.ownsPage(activePage)}
                />
              ))}
            </ul>
          </div>

          <div className="app-sidebar-section">
            <div className="app-sidebar-section-head">
              <span className="app-sidebar-section-title app-sidebar-reveal" id={pinnedHeadingId}>
                Pinned Tabs
              </span>
            </div>
            <ul className="app-sidebar-list" aria-labelledby={pinnedHeadingId}>
              {viewRows(true)}
            </ul>
          </div>

          <div className="app-sidebar-section">
            <div className="app-sidebar-section-head">
              <span className="app-sidebar-section-title app-sidebar-reveal" id={tabsHeadingId}>
                Tabs
              </span>
              {/* Gated on the workspace for the same reason the rows are: with
                  no slug the create POSTs to `/api/workspaces//views`, which is
                  a 404 the user asked for by pressing a control the chrome
                  offered them. */}
              {workspaceSlug === "" ? null : (
                <button
                  type="button"
                  className="app-sidebar-control"
                  aria-label="New view"
                  onClick={openNewView}
                >
                  <ControlIcon>
                    <path d="M12 5v14M5 12h14" />
                  </ControlIcon>
                </button>
              )}
            </div>
            <ul className="app-sidebar-list" aria-labelledby={tabsHeadingId}>
              {viewRows(false)}
            </ul>
          </div>

          <div className="app-sidebar-spacer" />

          <button type="button" className="app-sidebar-footer-btn" onClick={onOpenSettings}>
            <span className="app-sidebar-icon">
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                aria-hidden
              >
                <circle cx="12" cy="12" r="3" />
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
              </svg>
            </span>
            <span className="app-sidebar-name app-sidebar-reveal">Settings</span>
          </button>

          <div className="app-sidebar-footer-row">
            <span className="app-sidebar-avatar">
              <BaxterAvatar variant="head" state="idle" size={32} label="Baxter" />
            </span>
            <span className="app-sidebar-name app-sidebar-reveal">Baxter</span>
          </div>
        </div>
      </nav>

      {/* Outside the rail: the panel clips its own overflow, and a dialog is not
          part of the navigation landmark. */}
      {newViewOpen ? (
        <NewViewModal
          isCreating={tabs.isCreatingView}
          error={tabs.createViewError}
          onClose={() => setNewViewOpen(false)}
          onCreate={(input) => createView(input, onCreated)}
        />
      ) : null}
      <DeleteViewConfirmModal
        view={pendingDelete}
        isDeleting={tabs.isClosingView}
        onClose={() => setPendingDelete(null)}
        onConfirm={confirmDelete}
      />
    </>
  );
}
