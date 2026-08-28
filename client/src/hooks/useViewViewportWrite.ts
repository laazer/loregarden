/**
 * Storing where a view is being looked at — deliberately *not* a layout write.
 *
 * `useViewLayoutEdit` serializes every layout write of a view behind one queue,
 * composes each body from the cache at the front of that queue, and cancels the
 * view's in-flight reads when one lands. All three are right for an arrangement
 * edit and wrong for a pan:
 *
 *   - **A pan must not queue behind a layout edit, or a layout edit behind a
 *     pan.** Panning fires at pointer rate; through one queue it would starve
 *     the edits that actually change what the view contains. So this write has
 *     its own scope, per view.
 *   - **Losing the last pan is harmless.** Losing a layout edit is not. A
 *     viewport write carries the whole value it means to store, so a later write
 *     simply supersedes an earlier one — there is no read-modify-write to lose.
 *   - **A landing write must not disturb the layout in the cache.** The server
 *     answers with the whole record, and a layout PATCH may be in flight beside
 *     this one; writing that whole record into the cache would put the
 *     pre-edit layout back. Only the viewport is merged in.
 *
 * The caller debounces. This hook is what "write it down now" does, not when.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import { updateView, viewsKeys, type ViewSummary, type ViewViewportPatch } from "../lib/viewsApi";

interface ViewportWrite {
  slug: string;
  viewId: string;
  viewport: ViewViewportPatch;
}

/**
 * The queue this view's viewport writes share — separate from the layout's, so
 * neither waits on the other. Per view, and stable while one is open:
 * react-query re-binds a pending mutation's options, so a scope that changed
 * under an in-flight write would leave it queued behind nothing.
 */
function scopeFor(slug: string, viewId: string): string {
  return `view-viewport:${slug}:${viewId}`;
}

export function useViewViewportWrite(
  slug: string,
  viewId: string,
): (viewport: ViewViewportPatch) => void {
  const qc = useQueryClient();

  const write = useMutation({
    // A failed write costs the user the position this canvas reopens at, which
    // is invisible until the next time they open it — so it is said out loud
    // rather than swallowed. At most one per settle, not one per pan event.
    meta: { errorTitle: "Remember canvas position" },
    scope: { id: scopeFor(slug, viewId) },
    mutationFn: (vars: ViewportWrite): Promise<ViewSummary> =>
      updateView(vars.slug, vars.viewId, { viewport: vars.viewport }),
    onSuccess: (record, vars) => {
      const key = viewsKeys.view(vars.slug, vars.viewId);
      // The viewport alone, merged into whatever the cache holds now. The
      // server's record is authoritative about the field this write set and
      // stale about any layout PATCH that landed after this request was sent.
      // A view that left the cache while this was in flight is left absent
      // rather than resurrected from a viewport write.
      qc.setQueryData<ViewSummary>(key, (previous) =>
        previous === undefined ? undefined : { ...previous, viewport: record.viewport },
      );
      // The sidebar's view list is deliberately not invalidated: it lists titles
      // and icons, and refetching it on every settled pan would cost a request
      // per gesture for a value it does not draw.
    },
  });

  const mutate = write.mutate;
  return useCallback(
    (viewport: ViewViewportPatch) => {
      // Outside the view route there is no id, and a PATCH cannot be composed
      // without one.
      if (slug === "" || viewId === "") return;
      mutate({ slug, viewId, viewport });
    },
    [mutate, slug, viewId],
  );
}
