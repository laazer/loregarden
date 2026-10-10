/**
 * Every edge touching a learning, both directions, typed. Discredited
 * neighbours are listed and marked — the operator needs to see an edge to a
 * withdrawn learning, even though no agent is briefed with it.
 */

import type { MemoryRelation } from "../../api/memoryApi";

const ARROW = { out: "→", in: "←" } as const;

export function LearningRelations({
  relations,
  onOpen,
}: {
  relations: MemoryRelation[];
  onOpen: (nodeId: string) => void;
}) {
  if (relations.length === 0) {
    return (
      <p className="memory-muted">
        No relations. Agents link learnings with loregarden_create_memory_relation; an unlinked
        learning never appears in another's related digest.
      </p>
    );
  }
  return (
    <ul className="memory-relations" aria-label="Relations">
      {relations.map((edge) => (
        <li key={edge.id} className={`memory-relation memory-relation--${edge.relation_type}`}>
          <span className="memory-relation-type">
            {ARROW[edge.direction]} {edge.relation_type.replace("_", " ")}
          </span>
          <button type="button" className="memory-link-button" onClick={() => onOpen(edge.node_id)}>
            {edge.title}
          </button>
          {edge.discredited && <span className="state-label">discredited</span>}
        </li>
      ))}
    </ul>
  );
}
