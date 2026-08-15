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

import { runLedgerPrimitive } from "./runLedgerPrimitive";
import { terminalPrimitive } from "./terminalPrimitive";
import type { RegisteredPrimitive } from "./types";
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
}

export function ContainerPrimitiveHost({ containerId, settings }: ContainerPrimitiveHostProps) {
  const storedId = settings.primitive_id;
  const entry = typeof storedId === "string" ? getPrimitive(storedId) : undefined;

  if (entry === undefined) {
    // A view can outlive the primitive it names — a renamed id, a container
    // written by a newer build. Say so in the pane rather than taking the whole
    // view down with an exception.
    return (
      <div data-container-id={containerId} data-primitive-unknown="true" style={HOST_STYLE}>
        <p style={{ margin: 0, padding: 16, color: "var(--txl)", fontSize: 12.5 }}>
          This container asks for a primitive this build does not have.
        </p>
      </div>
    );
  }

  const Primitive = entry.Component;
  return (
    <div data-container-id={containerId} data-primitive-id={entry.id} style={HOST_STYLE}>
      <Primitive containerId={containerId} settings={settings} />
    </div>
  );
}
