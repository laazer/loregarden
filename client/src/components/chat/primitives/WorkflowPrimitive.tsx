import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Background,
  Controls,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { api } from "../../../api/client";
import type { StudioWorkflowStage } from "../../../api/types";
import type { WorkflowPart } from "./types";
import { PrimitiveCard } from "./PrimitiveCard";
import { OpenWorkflowStudioButton } from "./ResourceActionButton";

function stagesFromDraft(draft: Record<string, unknown> | null | undefined): StudioWorkflowStage[] {
  const stages = draft?.stages;
  if (!Array.isArray(stages)) return [];
  return stages as StudioWorkflowStage[];
}

function StageNodeLabel({ stage }: { stage: StudioWorkflowStage }) {
  const parallelAgents = stage.parallel_agents ?? [];
  const typeLabel = stage.stage_type?.replace("_", " ") || "agent";

  return (
    <div className="lg-primitive-workflow-node-content">
      <div className="lg-primitive-workflow-node-head">
        <strong>{stage.name || stage.key}</strong>
        <span>{typeLabel}</span>
      </div>
      {stage.stage_type === "parallel" ? (
        <div className="lg-primitive-workflow-parallel">
          {parallelAgents.length ? (
            parallelAgents.map((agent, index) => (
              <div
                key={`${agent.agent_id || "agent"}-${index}`}
                className="lg-primitive-workflow-agent"
              >
                <span className="lg-primitive-workflow-agent-index">{index + 1}</span>
                <span className="lg-primitive-workflow-agent-name">
                  {agent.agent_id || "Unassigned agent"}
                </span>
                {agent.skill_name ? (
                  <span className="lg-primitive-workflow-agent-skill">{agent.skill_name}</span>
                ) : null}
              </div>
            ))
          ) : (
            <span className="lg-primitive-workflow-node-empty">No agents assigned</span>
          )}
        </div>
      ) : stage.agent_id ? (
        <div className="lg-primitive-workflow-node-owner">
          <span>{stage.agent_id}</span>
          {stage.skill_name ? <small>{stage.skill_name}</small> : null}
        </div>
      ) : null}
      {stage.optional || stage.skip_when ? (
        <div className="lg-primitive-workflow-node-flags">
          {stage.optional ? <span>Optional</span> : null}
          {stage.skip_when ? <span>Skip: {stage.skip_when.replaceAll("_", " ")}</span> : null}
        </div>
      ) : null}
    </div>
  );
}

function stageNodeHeight(stage: StudioWorkflowStage): number {
  if (stage.stage_type === "parallel") {
    return 70 + Math.max(stage.parallel_agents?.length ?? 0, 1) * 28;
  }
  return stage.optional || stage.skip_when ? 112 : stage.agent_id ? 94 : 72;
}

function buildGraph(stages: StudioWorkflowStage[]): { nodes: Node[]; edges: Edge[] } {
  const sorted = [...stages].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  const columnCount = Math.min(4, Math.max(sorted.length, 1));
  const rowCount = Math.ceil(sorted.length / columnCount);
  const rowHeights = Array.from({ length: rowCount }, (_, row) =>
    Math.max(
      ...sorted
        .slice(row * columnCount, (row + 1) * columnCount)
        .map(stageNodeHeight),
      72,
    ),
  );
  const rowOffsets = rowHeights.map((_, row) =>
    rowHeights.slice(0, row).reduce((sum, height) => sum + height + 68, 40),
  );

  const nodes: Node[] = sorted.map((stage, index) => {
    const row = Math.floor(index / columnCount);
    const columnInFlow = index % columnCount;
    const column = row % 2 === 0 ? columnInFlow : columnCount - 1 - columnInFlow;
    const flowsRight = row % 2 === 0;
    return {
      id: stage.key || `stage-${index}`,
      position: { x: 40 + column * 270, y: rowOffsets[row] },
      sourcePosition: flowsRight ? Position.Right : Position.Left,
      targetPosition: flowsRight ? Position.Left : Position.Right,
      data: { label: <StageNodeLabel stage={stage} /> },
      className: [
        "lg-primitive-workflow-node",
        `lg-primitive-workflow-node--${stage.stage_type || "agent"}`,
      ].join(" "),
      style: {
        width: 220,
        minHeight: stageNodeHeight(stage),
        padding: 0,
        overflow: "hidden",
      },
    };
  });

  const edges: Edge[] = [];
  for (let i = 0; i < nodes.length - 1; i += 1) {
    const sourceStage = sorted[i];
    const targetStage = sorted[i + 1];
    const entersGate = targetStage.stage_type === "gate";
    const exitsGate = sourceStage.stage_type === "gate";
    edges.push({
      id: `${nodes[i].id}->${nodes[i + 1].id}`,
      source: nodes[i].id,
      target: nodes[i + 1].id,
      type: "smoothstep",
      markerEnd: { type: MarkerType.ArrowClosed },
      label: entersGate ? "Gate" : exitsGate ? "Pass" : undefined,
      className:
        entersGate || exitsGate
          ? "lg-primitive-workflow-edge lg-primitive-workflow-edge--gate"
          : "lg-primitive-workflow-edge",
      style: {
        stroke: entersGate || exitsGate ? "var(--red)" : "var(--txl)",
        strokeWidth: entersGate || exitsGate ? 1.8 : 1.35,
      },
      labelStyle: {
        fill: entersGate || exitsGate ? "var(--red)" : "var(--txm)",
        fontSize: 10,
        fontWeight: 650,
      },
      labelBgStyle: {
        fill: "var(--bg0)",
        fillOpacity: 0.94,
      },
      labelBgPadding: [6, 4],
      labelBgBorderRadius: 5,
    });
  }
  return { nodes, edges };
}

export function WorkflowPrimitive({ part }: { part: WorkflowPart }) {
  const slug = part.workflow_slug ?? undefined;
  const { data, isLoading, error } = useQuery({
    queryKey: ["studio-workflow", slug],
    queryFn: () => api.studioWorkflow(slug!),
    enabled: Boolean(slug) && !part.draft,
  });

  const stages = useMemo(
    () => (part.draft ? stagesFromDraft(part.draft) : (data?.stages ?? [])),
    [part.draft, data?.stages],
  );
  const { nodes, edges } = useMemo(() => buildGraph(stages), [stages]);
  const title =
    part.title ??
    data?.name ??
    (typeof part.draft?.name === "string" ? part.draft.name : null) ??
    slug ??
    "Workflow";

  return (
    <PrimitiveCard
      title={title}
      subtitle={slug ?? undefined}
      loading={isLoading && !part.draft}
      error={
        !part.draft && error
          ? error instanceof Error
            ? error.message
            : "Failed to load workflow"
          : null
      }
      actions={slug && !part.draft ? <OpenWorkflowStudioButton slug={slug} /> : null}
    >
      <div className="lg-primitive-workflow-flow">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          fitView
          fitViewOptions={{ padding: 0.16, minZoom: 0.58, maxZoom: 1 }}
          minZoom={0.45}
          maxZoom={1.8}
          nodesDraggable={false}
          nodesConnectable={false}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={20} size={1} color="var(--bd)" />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </PrimitiveCard>
  );
}
