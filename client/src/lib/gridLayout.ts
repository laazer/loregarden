/**
 * The edits a flex grid makes to its own stored layout: splitting a pane,
 * closing one, and moving a divider.
 *
 * Pure and free of React, because every one of them is arithmetic the server
 * validates and the renderer only triggers — `parse_view_layout` refuses a
 * sibling set that does not sum to 1.0, a root whose size is not 1.0, a split
 * nested deeper than `MAX_SPLIT_DEPTH`, a layout holding more than
 * `MAX_CONTAINERS` containers or `MAX_LAYOUT_NODES` nodes, and a container
 * nothing references. Each function below therefore produces a layout that
 * satisfies all of them or throws, rather than a layout the write path discovers
 * is a 400. Only splitting can grow a layout, so only `splitLeaf` has the
 * cardinality checks to make.
 *
 * `MAX_LAYOUT_BYTES` is deliberately not among them: a layout at maximum
 * cardinality serializes to roughly a fifth of it, so no sequence of splits can
 * reach the cap, and a local check for it would be dead code claiming coverage.
 *
 * Two shapes are load-bearing:
 *
 *   - **The stored tree is read into a model before it is edited.** A layout
 *     arrives as `unknown` JSON; editing it by index into `Record<string,
 *     unknown>` is how a missing `size` becomes `NaN` and a 422. Reading it once
 *     into `GridNodeModel` puts every narrowing in one place, and writing it back
 *     is total.
 *   - **Nothing is mutated.** The layout handed in is the record react-query is
 *     holding, and the panes on screen are rendered from it.
 */

import { asJson, freshId } from "./viewLayouts";
import type { ViewLayout } from "./viewsApi";

type Json = Record<string, unknown>;

/**
 * `SplitNode.walk_structure` refuses a split at this depth or deeper, counting
 * the root as depth 0 — so a leaf at depth 32 cannot become a split.
 */
export const MAX_SPLIT_DEPTH = 32;

/** `ContainerRegistry`'s `max_length`. One container per pane, so: panes. */
export const MAX_CONTAINERS = 256;

/**
 * `_StructureWalk.claim_node`'s ceiling, counting leaves and splits alike — so a
 * grid runs out of nodes before it runs out of containers only in a tree that is
 * mostly interior splits.
 */
export const MAX_LAYOUT_NODES = 512;

/** The server's `SplitOrientation`. Not `row`/`column`, which is a 422. */
export type SplitOrientation = "horizontal" | "vertical";

export interface GridLeaf {
  node: "leaf";
  id: string;
  size: number;
  container_id: string;
}

export interface GridSplit {
  node: "split";
  id: string;
  size: number;
  orientation: SplitOrientation;
  children: GridNodeModel[];
}

export type GridNodeModel = GridLeaf | GridSplit;

/** The container a freshly opened pane holds: no `primitive_id`, so it prompts. */
function emptyContainer(): Json {
  return { kind: "panel", settings: {} };
}

function containersOf(layout: ViewLayout): Json {
  return asJson(layout.containers) ?? {};
}

/**
 * `size` on a node, or the share it would have among `siblings` of it.
 *
 * The field is optional server-side (it defaults to 1.0), so a stored tree may
 * legitimately omit it — and a renderer that read `undefined` as `0` would give
 * the pane no width at all.
 */
function sizeOf(node: Json, siblings: number): number {
  const size = node.size;
  if (typeof size !== "number" || !Number.isFinite(size) || size <= 0) return 1 / siblings;
  return size;
}

function parseNode(value: unknown, siblings: number): GridNodeModel | undefined {
  const data = asJson(value);
  if (data === undefined) return undefined;
  const id = typeof data.id === "string" ? data.id : "";
  if (id === "") return undefined;
  const size = sizeOf(data, siblings);

  if (data.node === "split") {
    const raw = Array.isArray(data.children) ? data.children : [];
    const children: GridNodeModel[] = [];
    for (const child of raw) {
      const parsed = parseNode(child, raw.length);
      if (parsed !== undefined) children.push(parsed);
    }
    if (children.length === 0) return undefined;
    return {
      node: "split",
      id,
      size,
      orientation: data.orientation === "vertical" ? "vertical" : "horizontal",
      children,
    };
  }

  const containerId = typeof data.container_id === "string" ? data.container_id : "";
  if (containerId === "") return undefined;
  return { node: "leaf", id, size, container_id: containerId };
}

/** The stored arrangement as a model, or `undefined` when it is not one. */
export function readGridTree(layout: ViewLayout): GridNodeModel | undefined {
  return parseNode(layout.root, 1);
}

