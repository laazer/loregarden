import { createContext, useContext } from "react";

import type { PrimitiveKind } from "./types";

/** How much of the chat surface a primitive may claim. `regular` stays inside the
 *  reading measure; `wide` and `full` break out of it where the surface allows. */
export type PrimitiveSize = "regular" | "wide" | "full";

export const PRIMITIVE_SIZES: Record<PrimitiveKind, PrimitiveSize> = {
  text: "regular",
  thinking: "regular",
  ticket: "regular",
  ticket_workflow: "wide",
  parent_ticket: "wide",
  ticket_list: "wide",
  status_column: "regular",
  kanban: "full",
  filterable_kanban: "full",
  agent: "regular",
  workflow: "full",
  gate: "regular",
  terminal: "wide",
  edit: "wide",
  calendar: "full",
  calendar_event: "regular",
  workspace: "regular",
  todo_list: "regular",
  branch_history: "wide",
  commit: "regular",
  qa: "regular",
  btw: "regular",
  giphy: "regular",
};

const SIZE_RANK: Record<PrimitiveSize, number> = { regular: 0, wide: 1, full: 2 };

export function primitiveSize(kind: string): PrimitiveSize {
  return PRIMITIVE_SIZES[kind as PrimitiveKind] ?? "regular";
}

export function widestPrimitiveSize(
  parts: Array<{ primitive: string }> | undefined,
): PrimitiveSize {
  let widest: PrimitiveSize = "regular";
  for (const part of parts ?? []) {
    const size = primitiveSize(part.primitive);
    if (SIZE_RANK[size] > SIZE_RANK[widest]) widest = size;
  }
  return widest;
}

export interface PrimitiveFrame {
  size: PrimitiveSize;
  /** Cards that earned a breakout tier also earn a maximize control. */
  canExpand: boolean;
  expanded: boolean;
  toggleExpanded: () => void;
}

export const PrimitiveFrameContext = createContext<PrimitiveFrame | null>(null);

export function usePrimitiveFrame(): PrimitiveFrame | null {
  return useContext(PrimitiveFrameContext);
}
