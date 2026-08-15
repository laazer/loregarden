/**
 * The container primitive registry, and the one dispatcher that mounts from it.
 *
 * A view stores `primitive_id` inside a container's `settings` map and nothing
 * else; `ContainerPrimitiveHost` is how that string becomes a component. Grid
 * and canvas code never names a primitive, so adding one is an entry in the
 * list below and no change anywhere else.
 *
 * The stored id is attacker-influencable text, so it is resolved through a Map
 * (never a plain-object lookup, which reaches `constructor` and `__proto__`)
 * and never through a dynamic import.
 */

import type { CSSProperties } from "react";

import { PrimitiveErrorBoundary } from "./PrimitiveErrorBoundary";
import { runLedgerPrimitive } from "./runLedgerPrimitive";
import { terminalPrimitive } from "./terminalPrimitive";
import type { ContainerKind, RegisteredPrimitive, ViewContainer } from "./types";
import { webEmbedPrimitive } from "./webEmbedPrimitive";

export const CONTAINER_PRIMITIVES: RegisteredPrimitive[] = [
  terminalPrimitive,
  runLedgerPrimitive,
  webEmbedPrimitive,
];

const BY_ID = new Map(CONTAINER_PRIMITIVES.map((primitive) => [primitive.id, primitive]));

export function getPrimitive(id: string): RegisteredPrimitive | undefined {
  return BY_ID.get(id);
}

/**
 * The container a picked primitive id becomes.
 *
 * Turning an id into a container means knowing four things about 433's wire
 * model — that a container is `{kind, settings}` with no id of its own, that
 * `primitive_id` lives *inside* `settings`, that `kind` is the entry's
 * `containerKind`, and that the rest of `settings` starts at the schema's
 * declared defaults. The picker (this ticket), the settings editor (438), the
 * grid (440) and the canvas (442) all need that, and four copies of it is four
 * chances to store a container the host then refuses. It lives here instead.
 *
 * Returns `undefined` for an id the registry does not know, on the same
 * reasoning as `getPrimitive`: the caller's id can come from stored text.
 */
export function newContainerFor(primitiveId: string): ViewContainer | undefined {
  const entry = getPrimitive(primitiveId);
  if (entry === undefined) return undefined;

  const settings: Record<string, unknown> = { primitive_id: entry.id };
  for (const field of entry.settingsFields) settings[field.key] = field.default;
  return { kind: entry.containerKind, settings };
}

/**
 * Fill the pane; do not assert a size of your own.
 *
 * `min-height: 0` is the load-bearing one: a flex child defaults to
 * `min-height: auto` and refuses to shrink below its content, which is the
 * mechanism behind almost every "it overflows when the pane is small" bug.
 */
const HOST_STYLE: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  height: "100%",
  width: "100%",
  minHeight: "0",
  minWidth: "0",
  overflow: "auto",
};

export interface ContainerPrimitiveHostProps {
  containerId: string;
  /** The container's stored settings, verbatim: snake_case and unvalidated. */
  settings: Record<string, unknown>;
  /**
   * The `kind` the container is stored under, when the caller has it.
   *
   * Optional because a caller holding only a settings map is a legitimate
   * caller, but when it is supplied it is *checked*: a container stored as
   * `kind: "panel"` whose `primitive_id` is `terminal` is a disagreement
   * between two records of the same decision, and mounting the shell anyway
   * would let the wrong one win silently.
   */
  kind?: ContainerKind;
}

function Placeholder({
  containerId,
  reason,
  children,
}: {
  containerId: string;
  reason: string;
  children: string;
}) {
  return (
    <div data-container-id={containerId} data-primitive-unknown={reason} style={HOST_STYLE}>
      <p style={{ margin: 0, padding: 16, color: "var(--txl)", fontSize: 12.5 }}>{children}</p>
    </div>
  );
}

export function ContainerPrimitiveHost({
  containerId,
  settings,
  kind,
}: ContainerPrimitiveHostProps) {
  const storedId = settings.primitive_id;
  const entry = typeof storedId === "string" ? getPrimitive(storedId) : undefined;

  if (entry === undefined) {
    // A view can outlive the primitive it names — a renamed id, a container
    // written by a newer build. Say so in the pane rather than taking the whole
    // view down with an exception.
    return (
      <Placeholder containerId={containerId} reason="true">
        This container asks for a primitive this build does not have.
      </Placeholder>
    );
  }

  if (kind !== undefined && kind !== entry.containerKind) {
    return (
      <Placeholder containerId={containerId} reason="kind-mismatch">
        This container is stored as a kind its primitive does not belong to.
      </Placeholder>
    );
  }

  const Primitive = entry.Component;
  return (
    <div data-container-id={containerId} data-primitive-id={entry.id} style={HOST_STYLE}>
      {/* One boundary here, rather than one per view kind: a primitive that
          throws must lose its own pane and nothing else. */}
      <PrimitiveErrorBoundary resetKey={`${containerId}:${entry.id}`}>
        <Primitive containerId={containerId} settings={settings} />
      </PrimitiveErrorBoundary>
    </div>
  );
}
