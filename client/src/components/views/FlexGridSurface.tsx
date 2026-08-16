/**
 * The flex-grid arrangement: a recursive split tree, the controls that edit it,
 * and the dividers that resize it.
 *
 * The renderer owns four things, and each of them is a bug it exists to avoid:
 *
 *   - **Sizes reach the DOM as grow factors and nothing else.** A pane sized in
 *     pixels drifts the moment the window changes, and a terminal in it clips
 *     instead of reflowing. `min-width`/`min-height: 0` go with them: a flex
 *     child defaults to `min-*: auto` and refuses to shrink below its content.
 *   - **Every child is keyed by its node id.** Splitting inserts a node *above*
 *     a leaf and closing shifts every later sibling down one index — under index
 *     keys React hands the terminal at index 2 the instance that was at index 1,
 *     which is a new shell with no scrollback in it.
 *   - **A drag holds a local draft.** There is no optimistic update in the write
 *     path, so a renderer that dropped its drag state on `pointerup` would snap
 *     the panes back to where they started until the PATCH landed — and leave
 *     them there if it failed. The draft outlives both, and is dropped only when
 *     the split's children are no longer the ones it was measured against.
 *   - **The minimum pane size is arithmetic, not CSS.** A CSS floor stops the
 *     pane shrinking on screen while the stored fraction keeps falling, so the
 *     screen and the record quietly disagree until the next reload.
 *
 * Dividers speak Pointer Events because pointer capture is the platform's answer
 * to "the drag must not reach the terminal underneath it", and it is a
 * pointer-event API.
 */

