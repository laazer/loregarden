/**
 * The same records as the graph, as a list — the accessible path, and not a
 * lesser view: every row is a real button, reachable and operable by keyboard.
 */

import type { GraphNode } from "../../api/memoryApi";
import { NODE_TYPE_LABELS } from "../../lib/knowledgeLayout";
import { recordTitle } from "../../lib/memoryInferred";

export function KnowledgeList({
  nodes,
  selectedId,
  onSelect,
}: {
  /** Never empty: the page renders its empty states before reaching here. */
  nodes: GraphNode[];
  selectedId: string | null;
  onSelect: (nodeId: string) => void;
}) {
  return (
    <ul className="kb-list" aria-label="Records">
      {/* ux-ok: the page decides every empty state (no records, not configured, no match) before rendering this list */}
      {nodes.map((node) => (
        <li key={node.id}>
          <button
            type="button"
            className={`kb-row${node.id === selectedId ? " kb-row--selected" : ""}${
              node.discredited ? " kb-row--discredited" : ""
            }`}
            aria-pressed={node.id === selectedId}
            onClick={() => onSelect(node.id)}
          >
            <span className="kb-row-head">
              <span className="state-label">{NODE_TYPE_LABELS[node.node_type]}</span>
              {node.discredited && <span className="kb-pill">Discredited</span>}
            </span>
            <span className="kb-row-title">{recordTitle(node)}</span>
            {node.excerpt && <span className="kb-row-excerpt">{node.excerpt}</span>}
          </button>
        </li>
      ))}
    </ul>
  );
}