function nodeToJson(node: GridNodeModel): Json {
  if (node.node === "split") {
    return {
      node: "split",
      id: node.id,
      size: node.size,
      orientation: node.orientation,
      children: node.children.map(nodeToJson),
    };
  }
  return { node: "leaf", id: node.id, size: node.size, container_id: node.container_id };
}

/**
 * The same node under a different size.
 *
 * A helper rather than an inline spread because spreading a discriminated union
 * loses the discriminator's narrowing, and the two branches are the only place
 * the compiler can still see which node it is holding. Exported because a
 * renderer drawing a drag in progress needs the same thing of a node it is about
 * to hand to a child.
 */
export function withSize(node: GridNodeModel, size: number): GridNodeModel {
  if (node.node === "split") return { ...node, size };
  return { ...node, size };
}

/** Siblings scaled back to a sum of 1.0 — the rule `_StructureWalk` enforces. */
function renormalized(nodes: GridNodeModel[]): GridNodeModel[] {
  const total = nodes.reduce((sum, node) => sum + node.size, 0);
  if (total <= 0) return nodes.map((node) => withSize(node, 1 / nodes.length));
  return nodes.map((node) => withSize(node, node.size / total));
}

/** Every node in the subtree, splits included — what `claim_node` counts. */
function countNodes(node: GridNodeModel): number {
  if (node.node === "leaf") return 1;
  return node.children.reduce((total, child) => total + countNodes(child), 1);
}

function findLeaf(node: GridNodeModel, nodeId: string): GridLeaf | undefined {
  if (node.node === "leaf") return node.id === nodeId ? node : undefined;
  for (const child of node.children) {
    const found = findLeaf(child, nodeId);
    if (found !== undefined) return found;
  }
  return undefined;
}

/**
 * The layout with `tree` as its arrangement.
 *
 * The root's size is stamped rather than carried: `ROOT_SIZE` is not a default a
 * caller may override, and a survivor promoted to the root arrives holding the
 * fraction it had as a child.
 */
function storeTree(layout: ViewLayout, tree: GridNodeModel, containers: Json): ViewLayout {
  return { ...layout, containers, root: { ...nodeToJson(tree), size: 1 } };
}

function requireTree(layout: ViewLayout): GridNodeModel {
  const tree = readGridTree(layout);
  if (tree === undefined) {
    throw new Error("This view's layout could not be read, so it was left unchanged.");
  }
  return tree;
}

/**
 * `node` with the leaf `nodeId` replaced by whatever `make` returns for it, or
 * `undefined` when this subtree does not hold that leaf.
 *
 * `make` is handed the leaf's depth because the one caller that needs it — the
 * split — is refused at `MAX_SPLIT_DEPTH`, and the depth is only known here.
 */
function replaceLeaf(
  node: GridNodeModel,
  nodeId: string,
  depth: number,
  make: (leaf: GridLeaf, depth: number) => GridNodeModel,
): GridNodeModel | undefined {
  if (node.node === "leaf") return node.id === nodeId ? make(node, depth) : undefined;
  for (let index = 0; index < node.children.length; index += 1) {
    const replaced = replaceLeaf(node.children[index], nodeId, depth + 1, make);
    if (replaced === undefined) continue;
    const children = [...node.children];
    // The replacement stands where the old node stood, so it keeps its slot.
    children[index] = withSize(replaced, node.children[index].size);
    return { ...node, children };
  }
  return undefined;
}

function mapSplit(
  node: GridNodeModel,
  splitId: string,
  make: (split: GridSplit) => GridNodeModel,
): GridNodeModel | undefined {
  if (node.node === "leaf") return undefined;
  if (node.id === splitId) return make(node);
  for (let index = 0; index < node.children.length; index += 1) {
    const replaced = mapSplit(node.children[index], splitId, make);
    if (replaced === undefined) continue;
    const children = [...node.children];
    children[index] = withSize(replaced, node.children[index].size);
    return { ...node, children };
  }
  return undefined;
}

/**
 * Split the leaf `nodeId` in two, along `orientation`.
 *
 * The split stands where the leaf stood and takes its slot; the leaf keeps its
 * contents and its node id, and the pane that appears holds a container that has
 * not chosen a primitive yet. Both halves are 0.5 *of that slot*, which is what
 * the sibling-sum rule measures.
 *
 * Throws when the result would nest deeper, hold more panes, or hold more nodes
 * than the server accepts — a refusal the user sees, rather than a PATCH that
 * comes back 400 as a silent autosave. One split adds one container and two
 * nodes: the split that stands where the leaf stood, and the pane it opens.
 */
