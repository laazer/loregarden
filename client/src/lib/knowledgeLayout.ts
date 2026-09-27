/**
 * Display names for memory record types, used by the map legend, the type
 * chips, the list and the record panel's eyebrow. Where records sit on the
 * map is `memoryMapLayout`'s job.
 */

import type { NodeType } from "../api/memoryApi";

export const NODE_TYPE_LABELS: Record<NodeType, string> = {
  memory: "Memory",
  learning: "Learning",
};
