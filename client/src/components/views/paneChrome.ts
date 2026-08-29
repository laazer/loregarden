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
 * A stored container's settings map, narrowed.
 *
 * One spelling of `asJson(asJson(container)?.settings) ?? {}`, which the title,
 * the primitive lookup, the pane and the settings editor all need. The layout
 * blob is `unknown` by contract, and an array or a `null` reaching a `Record`
 * read is the bug the narrowing exists to stop — four times over, if each
 * reader re-derives it.
 */
export function paneSettings(container: unknown): Record<string, unknown> {
  return asJson(asJson(container)?.settings) ?? {};
}

/** The `primitive_id` a container stores, when it stores a usable one. */
export function panePrimitiveId(container: unknown): string {
  const primitiveId = paneSettings(container).primitive_id;
  return typeof primitiveId === "string" ? primitiveId : "";
}

/**
 * The registry entry a stored container names, when this build still has one.
 *
 * `undefined` covers three panes: one that has not picked a primitive yet, one
 * whose stored id is not a string, and one written by a build that had a
 * primitive this one does not. None of them has a schema to edit — which is why
 * a header with no entry offers no settings control.
 *
 * `paneTitle` deliberately does *not* collapse those three: it separates "no
 * primitive" from "a primitive this build lost", because a pane naming a
 * missing primitive is not an empty pane and must not read as one. Both are
 * built on the two lookups above, so that distinction stays a decision rather
 * than becoming a drift.
 */
export function panePrimitive(container: unknown): RegisteredPrimitive | undefined {
  const primitiveId = panePrimitiveId(container);
  if (primitiveId === "") return undefined;
  return getPrimitive(primitiveId);
}

/**
 * What a header calls this pane: the registry's name for what it holds.
 *
 * Named by the registry, never spelled here — a header holding its own copy of
 * "Terminal" goes stale the moment the entry is renamed.
 */
export function paneTitle(container: unknown): string {
  if (panePrimitiveId(container) === "") return EMPTY_PANE_TITLE;
  return panePrimitive(container)?.displayName ?? "Unknown contents";
}
