/**
 * What an operator picks from when adding a container to a view.
 *
 * Every option is derived from the registry, so a new primitive appears here
 * the moment it is registered — this module names no primitive id of its own.
 * The `entries` prop exists so a caller (and a test) can offer a subset or a
 * superset without this component knowing what is in it.
 */

import { CONTAINER_PRIMITIVES } from "./primitives/registry";
import "./paneChrome.css";
import type { RegisteredPrimitive } from "./primitives/types";

export interface PrimitivePickerProps {
  entries?: RegisteredPrimitive[];
  /**
   * What this list is offering, shown above it and used as the list's own
   * accessible name.
   *
   * A parameter rather than a constant because the two callers are asking
   * different questions: an empty pane is being *filled*, a configured one is
   * having its contents *changed*, and "Choose a primitive" over both makes the
   * second one read as if it were starting from empty.
   */
  legend: string;
  onPick: (primitiveId: string) => void;
}

export function PrimitivePicker({
  entries = CONTAINER_PRIMITIVES,
  legend,
  onPick,
}: PrimitivePickerProps) {
  return (
    <>
      <div className="primitive-picker-legend">{legend}</div>
      {/*
        The legend labels the list rather than merely sitting above it, so the
        list announces what it is offering to a screen reader as well as to an
        eye.
      */}
      <ul className="primitive-picker" aria-label={legend}>
        {entries.map((entry) => (
          <li key={entry.id}>
            <button
              type="button"
              className="primitive-option"
              data-primitive-id={entry.id}
              onClick={() => onPick(entry.id)}
            >
              {/*
                The glyph is decoration, not the name: it is bare Unicode with
                no shared metrics, so `displayName` beside it is what carries the
                meaning and the glyph stays out of the accessibility tree.
              */}
              <span className="primitive-option-glyph" aria-hidden="true">
                {entry.icon}
              </span>
              <span className="primitive-option-text">
                <span className="primitive-option-name">{entry.displayName}</span>
                <span className="primitive-option-category">{entry.category}</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}