import {
  useCallback,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import { useParams } from "react-router-dom";

import { useViewLayoutEdit, useViewLayoutWrite } from "../../hooks/useViewLayoutEdit";
import {
  closeLeaf,
  resizeSplit,
  splitLeaf,
  withSize,
  type GridLeaf,
  type GridNodeModel,
  type GridSplit,
  type SplitOrientation,
} from "../../lib/gridLayout";
import { useSidebarWorkspaceSlug } from "../../state/SidebarWorkspaceContext";
import { ContainerPane } from "./ContainerPane";
import { HEADER_BUTTON, paneTitle } from "./paneChrome";
import { PrimitivePicker } from "./PrimitivePicker";

type Json = Record<string, unknown>;

/**
 * The thinnest pane this renderer will produce, in pixels of the track it is
 * dividing. The server's own rule is only `size > 0`, which accepts a pane one
 * pixel wide.
 */
const MIN_PANE_PX = 32;

/** How far one arrow-key press moves a divider, in pixels of its track. */
const KEY_STEP_PX = 16;

interface GridActions {
  split: (nodeId: string, orientation: SplitOrientation) => void;
  close: (nodeId: string) => void;
  resize: (splitId: string, sizes: number[]) => void;
  pickPrimitive: (containerId: string, primitiveId: string) => void;
}

/**
 * The box every node is laid out in: a fraction of its parent, free to shrink.
 *
 * No width and no height — the pane's size is the grow factor and the browser's
 * arithmetic, which is what makes it observable by a `ResizeObserver` rather
 * than something a stored pixel count dictates.
 */
function nodeStyle(size: number): CSSProperties {
  return {
    display: "flex",
    flexGrow: size,
    flexShrink: 1,
    flexBasis: 0,
    minWidth: 0,
    minHeight: 0,
  };
}

/**
 * The two adjacent panes at `gap`, moved by `deltaFraction` of the track.
 *
 * Only the pair pays for the move — a divider is between two panes and the rest
 * of the split is not adjacent to it — and the pair's own total is preserved
 * exactly, so the siblings still sum to 1.0 the way `_StructureWalk` requires.
 */
function resizedPair(
  sizes: number[],
  gap: number,
  deltaFraction: number,
  minFraction: number,
): number[] {
  const pair = sizes[gap] + sizes[gap + 1];
  const high = pair - minFraction;
  // Two panes that cannot both clear the floor: the drag has nowhere to go, and
  // clamping into an inverted range is how one of them becomes negative.
  if (high <= minFraction) return sizes;

  const first = Math.min(Math.max(sizes[gap] + deltaFraction, minFraction), high);
  const next = [...sizes];
  next[gap] = first;
  next[gap + 1] = pair - first;
  return next;
}

/** Which way an arrow key moves a divider that runs along `orientation`. */
function keyStep(key: string, vertical: boolean): number {
  if (vertical) {
    if (key === "ArrowDown") return 1;
    if (key === "ArrowUp") return -1;
    return 0;
  }
  if (key === "ArrowRight") return 1;
  if (key === "ArrowLeft") return -1;
  return 0;
}

/**
 * One divider's gesture: where it started, what it started from, and which
 * pointer owns it.
 *
 * Per gap and not per split, because a split has as many handles as it has gaps
 * and a touchscreen can hold two of them at once. One shared slot lets the
 * second press overwrite the first's gap and origin, so the first finger's moves
 * then resize the *other* pair from the wrong starting point — and the second
 * finger's release finds nothing to commit and never gives the capture back.
 *
 * `pointerId` is stored for the same gesture: capture routes every later event
 * to the handle that took it, so a second pointer crossing that handle delivers
 * moves and ups against a drag it is no part of.
 */
interface DragState {
  pointerId: number;
  origin: number;
  track: number;
  base: number[];
}

function GridSplitView({
  split,
  containers,
  actions,
}: {
  split: GridSplit;
  containers: Json;
  actions: GridActions;
}) {
  const element = useRef<HTMLDivElement>(null);
  /** The open gesture on each gap, by gap index. */
  const drags = useRef(new Map<number, DragState>());
  /**
   * Sizes adjusted but not yet committed, by gap — for keyup, blur and cancel.
   *
   * Keyed like the drags and for the same reason: an arrow-key nudge left
   * unsaved on one handle is not something a `pointercancel` on a different
   * handle may commit on its behalf.
   */
  const pending = useRef(new Map<number, number[]>());
  const [draft, setDraft] = useState<{ ids: string; sizes: number[] } | undefined>(undefined);

  const vertical = split.orientation === "vertical";
  const ids = split.children.map((child) => child.id).join("|");
  const sizes =
    draft !== undefined && draft.ids === ids ? draft.sizes : split.children.map((c) => c.size);

  const apply = useCallback(
    (gap: number, next: number[]) => {
      pending.current.set(gap, next);
      setDraft({ ids, sizes: next });
    },
    [ids],
  );

  const commit = useCallback(
    (gap: number, next: number[]) => {
      pending.current.delete(gap);
      actions.resize(split.id, next);
    },
    [actions, split.id],
  );

  /** The split's own box is the track a fraction is a fraction *of*. */
  const trackPx = useCallback((): number => {
    if (element.current === null) return 0;
    const rect = element.current.getBoundingClientRect();
    return vertical ? rect.height : rect.width;
  }, [vertical]);

  function draggedTo(
    active: DragState,
    gap: number,
    event: ReactPointerEvent<HTMLDivElement>,
  ): number[] {
    const position = vertical ? event.clientY : event.clientX;
    return resizedPair(
      active.base,
      gap,
      (position - active.origin) / active.track,
      MIN_PANE_PX / active.track,
    );
  }

  /** The gesture this event belongs to, or `undefined` when it belongs to none. */
  function dragFor(gap: number, event: ReactPointerEvent<HTMLDivElement>): DragState | undefined {
    const active = drags.current.get(gap);
    if (active === undefined || active.pointerId !== event.pointerId) return undefined;
    return active;
  }

  function onPointerDown(gap: number, event: ReactPointerEvent<HTMLDivElement>) {
    // Without this the browser starts a native text selection (or an image
    // drag) that runs for the whole gesture.
    event.preventDefault();
    // A second pointer landing on a handle that is already being dragged is not
    // a new gesture: the first still owns the capture, and adopting the second
    // would move the origin out from under the moves still arriving for it.
    if (drags.current.has(gap)) return;
    const track = trackPx();
    if (track <= 0) return;
    // Capture is what keeps a move over a terminal from reaching the terminal.
    event.currentTarget.setPointerCapture(event.pointerId);
    drags.current.set(gap, {
      pointerId: event.pointerId,
      origin: vertical ? event.clientY : event.clientX,
      track,
      base: sizes,
    });
  }

  function onPointerMove(gap: number, event: ReactPointerEvent<HTMLDivElement>) {
    const active = dragFor(gap, event);
    // Drawn, not written: a PATCH per pointermove is the bug AC5 names outright.
    if (active !== undefined) apply(gap, draggedTo(active, gap, event));
  }

  function endDrag(
    gap: number,
    event: ReactPointerEvent<HTMLDivElement>,
    sizesAtEnd: number[] | undefined,
  ) {
    drags.current.delete(gap);
    if (sizesAtEnd !== undefined) {
      apply(gap, sizesAtEnd);
      commit(gap, sizesAtEnd);
    }
    // Last, because releasing capture is cleanup and storing the resize is the
    // point: `releasePointerCapture` throws on a pointer the element no longer
    // holds, and a throw ahead of the commit drops the user's drag in silence.
    event.currentTarget.releasePointerCapture(event.pointerId);
  }

  function onPointerUp(gap: number, event: ReactPointerEvent<HTMLDivElement>) {
    const active = dragFor(gap, event);
    if (active === undefined) return;
    endDrag(gap, event, draggedTo(active, gap, event));
  }

  function onPointerCancel(gap: number, event: ReactPointerEvent<HTMLDivElement>) {
    // The gesture was interrupted, but what is on screen is still what the user
    // dragged to; storing it is kinder than reverting it without a word.
    if (dragFor(gap, event) === undefined) return;
    endDrag(gap, event, pending.current.get(gap));
  }

  function onKeyDown(gap: number, event: ReactKeyboardEvent<HTMLDivElement>) {
    const step = keyStep(event.key, vertical);
    if (step === 0) return;
    event.preventDefault();
    const track = trackPx();
    if (track <= 0) return;
    const unsaved = pending.current.get(gap);
    const base = unsaved !== undefined && unsaved.length === sizes.length ? unsaved : sizes;
    apply(gap, resizedPair(base, gap, (step * KEY_STEP_PX) / track, MIN_PANE_PX / track));
  }

  /**
   * One adjustment, one PATCH. A held arrow key repeats its keydown and the
   * draft follows every repeat; the write waits for the key to come up, or for
   * the handle to lose focus with an adjustment still unsaved.
   */
  function onSettle(gap: number) {
    const adjusted = pending.current.get(gap);
    if (adjusted !== undefined) commit(gap, adjusted);
  }

  const children: ReactNode[] = [];
  split.children.forEach((child, index) => {
    children.push(
      <GridNodeView
        key={child.id}
        // Drawn at the draft's size while a drag is open, at the stored one
        // otherwise — the child itself is unchanged either way.
        node={withSize(child, sizes[index])}
        containers={containers}
        actions={actions}
      />,
    );
    if (index === split.children.length - 1) return;
    children.push(
      <div
        key={`divider:${split.id}:${index}`}
        data-grid-divider={`${split.id}:${index}`}
        role="separator"
        aria-orientation={vertical ? "horizontal" : "vertical"}
        aria-label={vertical ? "Resize the panes above and below" : "Resize the panes either side"}
        aria-valuenow={Math.round(sizes[index] * 100)}
        aria-valuemin={0}
        aria-valuemax={100}
        tabIndex={0}
        onPointerDown={(event) => onPointerDown(index, event)}
        onPointerMove={(event) => onPointerMove(index, event)}
        onPointerUp={(event) => onPointerUp(index, event)}
        onPointerCancel={(event) => onPointerCancel(index, event)}
        onKeyDown={(event) => onKeyDown(index, event)}
        onKeyUp={() => onSettle(index)}
        onBlur={() => onSettle(index)}
        style={{
          flex: "0 0 6px",
          alignSelf: "stretch",
          background: "var(--bd)",
          cursor: vertical ? "row-resize" : "col-resize",
          // The handle itself is never text to select, and never a touch scroll.
          userSelect: "none",
          touchAction: "none",
        }}
      />,
    );
  });

  return (
    <div
      ref={element}
      data-grid-node={split.id}
      style={{ ...nodeStyle(split.size), flexDirection: vertical ? "column" : "row" }}
    >
      {children}
    </div>
  );
}

function GridLeafView({
  leaf,
  containers,
  actions,
}: {
  leaf: GridLeaf;
  containers: Json;
  actions: GridActions;
}) {
  const [picking, setPicking] = useState(false);
  const container = containers[leaf.container_id];

  return (
    <div
      data-grid-node={leaf.id}
      style={{ ...nodeStyle(leaf.size), flexDirection: "column", overflow: "hidden" }}
    >
      {/* The split and close controls live on the container, not in a global
          toolbar: one pair for two panes cannot say which pane it splits. */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 4,
          padding: "4px 6px",
          borderBottom: "1px solid var(--bd)",
          minWidth: 0,
        }}
      >
        <span
          style={{
            flex: "1 1 0",
            minWidth: 0,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            color: "var(--txl)",
            fontSize: 11.5,
          }}
        >
          {paneTitle(container)}
        </span>
        <button
          type="button"
          className={HEADER_BUTTON}
          data-grid-action="pick-primitive"
          aria-label="Change contents"
          aria-expanded={picking}
          onClick={() => setPicking((open) => !open)}
        >
          <span aria-hidden="true">⇄</span>
        </button>
        <button
          type="button"
          className={HEADER_BUTTON}
          data-grid-action="split-horizontal"
          aria-label="Split horizontally"
          onClick={() => actions.split(leaf.id, "horizontal")}
        >
          <span aria-hidden="true">▥</span>
        </button>
        <button
          type="button"
          className={HEADER_BUTTON}
          data-grid-action="split-vertical"
          aria-label="Split vertically"
          onClick={() => actions.split(leaf.id, "vertical")}
        >
          <span aria-hidden="true">▤</span>
        </button>
        <button
          type="button"
          className={HEADER_BUTTON}
          data-grid-action="close"
          aria-label="Close this pane"
          onClick={() => actions.close(leaf.id)}
        >
          <span aria-hidden="true">✕</span>
        </button>
      </div>
      {picking ? (
        <div style={{ padding: "6px 8px", borderBottom: "1px solid var(--bd)" }}>
          <PrimitivePicker
            onPick={(primitiveId) => {
              setPicking(false);
              actions.pickPrimitive(leaf.container_id, primitiveId);
            }}
          />
        </div>
      ) : null}
      <div style={{ display: "flex", flex: "1 1 0", minHeight: 0, minWidth: 0 }}>
        <ContainerPane containerId={leaf.container_id} container={container} />
      </div>
    </div>
  );
}

