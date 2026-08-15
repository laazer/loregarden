/**
 * The hover-expanding sidebar: pinned built-in pages above, view tabs below.
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

import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type FocusEvent,
  type KeyboardEvent,
} from "react";
import { useLocation } from "react-router-dom";

import { useSidebarTabs } from "../hooks/useSidebarTabs";
import { pageFromPath, viewIdFromPath } from "../lib/appNavigation";
import type { SidebarEntry } from "../lib/viewsApi";
import { BrandMark } from "./BrandMark";
import { BaxterAvatar } from "./chat/BaxterAvatar";
import {
  DEFAULT_PINNED_PAGE_KEYS,
  SIDEBAR_PAGES,
  sidebarPageForKey,
} from "./appSidebarPages";
import {
  ControlIcon,
  PageRow,
  ViewRow,
  type DragHandlers,
  type MoveHandlers,
} from "./appSidebarRows";
import "./AppSidebar.css";

/** A stable identity for "seed nothing", so the seeding effect keeps its deps. */
const NO_SEED_PAGE_KEYS: string[] = [];

/**
 * Re-pinning a page that was unpinned. Without it, unpinning removes a built-in
 * page's only entry point with no way back.
 *
 * It follows `OverflowMenu`'s dismissal contract — Escape, and a pointer press
 * outside it — because a menu that only its own trigger can close is one a
 * keyboard user cannot back out of.
 */
