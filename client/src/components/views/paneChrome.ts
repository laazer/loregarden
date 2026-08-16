/**
 * The header a container gets, whichever arrangement is drawing it.
 *
 * Both renderers put a title and a row of icon buttons above a container — a
 * grid leaf (440) and a canvas item (442) — and both need the same two answers:
 * what this pane is called, and what an icon button in its header looks like. Two
 * copies of `getPrimitive(id)?.displayName ?? …` is two places to forget that the
 * registry, not the header, owns the name.
 *
 * The header itself is `PaneHeader`, next door. It lives in its own file rather
 * than here because a module that exports both a component and the constants
 * around it loses fast refresh for the component — so this one stays what its
 * name says: the pieces a header is assembled from.
 */

import { asJson } from "../../lib/viewLayouts";
import { getPrimitive } from "./primitives/registry";

/**
 * The class a compact icon-only button carries.
 *
 * Named for what it is rather than where it sits: a pane header uses it, and so
 * does the canvas toolbar's zoom pair, which is an icon-only control that is not
 * in a header.
 */
export const ICON_BUTTON = "btn-secondary btn-compact btn-icon-only";

/**
 * What a header calls this pane: the registry's name for what it holds.
 *
 * Named by the registry, never spelled here — a header holding its own copy of
 * "Terminal" goes stale the moment the entry is renamed.
 */
export function paneTitle(container: unknown): string {
  const settings = asJson(asJson(container)?.settings) ?? {};
  const primitiveId = typeof settings.primitive_id === "string" ? settings.primitive_id : "";
  if (primitiveId === "") return "Empty pane";
  return getPrimitive(primitiveId)?.displayName ?? "Unknown contents";
}
