import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { PrimitiveFrameContext, primitiveSize } from "./primitiveFrame";
import "./PrimitiveCard.css";
import { useDialogFocusTrap } from "../../../hooks/useDialogFocusTrap";

/** Wraps one primitive so it can claim more room than the chat measure allows,
 *  and — for breakout tiers — hoist itself into a full-viewport overlay. */
export function PrimitiveSlot({ kind, children }: { kind: string; children: ReactNode }) {
  const dialogRef = useDialogFocusTrap<HTMLDivElement>();
  const size = primitiveSize(kind);
  const canExpand = size !== "regular";
  const [expanded, setExpanded] = useState(false);
  const [reservedHeight, setReservedHeight] = useState<number | null>(null);
  const slotRef = useRef<HTMLDivElement>(null);

  const toggleExpanded = useCallback(() => {
    if (!expanded) setReservedHeight(slotRef.current?.offsetHeight ?? null);
    setExpanded(!expanded);
  }, [expanded]);

  useEffect(() => {
    if (!expanded) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [expanded]);

  const frame = useMemo(
    () => ({ size, canExpand, expanded, toggleExpanded }),
    [size, canExpand, expanded, toggleExpanded],
  );

  return (
    <PrimitiveFrameContext.Provider value={frame}>
      <div
        ref={slotRef}
        className={`lg-primitive-slot lg-primitive-slot--${size}`}
        // Hold the thread's scroll position while the card lives in the overlay.
        style={expanded && reservedHeight ? { minHeight: reservedHeight } : undefined}
      >
        {expanded
          ? createPortal(
              <div
                ref={dialogRef}
                className="lg-primitive-overlay"
                role="dialog"
                aria-modal="true"
                onMouseDown={(event) => {
                  if (event.target === event.currentTarget) setExpanded(false);
                }}
              >
                <div className="lg-primitive-overlay-inner">{children}</div>
              </div>,
              document.body,
            )
          : children}
      </div>
    </PrimitiveFrameContext.Provider>
  );
}