function PinPageMenu({
  pinnedKeys,
  expanded,
  onPin,
}: {
  pinnedKeys: Set<string>;
  expanded: boolean;
  onPin: (pageKey: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const available = SIDEBAR_PAGES.filter((page) => !pinnedKeys.has(page.key));
  // A collapsed rail clips the menu; leaving it open would bring it back on the
  // next hover without anyone having asked for it.
  useEffect(() => {
    if (!expanded) setOpen(false);
  }, [expanded]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  // `role="menu"` promises arrow-key movement between its items; the roles are
  // what assistive tech announces, so they come with the behaviour or not at all.
  const onMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const items = Array.from(
      event.currentTarget.querySelectorAll<HTMLElement>("[role='menuitem']"),
    );
    const index = items.indexOf(event.target as HTMLElement);
    if (index < 0) return;
    const next = items[index + (event.key === "ArrowDown" ? 1 : -1)];
    if (!next) return;
    event.preventDefault();
    next.focus();
  };

  return (
    <div className="app-sidebar-pin" ref={rootRef}>
      <button
        type="button"
        className="app-sidebar-footer-btn"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={available.length === 0}
        onClick={() => setOpen((current) => !current)}
      >
        <span className="app-sidebar-icon">
          <ControlIcon>
            <path d="M12 5v14M5 12h14" />
          </ControlIcon>
        </span>
        <span className="app-sidebar-name app-sidebar-reveal">Pin a page</span>
      </button>
      {open && available.length > 0 ? (
        <div
          className="app-sidebar-menu"
          role="menu"
          aria-label="Pages to pin"
          onKeyDown={onMenuKeyDown}
        >
          {available.map((page) => (
            <button
              key={page.key}
              type="button"
              role="menuitem"
              className="app-sidebar-menu-item"
              onClick={() => {
                setOpen(false);
                onPin(page.key);
              }}
            >
              {page.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function AppSidebar({
  workspaceSlug,
  seedDefaults = true,
  onOpenSettings,
}: {
  /** A concrete slug — never `"all"`, which 404s against every view route. */
  workspaceSlug: string;
  /**
   * Whether an unset workspace may be set up. Seeding writes seven pins, so it
   * needs a concrete, stable slug; the caller says whether it has one.
   */
  seedDefaults?: boolean;
  onOpenSettings: () => void;
}) {
  const { pathname } = useLocation();
  const activeViewId = viewIdFromPath(pathname);
  const activePage = pageFromPath(pathname);
  const tabs = useSidebarTabs(
    workspaceSlug,
    seedDefaults ? DEFAULT_PINNED_PAGE_KEYS : NO_SEED_PAGE_KEYS,
  );

  const [hovered, setHovered] = useState(false);
  const [focusWithin, setFocusWithin] = useState(false);
  const [editingViewId, setEditingViewId] = useState("");
  const [draggingId, setDraggingId] = useState("");
  const expanded = hovered || focusWithin;

  const pinnedHeadingId = useId();
  const tabsHeadingId = useId();

  const { entries, viewsById, isReady, swapEntries, dropEntry } = tabs;
  const pinnedKeys = useMemo(
    () => new Set(entries.filter((e) => e.entry_kind === "page").map((e) => e.page_key)),
    [entries],
  );

  /**
   * The neighbour a move actually swaps with.
   *
   * Ranking is one list across both sections, but the sections are *drawn*
   * separately. The neighbour in the full list can therefore be an entry of the
   * other kind, and swapping with it re-ranks a row the user is not looking at
   * while leaving the section they are looking at byte-identical — a real PATCH
   * that reads as a no-op. So the neighbour is the nearest entry of the same
   * kind, and the permutation sent still spans both sections.
   */
  const sectionNeighbour = useCallback(
    (entry: SidebarEntry, offset: number): SidebarEntry | undefined => {
      const index = entries.indexOf(entry);
      if (index < 0) return undefined;
      for (let at = index + offset; at >= 0 && at < entries.length; at += offset) {
        if (entries[at].entry_kind === entry.entry_kind) return entries[at];
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

  const dragHandlers = useCallback(
    (entryId: string): DragHandlers => ({
      draggable: true,
      onDragStart: () => setDraggingId(entryId),
      onDragEnd: () => setDraggingId(""),
      onDragOver: (event) => {
        if (draggingId) event.preventDefault();
      },
      onDrop: () => {
        if (draggingId) dropEntry(draggingId, entryId);
        setDraggingId("");
      },
    }),
    [draggingId, dropEntry],
  );

  const handleBlur = (event: FocusEvent<HTMLElement>) => {
    // Focus moving between rows is not focus leaving the rail.
    if (event.currentTarget.contains(event.relatedTarget)) return;
    setFocusWithin(false);
  };

  const rows = isReady ? entries : [];

  return (
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
            <span className="app-sidebar-section-title app-sidebar-reveal" id={pinnedHeadingId}>
              Pinned Tabs
            </span>
          </div>
          <ul className="app-sidebar-list" aria-labelledby={pinnedHeadingId}>
            {rows.map((entry) => {
              if (entry.entry_kind !== "page") return null;
              // An unknown key keeps its rank and is not routed on blind.
              const page = sidebarPageForKey(entry.page_key);
              if (!page) return null;
              return (
                <PageRow
                  key={entry.id}
                  page={page}
                  active={activeViewId === null && page.ownsPage(activePage)}
                  move={moveHandlers(entry)}
                  drag={dragHandlers(entry.id)}
                  onUnpin={() => tabs.unpinPageEntry(entry.id)}
                />
              );
            })}
          </ul>
        </div>

        <div className="app-sidebar-section">
          <div className="app-sidebar-section-head">
            <span className="app-sidebar-section-title app-sidebar-reveal" id={tabsHeadingId}>
              Tabs
            </span>
          </div>
          <ul className="app-sidebar-list" aria-labelledby={tabsHeadingId}>
            {rows.map((entry) => {
              if (entry.entry_kind !== "view") return null;
              const view = viewsById.get(entry.view_id);
              if (!view) return null;
              return (
                <ViewRow
                  key={entry.id}
                  view={view}
                  active={activeViewId === view.id}
                  editing={editingViewId === view.id}
                  move={moveHandlers(entry)}
                  drag={dragHandlers(entry.id)}
                  onStartRename={() => setEditingViewId(view.id)}
                  onCancelRename={() => setEditingViewId("")}
                  onRename={(title) => {
                    setEditingViewId("");
                    if (title && title !== view.title) tabs.renameView(view.id, title);
                  }}
                  onClose={() => tabs.closeView(view.id)}
                />
              );
            })}
          </ul>
        </div>

        <div className="app-sidebar-spacer" />

        <PinPageMenu pinnedKeys={pinnedKeys} expanded={expanded} onPin={tabs.pinPageKey} />

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
  );
}
