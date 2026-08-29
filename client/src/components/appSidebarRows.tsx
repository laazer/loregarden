/**
 * The sidebar's row primitives: one row per built-in page, one per view tab.
 *
 * They live beside `AppSidebar` rather than inside it because the sidebar's own
 * job — expansion state, section slicing, the reorder wiring — is already the
 * whole of that file, and the create-view control lands in it next.
 *
 * **Tab reaches a row, arrows reach its controls.** A view row carries move,
 * pin, rename, duplicate and delete buttons; leaving all of them in the tab
 * sequence costs six stops per entry where the rail this replaces cost one. The
 * controls are therefore a roving group: the row's link is the tab stop, and
 * Left/Right move focus along the row from there. A Tools row has no controls
 * to rove between and is one stop on its own.
 */

import type { DragEvent, KeyboardEvent, MouseEvent, ReactNode } from "react";
import { NavLink } from "react-router-dom";

import { viewPath } from "../lib/appNavigation";
import type { ViewKind, ViewSummary } from "../lib/viewsApi";
import type { SidebarPageDef } from "./appSidebarPages";

/** The wire values are `flex_grid` and `canvas`; these are what a tab shows. */
const VIEW_KIND_LABELS: Record<ViewKind, string> = {
  flex_grid: "Grid",
  canvas: "Canvas",
};

/**
 * A row's link keeps focus after it navigates — clicking it does not blur it
 * the way clicking away does — and the rail reads focus as a reason to stay
 * expanded (see `AppSidebar`'s `focusWithin`). Left alone, that pins the rail
 * open until something else on the page steals focus, well after the row that
 * caused it is gone. Blurring on click is scoped to just the row link: the
 * footer's own controls (the pin menu, in particular) still rely on focus to
 * stay open while a keyboard user is working through them.
 */
function blurOnClick(event: MouseEvent<HTMLAnchorElement>) {
  event.currentTarget.blur();
}

/**
 * Native HTML5 drag, the only reorder precedent in this app. It is pointer-only
 * by construction, which is why the move controls exist beside it rather than
 * as a fallback.
 */
export interface DragHandlers {
  draggable: true;
  onDragStart: () => void;
  onDragEnd: () => void;
  onDragOver: (event: DragEvent<HTMLLIElement>) => void;
  onDrop: () => void;
}

export interface MoveHandlers {
  canMoveUp: boolean;
  canMoveDown: boolean;
  onMoveUp: () => void;
  onMoveDown: () => void;
}

/**
 * Left/Right along a row's focusable controls. The rename field swallows its
 * own arrow keys — caret movement inside it is not row navigation.
 */
function onRowKeyDown(event: KeyboardEvent<HTMLLIElement>) {
  if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
  const target = event.target as HTMLElement;
  if (target.tagName === "INPUT") return;
  const stops = Array.from(
    event.currentTarget.querySelectorAll<HTMLElement>("a, button:not([disabled])"),
  );
  const index = stops.indexOf(target);
  if (index < 0) return;
  const next = stops[index + (event.key === "ArrowRight" ? 1 : -1)];
  if (!next) return;
  event.preventDefault();
  next.focus();
}

export function ControlIcon({ children }: { children: ReactNode }) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      aria-hidden
    >
      {children}
    </svg>
  );
}

/** A row control: reachable by arrow key from the row, never a tab stop of its own. */
function RowControl({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string;
  disabled?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      className="app-sidebar-control"
      aria-label={label}
      tabIndex={-1}
      disabled={disabled}
      onClick={onClick}
    >
      <ControlIcon>{children}</ControlIcon>
    </button>
  );
}

/**
 * Reordering by keyboard. The drag affordance is pointer-only, so the move
 * controls are the reachable path, not a fallback.
 */
function MoveControls({
  name,
  canMoveUp,
  canMoveDown,
  onMoveUp,
  onMoveDown,
}: MoveHandlers & { name: string }) {
  return (
    <>
      <RowControl label={`Move ${name} up`} disabled={!canMoveUp} onClick={onMoveUp}>
        <path d="m6 14 6-6 6 6" />
      </RowControl>
      <RowControl label={`Move ${name} down`} disabled={!canMoveDown} onClick={onMoveDown}>
        <path d="m6 10 6 6 6-6" />
      </RowControl>
    </>
  );
}

/**
 * A built-in page in the Tools section.
 *
 * It carries no controls at all, and that is the whole ticket: Tools is derived
 * from the page catalog rather than stored, so there is nothing to remove, and
 * nothing to reorder — the rank a move control would edit does not exist for
 * these rows. A row with no controls is also, incidentally, exactly one tab
 * stop without needing the roving group.
 */
