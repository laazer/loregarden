/**
 * Adding a primitive to a view from somewhere else in the app.
 *
 * The two arrangements each own their own append — `appendPane` splits a tree,
 * `appendItem` places on a surface — because the internals they need are
 * private to those modules and should stay that way. This is the one line that
 * knows a view has a kind at all, so a caller adding a Kanban board from the
 * Dashboard does not have to.
 *
 * The container is composed by the registry rather than assembled here, which
 * is what keeps `kind` and `primitive_id` from disagreeing: a container stored
 * as `panel` whose primitive is a terminal renders a placeholder, and
 * `containerWithSettings` is the one function that stamps both from the same
 * entry.
 */

import { appendItem } from "./canvasLayout";
import { appendPane } from "./gridLayout";
import type { ViewLayout } from "./viewsApi";
import { containerWithSettings } from "../components/views/primitives/registry";

/**
 * `layout` with a pane holding `primitiveId`, configured by `values`.
 *
 * Throws for a primitive the registry does not know — the caller named it, so
 * an unknown id is a bug in the caller rather than stored text that has aged
 * out, and returning the layout unchanged would look like a save that worked.
 */
export function withPrimitivePane(
  layout: ViewLayout,
  primitiveId: string,
  values: ReadonlyMap<string, unknown>,
): ViewLayout {
  const container = containerWithSettings(primitiveId, values);
  if (container === undefined) {
    throw new Error(`This build has no primitive called ${primitiveId}.`);
  }
  const asJsonContainer = container as unknown as Record<string, unknown>;
  return layout.kind === "canvas"
    ? appendItem(layout, asJsonContainer)
    : appendPane(layout, asJsonContainer);
}
