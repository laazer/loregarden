/**
 * Putting a primitive into a view from a page that is not that view.
 *
 * `useViewLayoutEdit` cannot do this. It composes its PATCH from the record
 * react-query is holding for the view being edited, and refuses when that record
 * is absent — correctly, because inventing a layout would store a view made of
 * one edit. From the Dashboard or the Queue page the target view is not loaded
 * at all, so the layout has to be *fetched* before it can be added to.
 *
 * ## The window this leaves, stated rather than hidden
 *
 * PATCH replaces a layout whole, so read-modify-write can clobber a concurrent
 * edit made in another tab. The read happens inside the mutation, immediately
 * before the write, which makes the window as small as a client can make it —
 * it does not close it. The same is true of every layout write in this app; the
 * difference here is only that the read is explicit. A view being edited in
 * another tab while you add a pane to it from a third page is the case that
 * loses, and it loses the way the rest of the app already does.
 *
 * ## Two destinations, one shape
 *
 * A new tab is a create with a one-pane layout; an existing tab is a fetch, an
 * append and a PATCH. Both end with the view's id, so a caller can navigate to
 * what it just made without knowing which happened.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { withPrimitivePane } from "../lib/addPrimitivePane";
import { emptyLayoutFor } from "../lib/viewLayouts";
import {
  createView,
  fetchView,
  fetchViews,
  updateView,
  viewsKeys,
  type ViewSummary,
} from "../lib/viewsApi";

/** What a caller asks for: a primitive, and the settings that make it concrete. */
export interface PrimitivePlacement {
  primitiveId: string;
  /**
   * The settings the surface already knows — the ticket it is showing, the lane
   * it is drawing. A `Map`, matching `containerWithSettings`, because a key here
   * is compared against declared field keys and `"key" in plainObject` answers
   * true for `constructor`.
   */
  values: ReadonlyMap<string, unknown>;
  /** The tab's title when this creates one. */
  title: string;
}

export interface AddPrimitiveToTab {
  /** Existing tabs, for the menu to list. Empty until they load. */
  views: ViewSummary[];
  isLoading: boolean;
  /** Add to an existing view; resolves with the view so a caller can navigate. */
  addToView: (viewId: string, placement: PrimitivePlacement) => Promise<ViewSummary>;
  /** Create a tab holding it. */
  addToNewView: (placement: PrimitivePlacement) => Promise<ViewSummary>;
  isWriting: boolean;
}

export function useAddPrimitiveToTab(slug: string, enabled = true): AddPrimitiveToTab {
  const qc = useQueryClient();

  const views = useQuery({
    queryKey: viewsKeys.views(slug),
    queryFn: () => fetchViews(slug),
    enabled: enabled && slug !== "",
  });

  function refresh() {
    void qc.invalidateQueries({ queryKey: viewsKeys.views(slug) });
    void qc.invalidateQueries({ queryKey: viewsKeys.sidebarEntries(slug) });
  }

  const addExisting = useMutation({
    meta: { errorTitle: "Add to tab" },
    mutationFn: async (vars: { viewId: string; placement: PrimitivePlacement }) => {
      // Fetched, not read from cache: a list entry carries the layout as it was
      // when the list was fetched, and appending to a stale one drops whatever
      // was added in between.
      const current = await fetchView(slug, vars.viewId);
      const layout = withPrimitivePane(
        current.layout,
        vars.placement.primitiveId,
        vars.placement.values,
      );
      return updateView(slug, vars.viewId, { layout });
    },
    onSuccess: (record) => {
      qc.setQueryData<ViewSummary>(viewsKeys.view(slug, record.id), record);
      refresh();
    },
  });

  const addNew = useMutation({
    meta: { errorTitle: "Add to a new tab" },
    mutationFn: async (placement: PrimitivePlacement) => {
      const layout = withPrimitivePane(
        // A grid, not a canvas: this is one pane, and the grid's empty seed is
        // the layout `withPrimitivePane` fills rather than adds beside.
        emptyLayoutFor("flex_grid"),
        placement.primitiveId,
        placement.values,
      );
      return createView(slug, { title: placement.title, icon: "", layout });
    },
    onSuccess: (record) => {
      qc.setQueryData<ViewSummary>(viewsKeys.view(slug, record.id), record);
      refresh();
    },
  });

  return {
    views: views.data ?? [],
    isLoading: enabled && slug !== "" && views.isLoading,
    addToView: (viewId, placement) => addExisting.mutateAsync({ viewId, placement }),
    addToNewView: (placement) => addNew.mutateAsync(placement),
    isWriting: addExisting.isPending || addNew.isPending,
  };
}
