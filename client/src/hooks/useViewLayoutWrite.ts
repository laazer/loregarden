/**
 * Every write to a stored layout: a container choosing its primitive, and the
 * arrangement edits a renderer makes around it.
 *
 * It is a hook rather than a handler composed in the page because both view
 * renderers — the grid's leaves (440) and the canvas's items (442) — put a
 * container pane on screen, and a pane that has to be *handed* its write means
 * every renderer re-deriving how the write is composed.
 *
 * Three properties are load-bearing, and each of them was a bug first:
 *
 *   - **The write carries its own identity.** `useMutation` re-binds its options
 *     on every render, in-flight mutations included, so a handler that reads
 *     `slug`/`viewId` from the closure acts on whichever view is on screen when
 *     the request *lands*. A PATCH issued on view A that resolves after the user
 *     opened view B then writes A's record into B's cache entry — and the next
 *     pick sends A's layout to B, destroying it server-side. `slug` and `viewId`
 *     travel in the mutation variables, and every callback reads them from
 *     there.
 *   - **Writes to one view are serialized.** `scope` makes react-query queue a
 *     second write behind the first, and the body is composed inside
 *     `mutationFn` — that is, once the queue releases it, after the previous
 *     write's `onSuccess` has put the server's record in the cache. A body
 *     composed at click time from the layout on screen would revert whatever the
 *     open PATCH was writing, and the server accepts it: PATCH replaces the
 *     layout whole.
 *   - **The base layout is the cache's, not a captured one.** For the same
 *     reason: the record react-query holds is the newest one this client knows.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import { newContainerFor } from "../components/views/primitives/registry";
import { asJson } from "../lib/viewLayouts";
import { updateView, viewsKeys, type ViewLayout, type ViewSummary } from "../lib/viewsApi";

/**
 * How a caller says what it wants stored: given the newest layout this client
 * knows, the layout to PATCH.
 *
 * A function rather than a composed body, because *when* the body is composed is
 * the whole point of the queue below — it runs once the previous write's record
 * is in the cache, not at click time. An edit that cannot be made (a split
 * deeper than the server accepts, a node that has since been closed) throws, and
 * the mutation reports it through the same toast a rejected PATCH uses.
 */
export type LayoutEdit = (layout: ViewLayout) => ViewLayout;

/** What a single write has to know, none of it read from a render closure. */
interface LayoutWrite {
  slug: string;
  viewId: string;
  edit: LayoutEdit;
}

/**
 * The queue a view's layout writes share.
 *
 * Per view, so two views are not serialized against each other, and stable for
 * as long as one view is open — react-query re-binds a pending mutation's
 * options, so a scope that changed under an in-flight write would leave it
 * queued behind nothing.
 */
function scopeFor(slug: string, viewId: string): string {
  return `view-layout:${slug}:${viewId}`;
}

/**
 * Store a whole layout, composed at the front of this view's write queue.
 *
 * Every layout write in a view goes through here — the pick below is one of
 * them — so they share one queue rather than racing each other: PATCH replaces
 * the layout whole, and two unserialized writes make the later one revert the
 * earlier.
 */
export function useViewLayoutEdit(slug: string, viewId: string): (edit: LayoutEdit) => void {
  const qc = useQueryClient();

  const write = useMutation({
    meta: { errorTitle: "Update view" },
    scope: { id: scopeFor(slug, viewId) },
    mutationFn: (vars: LayoutWrite) => {
      const key = viewsKeys.view(vars.slug, vars.viewId);
      const current = qc.getQueryData<ViewSummary>(key);
      // The view left the cache while this write waited its turn — deleted in
      // another tab, or closed from the sidebar. There is no layout to PATCH
      // into, and inventing one would store a view made of this single edit.
      if (current === undefined) {
        throw new Error("The view is no longer loaded, so its layout was not written.");
      }
      // The edit reads the cache's layout and returns a new one; nothing here
      // mutates the record react-query is holding and the panes are drawn from.
      return updateView(vars.slug, vars.viewId, { layout: vars.edit(current.layout) });
    },
    onSuccess: (updated, vars) => {
      // The server's record, not a refetch: the write already returned it. Under
      // the key this write's own variables name — not the one the page happens
      // to be showing now.
      qc.setQueryData(viewsKeys.view(vars.slug, vars.viewId), updated);
      qc.invalidateQueries({ queryKey: viewsKeys.views(vars.slug) });
    },
  });

  const mutate = write.mutate;
  return useCallback(
    (edit: LayoutEdit) => {
      if (slug === "" || viewId === "") return;
      mutate({ slug, viewId, edit });
    },
    [mutate, slug, viewId],
  );
}

export function useViewLayoutWrite(
  slug: string,
  viewId: string,
): (containerId: string, primitiveId: string) => void {
  const edit = useViewLayoutEdit(slug, viewId);

  return useCallback(
    (containerId: string, primitiveId: string) => {
      const container = newContainerFor(primitiveId);
      // The registry does not know this id — a stale option, or a build that
      // dropped the primitive. Posting `undefined` would be a 422.
      if (container === undefined) return;
      edit((layout) => {
        const containers = asJson(layout.containers) ?? {};
        // The pick *replaces* the container: a primitive merged into the
        // placeholder leaves the old `kind` behind, which the host refuses.
        return { ...layout, containers: { ...containers, [containerId]: container } };
      });
    },
    [edit],
  );
}