function GridNodeView({
  node,
  containers,
  actions,
}: {
  node: GridNodeModel;
  containers: Json;
  actions: GridActions;
}) {
  if (node.node === "split") {
    return <GridSplitView split={node} containers={containers} actions={actions} />;
  }
  return <GridLeafView leaf={node} containers={containers} actions={actions} />;
}

/**
 * The arrangement, already parsed.
 *
 * The tree arrives read rather than read here: a layout this renderer cannot
 * parse is a state the *page* owns — it has a `ViewUndrawable` to say it with,
 * and a renderer that discovered it could only return nothing, which is the one
 * blank screen the page rules out.
 */
export function FlexGridSurface({
  tree,
  containers,
}: {
  tree: GridNodeModel;
  containers: Json;
}) {
  const slug = useSidebarWorkspaceSlug();
  // Outside the view route there is no id, and the write refuses to compose a
  // PATCH without one — a grid can only reach the screen underneath it.
  const { viewId = "" } = useParams<{ viewId: string }>();
  const edit = useViewLayoutEdit(slug, viewId);
  const pickPrimitive = useViewLayoutWrite(slug, viewId);

  const actions = useMemo<GridActions>(
    () => ({
      // Each edit is composed at the front of the view's write queue, from the
      // newest layout this client holds — never from the tree that was on screen
      // when the button was pressed, which a still-open PATCH would revert.
      split: (nodeId, orientation) => edit((current) => splitLeaf(current, nodeId, orientation)),
      close: (nodeId) => edit((current) => closeLeaf(current, nodeId)),
      resize: (splitId, sizes) => edit((current) => resizeSplit(current, splitId, sizes)),
      pickPrimitive,
    }),
    [edit, pickPrimitive],
  );

  return <GridNodeView node={tree} containers={containers} actions={actions} />;
}
