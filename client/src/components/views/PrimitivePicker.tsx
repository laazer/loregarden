/**
 * What an operator picks from when adding a container to a view.
 *
 * Every option is derived from the registry, so a new primitive appears here
 * the moment it is registered — this module names no primitive id of its own.
 * The `entries` prop exists so a caller (and a test) can offer a subset or a
 * superset without this component knowing what is in it.
 */

import { CONTAINER_PRIMITIVES } from "./primitives/registry";
import type { RegisteredPrimitive } from "./primitives/types";

export interface PrimitivePickerProps {
  entries?: RegisteredPrimitive[];
  onPick: (primitiveId: string) => void;
}

export function PrimitivePicker({ entries = CONTAINER_PRIMITIVES, onPick }: PrimitivePickerProps) {
  return (
    <ul
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: 8,
        margin: 0,
        padding: 0,
        listStyle: "none",
        minWidth: "0",
      }}
    >
      {entries.map((entry) => (
        <li key={entry.id}>
          <button
            type="button"
            className="list-btn"
            data-primitive-id={entry.id}
            onClick={() => onPick(entry.id)}
            style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}
          >
            <span aria-hidden="true" style={{ fontFamily: "var(--mono)" }}>
              {entry.icon}
            </span>
            <span>{entry.displayName}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}
