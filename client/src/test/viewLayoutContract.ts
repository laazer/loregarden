/**
 * The server's layout rules, transcribed once.
 *
 * Mirrors `server/loregarden/models/domain/view_layout.py` — `_StructureWalk`,
 * the field bounds on `LeafNode`/`SplitNode`/`CanvasItem`/`ViewContainer`, and
 * the `extra="forbid"` on every one of them. Any layout this module accepts is
 * one `parse_view_layout` accepts, and any layout it refuses is a 400 at
 * runtime.
 *
 * It lives here rather than inside a test file because three suites assert on
 * layouts the client composes — the seed, the duplicate, the create body, and
 * the container-picker PATCH — and a transcription copied four times is four
 * chances to drift from the model it is standing in for. When the server's
 * rules change, this file is the one place that has to follow.
 *
 * It throws rather than calling `expect`, so the failure names the rule that
 * was broken and the module stays usable outside a matcher.
 */

/** Sibling fractions are authored by dividing 1.0, so compare with slack. */
export const SIZE_SUM_TOLERANCE = 1e-6;

export const MAX_SPLIT_DEPTH = 32;
export const MAX_CONTAINERS = 256;
export const MAX_LAYOUT_NODES = 512;
export const MAX_CANVAS_EXTENT = 1_000_000;
export const MAX_CANVAS_COORDINATE = 10_000_000;

/** `ViewKind` — closed, and the discriminator that selects the arrangement. */
export const VIEW_KINDS = ["flex_grid", "canvas"] as const;
/** `ContainerKind` — closed. The panel's *primitive* is a separate vocabulary. */
export const CONTAINER_KINDS = ["terminal", "panel", "web_embed"] as const;
export const SPLIT_ORIENTATIONS = ["horizontal", "vertical"] as const;

type Json = Record<string, unknown>;

function fail(where: string, message: string): never {
  throw new Error(`Layout the server would refuse (${where}): ${message}`);
}

function asObject(value: unknown, where: string): Json {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    fail(where, `expected an object, got ${JSON.stringify(value)}`);
  }
  return value as Json;
}

/** `extra="forbid"`, plus the required/optional split each model declares. */
function checkFields(value: Json, required: string[], optional: string[], where: string) {
  for (const key of required) {
    if (!(key in value)) fail(where, `missing required field \`${key}\``);
  }
  const allowed = new Set([...required, ...optional]);
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) fail(where, `extra field \`${key}\` (the model is extra="forbid")`);
  }
}

function checkNonEmptyString(value: unknown, where: string): string {
  if (typeof value !== "string" || value.length === 0) {
    fail(where, `expected a non-empty string, got ${JSON.stringify(value)}`);
  }
  return value;
}

function checkFiniteInRange(
  value: unknown,
  where: string,
  low: number,
  high: number,
  lowInclusive: boolean,
) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    fail(where, `expected a finite number, got ${JSON.stringify(value)} (allow_inf_nan=False)`);
  }
  const lowOk = lowInclusive ? value >= low : value > low;
  if (!lowOk || value > high) {
    fail(where, `${value} is outside ${lowInclusive ? "[" : "("}${low}, ${high}]`);
  }
}

/** `NodeSize`: gt=0, le=1.0, finite. Defaulted to 1.0 when absent. */
function checkNodeSize(node: Json, where: string): number {
  if (!("size" in node)) return 1;
  checkFiniteInRange(node.size, `${where}.size`, 0, 1, false);
  return node.size as number;
}

/** `_StructureWalk`, with the counting it does. */
class StructureWalk {
  readonly known: Set<string>;
  readonly nodeIds = new Set<string>();
  readonly referenced = new Set<string>();

  constructor(containers: Json) {
    this.known = new Set(Object.keys(containers));
  }

  claimNode(nodeId: string) {
    if (this.nodeIds.has(nodeId)) fail("structure", `duplicate node id: ${nodeId}`);
    if (this.nodeIds.size >= MAX_LAYOUT_NODES) {
      fail("structure", `more than ${MAX_LAYOUT_NODES} nodes`);
    }
    this.nodeIds.add(nodeId);
  }

  claimContainer(containerId: string) {
    if (!this.known.has(containerId)) {
      fail("structure", `node references unknown container: ${containerId}`);
    }
    if (this.referenced.has(containerId)) {
      fail("structure", `container placed more than once: ${containerId}`);
    }
    this.referenced.add(containerId);
  }

  finish() {
    const orphaned = [...this.known].filter((id) => !this.referenced.has(id));
    if (orphaned.length > 0) {
      fail("structure", `container(s) no arrangement references: ${orphaned.sort().join(", ")}`);
    }
  }
}

