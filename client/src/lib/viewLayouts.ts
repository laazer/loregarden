/**
 * The layouts the client composes: the seed a new view is created with, and the
 * copy a duplicate posts.
 *
 * Both are pure, and both are shaped by `parse_view_layout` server-side:
 *
 *   - **An empty flex grid is not an empty registry.** `FlexGridLayout` requires
 *     a root, a leaf root must name a container, and the structure walk refuses
 *     any container nothing references — so the smallest grid the server accepts
 *     is exactly one container that has not chosen a primitive yet. A canvas has
 *     no required root, so its empty form really is empty; the two are not
 *     symmetric.
 *   - **Duplicating regenerates ids.** Container ids are keys of the layout's own
 *     registry, so reusing them across views is accepted by the server and only
 *     shows up later, when the two views alias each other in every cache keyed by
 *     container id. Node and item ids are regenerated with them, and every
 *     reference is rewritten to match.
 *
 * Nothing here mutates its input: the layout handed in is the record react-query
 * is holding, and the pane on screen is rendered from it.
 */

import type { ViewKind, ViewLayout } from "./viewsApi";

type Json = Record<string, unknown>;

/**
 * A fresh id, unique within a session.
 *
 * The counter carries the uniqueness; the random suffix keeps two browser tabs
 * from minting the same container id for two different views, which the server
 * would accept and every container-keyed cache would then alias.
 *
 * Exported because every arrangement editor mints ids on the same terms — the
 * grid's split (440) and the canvas's drop (442) both add a node and a container
 * — and a second generator is a second uniqueness argument to keep in step.
 */
let idCounter = 0;

export function freshId(prefix: string): string {
  idCounter += 1;
  return `${prefix}-${idCounter.toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/**
 * A value narrowed to a JSON object, or `undefined`.
 *
 * Every reader of a stored layout needs this — the blob is `unknown` by
 * contract, and an array or a `null` reaching a `Record` read is the bug the
 * narrowing exists to stop. It lives here, with the other layout reasoning,
 * rather than being re-typed in each renderer.
 */
export function asJson(value: unknown): Json | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
  return value as Json;
}

/**
 * The layout a newly created view starts life with.
 *
 * A fresh object every call: a module-level constant handed out by reference
 * gives every new grid the same container id, and the first in-place edit of one
 * view's layout changes what the next `New View` posts.
 */
export function emptyLayoutFor(kind: ViewKind): ViewLayout {
  if (kind === "canvas") {
    return { kind: "canvas", containers: {}, items: [] };
  }
  const containerId = freshId("c");
  return {
    kind: "flex_grid",
    // No `primitive_id` at all — not an empty string, which the primitive host
    // reads as a stored id it cannot resolve. The absence is what makes the pane
    // render its picker prompt.
    containers: { [containerId]: { kind: "panel", settings: {} } },
    root: { node: "leaf", id: freshId("n"), size: 1, container_id: containerId },
  };
}

function duplicateNode(value: unknown, containerIds: Map<string, string>): unknown {
  const node = asJson(value);
  if (node === undefined) return value;
  if (node.node === "split") {
    const children = Array.isArray(node.children) ? node.children : [];
    return {
      ...node,
      id: freshId("n"),
      children: children.map((child) => duplicateNode(child, containerIds)),
    };
  }
  const containerId = node.container_id;
  return {
    ...node,
    id: freshId("n"),
    // A source that already referenced an unknown container keeps its dangling
    // reference rather than gaining a plausible-looking wrong one.
    container_id:
      typeof containerId === "string" ? (containerIds.get(containerId) ?? containerId) : containerId,
  };
}

/**
 * A deep copy of `layout` under fresh container, node and item ids.
 *
 * There is no copy endpoint: duplicate is this, re-POSTed through `createView`.
 */
export function duplicateLayout(layout: ViewLayout): ViewLayout {
  // The layout is JSON by definition — it round-trips through the wire on every
  // read — so this is both the deep copy and the guarantee that no part of the
  // source is shared with the copy.
  const source = JSON.parse(JSON.stringify(layout)) as Json;
  const sourceContainers = asJson(source.containers) ?? {};

  const containerIds = new Map<string, string>();
  const containers: Json = {};
  for (const key of Object.keys(sourceContainers)) {
    const fresh = freshId("c");
    containerIds.set(key, fresh);
    containers[fresh] = sourceContainers[key];
  }

  if (source.kind === "canvas") {
    const items = Array.isArray(source.items) ? source.items : [];
    return {
      ...source,
      containers,
      items: items.map((raw) => {
        const item = asJson(raw);
        if (item === undefined) return raw;
        const containerId = item.container_id;
        return {
          ...item,
          id: freshId("i"),
          container_id:
            typeof containerId === "string"
              ? (containerIds.get(containerId) ?? containerId)
              : containerId,
        };
      }),
    };
  }

  return { ...source, containers, root: duplicateNode(source.root, containerIds) };
}
