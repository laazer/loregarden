/**
 * Where each record sits on the knowledge graph canvas.
 *
 * Deliberately meaningless, and the legend says so: records are laid out in
 * one grid per record type, newest first, so the same graph always draws the
 * same way. Nearness within a grid is not similarity and distance is not
 * dissimilarity — a force layout would imply both to anyone who did not build
 * it. Edges are the only relationships the canvas asserts.
 */

import type { GraphNode, NodeType } from "../api/memoryApi";
import { NODE_TYPES } from "../api/memoryApi";

export const CELL_WIDTH = 240;
export const CELL_HEIGHT = 110;
const COLUMNS = 4;
const GROUP_GAP = 80;

export interface Placed {
  id: string;
  x: number;
  y: number;
}

export function layoutKnowledge(nodes: GraphNode[]): Placed[] {
  const placed: Placed[] = [];
  let top = 0;
  const groups: NodeType[] = [...NODE_TYPES];
  for (const type of groups) {
    const members = nodes.filter((node) => node.node_type === type);
    members.forEach((node, index) => {
      placed.push({
        id: node.id,
        x: (index % COLUMNS) * CELL_WIDTH,
        y: top + Math.floor(index / COLUMNS) * CELL_HEIGHT,
      });
    });
    if (members.length > 0) {
      top += Math.ceil(members.length / COLUMNS) * CELL_HEIGHT + GROUP_GAP;
    }
  }
  return placed;
}

/** Display names for record types, used by chips, the legend and the panel eyebrow. */
export const NODE_TYPE_LABELS: Record<NodeType, string> = {
  memory: "Memory",
  learning: "Learning",
};
