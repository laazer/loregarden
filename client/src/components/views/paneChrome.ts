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
import type { RegisteredPrimitive } from "./primitives/types";

/**
 * The class a compact icon-only button carries.
 *
 * Named for what it is rather than where it sits: a pane header uses it, and so
 * does the canvas toolbar's zoom pair, which is an icon-only control that is not
 * in a header.
 */
export const ICON_BUTTON = "btn-secondary btn-compact btn-icon-only";

/**
 * What a header calls a container that has not been given a primitive yet.
 *
 * Exported because the header also *styles* that state — an unconfigured pane
 * says so in its title, not only in its body — and a second spelling of the
 * string in the header is a comparison that silently stops matching the day this
 * one is reworded.
 */
export const EMPTY_PANE_TITLE = "Empty pane";

/**
 * The registry entry a stored container names, when this build still has one.
 *
 * `undefined` covers three different panes that a header treats the same way:
 * one that has not picked a primitive yet, one whose stored id is not a string,
 * and one written by a build that had a primitive this one does not. None of
 * them has a name to show or a schema to edit.
 */
export function panePrimitive(container: unknown): RegisteredPrimitive | undefined {
  const settings = asJson(asJson(container)?.settings) ?? {};
  const primitiveId = settings.primitive_id;
  if (typeof primitiveId !== "string" || primitiveId === "") return undefined;
  return getPrimitive(primitiveId);
}

/**
 * What a header calls this pane: the registry's name for what it holds.
 *
 * Named by the registry, never spelled here — a header holding its own copy of
 * "Terminal" goes stale the moment the entry is renamed.
 */
export function paneTitle(container: unknown): string {
  const settings = asJson(asJson(container)?.settings) ?? {};
  const primitiveId = typeof settings.primitive_id === "string" ? settings.primitive_id : "";
  if (primitiveId === "") return EMPTY_PANE_TITLE;
  return getPrimitive(primitiveId)?.displayName ?? "Unknown contents";
}
