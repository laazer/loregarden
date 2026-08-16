/**
 * The header a container gets, whichever arrangement is drawing it.
 *
 * Both renderers put a title and a row of icon buttons above a container — a
 * grid leaf (440) and a canvas item (442) — and both need the same two answers:
 * what this pane is called, and what an icon button in its header looks like. Two
 * copies of `getPrimitive(id)?.displayName ?? …` is two places to forget that the
 * registry, not the header, owns the name.
 */

import { asJson } from "../../lib/viewLayouts";
import { getPrimitive } from "./primitives/registry";

/** The class every header icon button carries. */
export const HEADER_BUTTON = "btn-secondary btn-compact btn-icon-only";

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
