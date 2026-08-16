/**
 * The `/view/:viewId` host.
 *
 * The URL carries an id and nothing else: the kind, the layout and the title all
 * arrive with the record, so the page dispatches on what it loaded rather than
 * on what the path looked like. That is also what makes the route deep-linkable
 * — a cold mount derives everything from the id.
 *
 * The arrangement renderers are `components/views/FlexGridSurface` (440, nested
 * resizable splits) and the canvas below (442, still a walk that puts each item
 * where its layout says). What lives here is the seam they hang off — the host
 * element carrying the view's identity, and the dispatch on the loaded kind. The
 * pane itself, and the write a pick makes, are `components/views/ContainerPane`:
 * a grid leaf and a canvas item both draw one.
 *
 * Three failure paths are load-bearing:
 *
 *   - **404 is a state, not a redirect.** Bouncing to `/` looks helpful and is a
 *     loop: the sidebar entry the user clicked is still cached, so they click it
 *     again. The read is a query rather than a mutation and raises no toast of
 *     its own — a deleted view explains itself in the pane, once.
 *   - **Any other failure is not "this view is gone."** A 500 or a dropped
 *     connection renders as a failed read, so the user is not told to stop
 *     looking for a view that is still there. And only when there is nothing
 *     else to show: this query refetches on window focus, so a background read
 *     that fails while a good record is in hand reports itself over the view
 *     rather than replacing it — the alternative blanks a working view because
 *     the user tabbed away and back, and leaves it blanked.
 *   - **A record this page cannot draw is not a blank screen.** A layout that is
 *     not an object, a kind with no renderer, or a grid whose arrangement cannot
 *     be read would otherwise render an empty body — the same nothing AC4 rules
 *     out, reached through the success path. Which is why the grid's tree is
 *     parsed *here*: the renderer has no way to say "undrawable" except by
 *     rendering nothing, and this page is where nothing is not an option.
 */

import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { ApiError } from "../api/http";
import { ContainerPane } from "../components/views/ContainerPane";
import { FlexGridSurface } from "../components/views/FlexGridSurface";
import { readGridTree } from "../lib/gridLayout";
import { asJson } from "../lib/viewLayouts";
import { fetchView, viewsKeys } from "../lib/viewsApi";
import { useSidebarWorkspace } from "../state/SidebarWorkspaceContext";
import { describeError } from "../state/toastStore";

type Json = Record<string, unknown>;

function CanvasSurface({ layout }: { layout: Json }) {
  const containers = asJson(layout.containers) ?? {};
  const items = Array.isArray(layout.items) ? layout.items : [];

  return (
    <div style={{ position: "relative", flex: "1 1 0", minHeight: 0, overflow: "auto" }}>
      {/* An empty canvas is a legitimate stored state — it is what New View →
          Canvas creates — and drawing it as literally nothing leaves the user on
          a blank screen with no text, no control and no way to tell a new view
          from a broken one. The affordance that fills it is 442's. */}
      {items.length === 0 ? (
        <div className="queue-page-empty" data-testid="view-canvas-empty">
          <p style={{ maxWidth: 520 }}>
            This canvas is empty. Drop a container onto it to get started.
          </p>
        </div>
      ) : null}
      {items.map((raw, index) => {
        const item = asJson(raw);
        const containerId = typeof item?.container_id === "string" ? item.container_id : "";
        if (item === undefined || containerId === "") return null;
        const key = typeof item.id === "string" ? item.id : String(index);
        return (
          <div
            key={key}
            style={{
              position: "absolute",
              left: Number(item.x) || 0,
              top: Number(item.y) || 0,
              width: Number(item.width) || 0,
              height: Number(item.height) || 0,
              display: "flex",
            }}
          >
            <ContainerPane containerId={containerId} container={containers[containerId]} />
          </div>
        );
      })}
    </div>
  );
}