export function ToolRow({ page, active }: { page: SidebarPageDef; active: boolean }) {
  return (
    <li className="app-sidebar-row">
      {/* `NavLink` owns `aria-current`, matching on the route the way the fixed
          rail did; the active treatment also covers the pages that own more
          than their own path. */}
      <NavLink
        to={page.path}
        end={page.path === "/"}
        className={`app-sidebar-link${active ? " app-sidebar-link--active" : ""}`}
        onClick={blurOnClick}
      >
        {active ? <span className="app-sidebar-bar" aria-hidden /> : null}
        <span className="app-sidebar-icon">{page.icon}</span>
        <span className="app-sidebar-name app-sidebar-reveal">{page.label}</span>
      </NavLink>
    </li>
  );
}

function RenameField({
  title,
  onSubmit,
  onCancel,
}: {
  title: string;
  onSubmit: (next: string) => void;
  onCancel: () => void;
}) {
  return (
    <input
      className="app-sidebar-rename"
      aria-label="View title"
      defaultValue={title}
      autoFocus
      onKeyDown={(event) => {
        if (event.key === "Enter") onSubmit(event.currentTarget.value.trim());
        if (event.key === "Escape") onCancel();
      }}
      onBlur={onCancel}
    />
  );
}

export function ViewRow({
  view,
  active,
  editing,
  move,
  drag,
  onStartRename,
  onRename,
  onCancelRename,
  onDuplicate,
  duplicateDisabled = false,
  onTogglePin,
  pinned,
  onClose,
}: {
  view: ViewSummary;
  active: boolean;
  editing: boolean;
  move: MoveHandlers;
  drag: DragHandlers;
  onStartRename: () => void;
  onRename: (title: string) => void;
  onCancelRename: () => void;
  onDuplicate: () => void;
  /** A duplicate is already in flight — the second click would make a second view. */
  duplicateDisabled?: boolean;
  /** Which section this row is drawn in, and therefore which way the control moves it. */
  pinned: boolean;
  onTogglePin: () => void;
  onClose: () => void;
}) {
  return (
    // Dragging is off while the title field is open, so a click-drag inside it
    // selects text instead of picking the row up.
    <li
      className="app-sidebar-row"
      onKeyDown={onRowKeyDown}
      {...drag}
      draggable={drag.draggable && !editing}
    >
      {/* While renaming, the field stands in for the name text; the link keeps
          the tab's name for assistive tech in the meantime. */}
      <NavLink
        to={viewPath(view.id)}
        aria-label={view.title}
        className={`app-sidebar-link${active ? " app-sidebar-link--active" : ""}`}
        onClick={blurOnClick}
      >
        {active ? <span className="app-sidebar-bar" aria-hidden /> : null}
        <span className="app-sidebar-icon">
          <ControlIcon>
            <rect x="3" y="4" width="18" height="16" rx="2" />
            <path d="M10 4v16" />
          </ControlIcon>
        </span>
        {editing ? null : (
          <span className="app-sidebar-name app-sidebar-reveal">{view.title}</span>
        )}
      </NavLink>
      {editing ? (
        <RenameField title={view.title} onSubmit={onRename} onCancel={onCancelRename} />
      ) : (
        <span className="app-sidebar-kind app-sidebar-reveal">{VIEW_KIND_LABELS[view.kind]}</span>
      )}
      <span className="app-sidebar-controls app-sidebar-reveal">
        <MoveControls name={view.title} {...move} />
        <RowControl label={`Rename ${view.title}`} onClick={onStartRename}>
          <path d="M4 20h4L19 9l-4-4L4 16z" />
        </RowControl>
        {/* There is no copy endpoint: a duplicate is this view's layout,
            deep-copied under fresh container ids and re-POSTed as a new view. */}
        <RowControl
          label={`Duplicate ${view.title}`}
          disabled={duplicateDisabled}
          onClick={onDuplicate}
        >
          <rect x="9" y="9" width="11" height="11" rx="2" />
          <path d="M5 15V5a2 2 0 0 1 2-2h8" />
        </RowControl>
        {/* The two sections are one list under two headings, so this is a
            toggle rather than a pair of controls: the row is in exactly one of
            them, and the label says where the click sends it. */}
        <RowControl
          label={`${pinned ? "Unpin" : "Pin"} ${view.title}`}
          onClick={onTogglePin}
        >
          {pinned ? (
            <path d="M5 5l14 14M9.5 4h5l-.6 5.2 3.1 3.1H14v4l-2 3-2-3v-4H7l3.1-3.1z" />
          ) : (
            <path d="M9.5 4h5l-.6 5.2 3.1 3.1H14v4l-2 3-2-3v-4H7l3.1-3.1z" />
          )}
        </RowControl>
        {/* Closing a view tab deletes the view: its sidebar entry is not
            separately deletable, and the server refuses that with a 400. It goes
            through a confirmation, because it cannot be undone. */}
        <RowControl label={`Delete ${view.title}`} onClick={onClose}>
          <path d="M6 6l12 12M18 6 6 18" />
        </RowControl>
      </span>
    </li>
  );
}
