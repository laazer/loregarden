/**
 * The knowledge graph drawn with @xyflow/react — the same records as the list.
 *
 * Colour identifies the record type; a dashed border marks a discredited
 * record, so its standing is carried by something other than colour. Edges
 * are drawn only for recorded relations. Positions come from
 * `layoutKnowledge` and mean nothing, which the legend says in words.
 */

import {
  Background,
  Controls,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useEffect, useMemo } from "react";

import type { GraphNode, GraphRelation, NodeType } from "../../api/memoryApi";
import { layoutKnowledge, NODE_TYPE_LABELS } from "../../lib/knowledgeLayout";

const TYPE_COLOURS: Record<NodeType, string> = {
  memory: "var(--kb-memory, #6aa9ff)",
  learning: "var(--kb-learning, #3fb950)",
};

function Canvas({
  nodes,
  relations,
  selectedId,
  onSelect,
}: {
  nodes: GraphNode[];
  relations: GraphRelation[];
  selectedId: string | null;
  onSelect: (nodeId: string) => void;
}) {
  const flow = useReactFlow();
  const flowNodes = useMemo<Node[]>(() => {
    const positions = new Map(layoutKnowledge(nodes).map((p) => [p.id, p]));
    return nodes.map((node) => {
      const at = positions.get(node.id) ?? { x: 0, y: 0 };
      return {
        id: node.id,
        position: { x: at.x, y: at.y },
        data: { label: node.title },
        selected: node.id === selectedId,
        className: `kb-node${node.discredited ? " kb-node--discredited" : ""}${
          node.id === selectedId ? " kb-node--selected" : ""
        }`,
        style: { borderColor: TYPE_COLOURS[node.node_type] },
      };
    });
  }, [nodes, selectedId]);
  const flowEdges = useMemo<Edge[]>(
    () =>
      relations.map((edge) => ({
        id: edge.id,
        source: edge.source_id,
        target: edge.target_id,
        label: edge.relation_type === "related" ? undefined : edge.relation_type.replace("_", " "),
      })),
    [relations],
  );

  // Focus the canvas on the selected record — how a neighbour click in the
  // panel moves the view. Selection never refetches the graph.
  useEffect(() => {
    if (selectedId && nodes.some((node) => node.id === selectedId)) {
      void flow.fitView({ nodes: [{ id: selectedId }], duration: 300, maxZoom: 1 });
    }
  }, [flow, nodes, selectedId]);

  return (
    <ReactFlow
      nodes={flowNodes}
      edges={flowEdges}
      onNodeClick={(_event, node) => onSelect(node.id)}
      fitView
      fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
      minZoom={0.3}
      maxZoom={1.8}
      nodesDraggable={false}
      nodesConnectable={false}
      proOptions={{ hideAttribution: true }}
    >
      <Background gap={20} size={1} color="var(--bd)" />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}

function Legend({ types }: { types: NodeType[] }) {
  return (
    <div className="kb-legend" aria-label="Legend">
      <ul>
        {types.map((type) => (
          <li key={type}>
            <span className="kb-swatch" style={{ borderColor: TYPE_COLOURS[type] }} aria-hidden />
            {NODE_TYPE_LABELS[type]}
          </li>
        ))}
        <li>
          <span className="kb-swatch kb-swatch--discredited" aria-hidden />
          Discredited
        </li>
      </ul>
      <p>
        Colours identify record types. Lines show recorded relationships; nearby positions are a
        visual grouping only.
      </p>
    </div>
  );
}

export function KnowledgeGraphView(props: {
  nodes: GraphNode[];
  relations: GraphRelation[];
  selectedId: string | null;
  onSelect: (nodeId: string) => void;
}) {
  const types = [...new Set(props.nodes.map((node) => node.node_type))];
  return (
    <div className="kb-graph">
      <div className="kb-canvas" data-testid="knowledge-canvas">
        <ReactFlowProvider>
          <Canvas {...props} />
        </ReactFlowProvider>
      </div>
      <Legend types={types} />
    </div>
  );
}
