import type { WorkflowStageView } from "../api/types";
import { stageRouteHints, stageRouteLabel, type WorkflowTransition } from "../lib/workflowTransitions";

interface StageRouteHintsProps {
  stage: WorkflowStageView;
  transitions: WorkflowTransition[];
  stages: WorkflowStageView[];
}

export function StageRouteHints({ stage, transitions, stages }: StageRouteHintsProps) {
  const hints = stageRouteHints(stage.key, transitions, stages);
  if (!hints.length) return null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 6 }}>
      {hints.map((hint) => (
        <div
          key={`${stage.key}-${hint.when}-${hint.targetKey}`}
          style={{
            fontFamily: "var(--mono)",
            fontSize: 10.5,
            color: hint.upstream || hint.when === "reject" ? "var(--orl, #c77d2d)" : "var(--txm)",
          }}
        >
          {stageRouteLabel(hint)}
        </div>
      ))}
    </div>
  );
}