function ViewNotFound() {
  return (
    <div className="queue-page-empty" data-testid="view-not-found">
      <h2 style={{ marginTop: 0 }}>This view is gone</h2>
      <p style={{ maxWidth: 520 }}>
        It was deleted, or the link points at an id this workspace does not have.
      </p>
      <Link className="btn-secondary" to="/">
        Back to Home
      </Link>
    </div>
  );
}

/**
 * A record that loaded and still cannot be drawn: a layout that is not an
 * object, or a kind with no renderer behind it. Both are reachable from a 200.
 */
function ViewUndrawable({ reason }: { reason: string }) {
  return (
    <div className="queue-page-empty" data-testid="view-undrawable">
      <h2 style={{ marginTop: 0 }}>This view cannot be drawn</h2>
      <p style={{ maxWidth: 520 }}>{reason}</p>
    </div>
  );
}

export function ViewPage() {
  const { viewId = "" } = useParams<{ viewId: string }>();
  const { slug, isResolved } = useSidebarWorkspace();

  const view = useQuery({
    queryKey: viewsKeys.view(slug, viewId),
    queryFn: () => fetchView(slug, viewId),
    enabled: slug !== "" && viewId !== "",
    // A 404 is an answer, not a hiccup: re-asking three times delays the state
    // the user is owed and hammers the server on every mistyped link.
    retry: false,
  });

  const loaded = view.data;

  if (slug === "") {
    // "No workspace yet" and "no workspace at all" are different states, and
    // this route has no picker on it: telling a user to pick one while the
    // chrome is still deciding is advice they cannot act on.
    return (
      <div className="queue-page-empty">
        <p>{isResolved ? "Pick a workspace to open its views." : "Loading workspace…"}</p>
      </div>
    );
  }

  // A 404 answers the question even when a record is in hand: the view the user
  // is looking at has just been deleted, and keeping it on screen is a lie.
  if (view.error instanceof ApiError && view.error.status === 404) return <ViewNotFound />;

  if (view.error && loaded === undefined) {
    return (
      <div className="queue-page-empty" data-testid="view-load-failed">
        <h2 style={{ marginTop: 0 }}>This view could not be loaded</h2>
        <p style={{ maxWidth: 520 }}>{describeError(view.error, "The view failed to load")}</p>
      </div>
    );
  }

  if (loaded === undefined) {
    return (
      <div className="queue-page-empty">
        <p>Loading view…</p>
      </div>
    );
  }

  const layout = asJson(loaded.layout);

  return (
    <div
      data-testid="view-host"
      data-view-id={loaded.id}
      data-view-kind={loaded.kind}
      style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}
    >
      {/* A refresh that failed while the record on screen is still good: said
          over the view, never instead of it. */}
      {view.error ? (
        <p
          data-testid="view-refresh-failed"
          role="status"
          style={{ margin: 0, padding: "6px 12px", color: "var(--txl)", fontSize: 12.5 }}
        >
          {describeError(view.error, "This view could not be refreshed")}
        </p>
      ) : null}
      {/* Keyed by the view: the pane components carry a layout write of their
          own, and reusing one across two views hands a pending write the
          identity of a view it was never issued against. */}
      <ViewSurface key={loaded.id} kind={loaded.kind} layout={layout} />
    </div>
  );
}

function ViewSurface({ kind, layout }: { kind: string; layout: Json | undefined }) {
  if (layout === undefined) {
    return <ViewUndrawable reason="Its stored layout is missing or is not a layout." />;
  }
  if (kind === "canvas") return <CanvasSurface layout={layout} />;
  if (kind === "flex_grid") {
    // Parsed here rather than inside the renderer, because "this grid has no
    // readable arrangement" is one of the undrawable states this page owns — a
    // renderer that discovered it and returned nothing would put the user on the
    // blank screen the states above exist to rule out.
    const tree = readGridTree(layout);
    if (tree === undefined) {
      return <ViewUndrawable reason="Its stored arrangement could not be read." />;
    }
    return <FlexGridSurface tree={tree} containers={asJson(layout.containers) ?? {}} />;
  }
  return (
    <ViewUndrawable reason="It is stored in a form this build has no renderer for." />
  );
}