export function splitLeaf(
  layout: ViewLayout,
  nodeId: string,
  orientation: SplitOrientation,
): ViewLayout {
  const tree = requireTree(layout);
  const addedContainerId = freshId("c");
  const containerCount = Object.keys(containersOf(layout)).length;
  const nodeCount = countNodes(tree);

  const replaced = replaceLeaf(tree, nodeId, 0, (leaf, depth) => {
    if (depth >= MAX_SPLIT_DEPTH) {
      throw new Error(`A pane cannot be split more than ${MAX_SPLIT_DEPTH} levels deep.`);
    }
    if (containerCount + 1 > MAX_CONTAINERS) {
      throw new Error(`A view cannot hold more than ${MAX_CONTAINERS} panes.`);
    }
    if (nodeCount + 2 > MAX_LAYOUT_NODES) {
      throw new Error(`A view cannot hold more than ${MAX_LAYOUT_NODES} layout nodes.`);
    }
    return {
      node: "split",
      id: freshId("n"),
      size: leaf.size,
      orientation,
      children: [
        { ...leaf, size: 0.5 },
        { node: "leaf", id: freshId("n"), size: 0.5, container_id: addedContainerId },
      ],
    };
  });
  if (replaced === undefined) throw new Error("The pane that was split is no longer in this view.");

  return storeTree(layout, replaced, {
    ...containersOf(layout),
    [addedContainerId]: emptyContainer(),
  });
}

/**
 * The split without the leaf `nodeId`, or `undefined` when removing it empties
 * the split entirely.
 *
 * A split left holding one child is not a split: the survivor is promoted into
 * the slot the split occupied, and `withSize(rest[0], split.size)` is what
 * "promoted into the slot" means here — the survivor stops carrying the fraction
 * it held *inside* the split and carries the split's own instead.
 *
 * That is this function's local contract and not the guarantee that siblings sum
 * to 1.0: whatever size a returned node carries is re-stamped downstream — by
 * this function's own recursive branch below, which applies the child's slot
 * size to whatever came back, and by `storeTree`, which stamps the root at
 * exactly 1.0 for the one node no slot covers. Those are what the sum survives,
 * and neither is this line. This line is what
 * keeps the size *meaningful* while it is in flight, so a reader of the model
 * mid-collapse is not looking at a fraction of a parent that no longer exists.
 */
function withoutLeaf(split: GridSplit, nodeId: string): GridNodeModel | undefined {
  const index = split.children.findIndex((child) => findLeaf(child, nodeId) !== undefined);
  if (index < 0) return split;

  const child = split.children[index];
  let rest: GridNodeModel[];
  if (child.node === "leaf") {
    rest = split.children.filter((_, at) => at !== index);
  } else {
    const replaced = withoutLeaf(child, nodeId);
    rest =
      replaced === undefined
        ? split.children.filter((_, at) => at !== index)
        : split.children.map((sibling, at) =>
            at === index ? withSize(replaced, child.size) : sibling,
          );
  }

  if (rest.length === 0) return undefined;
  if (rest.length === 1) return withSize(rest[0], split.size);
  return { ...split, children: renormalized(rest) };
}

/**
 * Close the pane `nodeId`, and drop the container it held.
 *
 * The container key goes with the node: a container no arrangement references is
 * a 422, not a harmless leftover. Closing the *last* pane resets its container to
 * the empty one instead of deleting the root — a grid needs a root and a leaf
 * root needs a container, so there is no empty grid to write.
 */
export function closeLeaf(layout: ViewLayout, nodeId: string): ViewLayout {
  const tree = requireTree(layout);
  const leaf = findLeaf(tree, nodeId);
  if (leaf === undefined) throw new Error("The pane that was closed is no longer in this view.");

  const remaining = tree.node === "leaf" ? undefined : withoutLeaf(tree, nodeId);
  if (remaining === undefined) {
    return storeTree(layout, leaf, { [leaf.container_id]: emptyContainer() });
  }

  const containers = { ...containersOf(layout) };
  delete containers[leaf.container_id];
  return storeTree(layout, remaining, containers);
}

/** The layout with `splitId`'s children resized, in order. */
export function resizeSplit(layout: ViewLayout, splitId: string, sizes: number[]): ViewLayout {
  const tree = requireTree(layout);
  const replaced = mapSplit(tree, splitId, (split) => {
    if (sizes.length !== split.children.length) {
      throw new Error("This split changed while it was being resized, so it was left alone.");
    }
    return {
      ...split,
      children: split.children.map((child, index) => withSize(child, sizes[index])),
    };
  });
  if (replaced === undefined) {
    throw new Error("The divider that moved is no longer in this view.");
  }
  return storeTree(layout, replaced, containersOf(layout));
}