function checkContainers(value: unknown): Json {
  const containers = asObject(value, "containers");
  const keys = Object.keys(containers);
  if (keys.length > MAX_CONTAINERS) fail("containers", `more than ${MAX_CONTAINERS} containers`);
  for (const key of keys) {
    // `ContainerId` is `min_length=1` on the registry's *key*.
    checkNonEmptyString(key, "containers key");
    const container = asObject(containers[key], `containers[${key}]`);
    checkFields(container, ["kind"], ["settings"], `containers[${key}]`);
    if (!(CONTAINER_KINDS as readonly string[]).includes(container.kind as string)) {
      fail(`containers[${key}]`, `kind ${JSON.stringify(container.kind)} is not a ContainerKind`);
    }
    if ("settings" in container) asObject(container.settings, `containers[${key}].settings`);
  }
  return containers;
}

function walkNode(node: Json, walk: StructureWalk, depth: number, where: string) {
  const tag = node.node;
  if (tag === "leaf") {
    checkFields(node, ["node", "id", "container_id"], ["size"], where);
    walk.claimNode(checkNonEmptyString(node.id, `${where}.id`));
    checkNodeSize(node, where);
    walk.claimContainer(checkNonEmptyString(node.container_id, `${where}.container_id`));
    return;
  }
  if (tag !== "split") {
    fail(where, `node discriminator ${JSON.stringify(tag)} is neither "leaf" nor "split"`);
  }

  checkFields(node, ["node", "id", "orientation", "children"], ["size"], where);
  // Depth is claimed before the node, exactly as `SplitNode.walk_structure` does.
  if (depth >= MAX_SPLIT_DEPTH) fail(where, `split nesting deeper than ${MAX_SPLIT_DEPTH}`);
  walk.claimNode(checkNonEmptyString(node.id, `${where}.id`));
  checkNodeSize(node, where);
  if (!(SPLIT_ORIENTATIONS as readonly string[]).includes(node.orientation as string)) {
    fail(where, `orientation ${JSON.stringify(node.orientation)} is not a SplitOrientation`);
  }

  const children = node.children;
  if (!Array.isArray(children) || children.length === 0) {
    fail(where, "a split needs at least one child (min_length=1)");
  }
  let total = 0;
  children.forEach((child, index) => {
    total += checkNodeSize(asObject(child, `${where}.children[${index}]`), `${where}.children[${index}]`);
  });
  if (Math.abs(total - 1) > SIZE_SUM_TOLERANCE) {
    fail(where, `sibling sizes sum to ${total}, not 1.0`);
  }
  children.forEach((child, index) => {
    walkNode(child as Json, walk, depth + 1, `${where}.children[${index}]`);
  });
}

/**
 * Throw unless `parse_view_layout` would accept this payload.
 *
 * The kind is checked against the closed `ViewKind` set first: the server
 * dispatches on the discriminator and refuses a tag it does not know, so a
 * checker that treated "not flex_grid" as "canvas" would bless a layout the
 * server rejects outright.
 */
export function assertServerAcceptableLayout(value: unknown): void {
  const layout = asObject(value, "layout");
  const kind = layout.kind;
  if (!(VIEW_KINDS as readonly string[]).includes(kind as string)) {
    fail("layout", `kind ${JSON.stringify(kind)} is not a ViewKind (the discriminator is closed)`);
  }

  if (kind === "flex_grid") {
    checkFields(layout, ["kind", "containers", "root"], [], "layout");
    const containers = checkContainers(layout.containers);
    const root = asObject(layout.root, "layout.root");
    // `ROOT_SIZE` is not a default a caller may override.
    if (Math.abs(checkNodeSize(root, "layout.root") - 1) > SIZE_SUM_TOLERANCE) {
      fail("layout.root", `the root node's size must be 1.0, not ${root.size}`);
    }
    const walk = new StructureWalk(containers);
    walkNode(root, walk, 0, "layout.root");
    walk.finish();
    return;
  }

  checkFields(layout, ["kind", "containers", "items"], [], "layout");
  const containers = checkContainers(layout.containers);
  const items = layout.items;
  if (!Array.isArray(items)) fail("layout.items", "expected a list");
  const walk = new StructureWalk(containers);
  items.forEach((raw, index) => {
    const where = `layout.items[${index}]`;
    const item = asObject(raw, where);
    checkFields(item, ["id", "container_id", "x", "y", "width", "height"], ["z_index"], where);
    walk.claimNode(checkNonEmptyString(item.id, `${where}.id`));
    walk.claimContainer(checkNonEmptyString(item.container_id, `${where}.container_id`));
    checkFiniteInRange(item.x, `${where}.x`, -MAX_CANVAS_COORDINATE, MAX_CANVAS_COORDINATE, true);
    checkFiniteInRange(item.y, `${where}.y`, -MAX_CANVAS_COORDINATE, MAX_CANVAS_COORDINATE, true);
    checkFiniteInRange(item.width, `${where}.width`, 0, MAX_CANVAS_EXTENT, false);
    checkFiniteInRange(item.height, `${where}.height`, 0, MAX_CANVAS_EXTENT, false);
    if ("z_index" in item && !Number.isInteger(item.z_index)) {
      fail(`${where}.z_index`, `expected an int, got ${JSON.stringify(item.z_index)}`);
    }
  });
  walk.finish();
}
