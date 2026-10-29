import type { ReactNode } from "react";

import type { WorkflowStageView } from "../api/types";
import { stageAgentMeta, stageStatusMeta } from "../lib/stageDisplay";
import { vineMark } from "../lib/vine";
import { VineNode } from "./vine/VineNode";

function AgentRobotIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="var(--vil)" strokeWidth="2" aria-hidden>
      <rect x="4" y="8" width="16" height="12" rx="2" />
      <path d="M12 4v4M9 2h6" />
      <circle cx="9" cy="14" r="1" fill="var(--vil)" />
      <circle cx="15" cy="14" r="1" fill="var(--vil)" />
    </svg>
  );
}

export function WorkflowStageTimeline({
  stages,
  currentStageKey,
  renderStageActions,
  renderStageExtras,
}: {
  stages: WorkflowStageView[];
  currentStageKey: string;
  renderStageActions?: (stage: WorkflowStageView) => ReactNode;
  renderStageExtras?: (stage: WorkflowStageView) => ReactNode;
}) {
  return (
    <div className="workflow-lifecycle">
      {stages.map((stage, index) => {
        const meta = stageStatusMeta(stage.status);
        const isCurrent = stage.key === currentStageKey;
        const isLast = index === stages.length - 1;
        const { agent, skill } = stageAgentMeta(stage);

        return (
          <div key={stage.key} className="workflow-stage">
            <VineNode index={index} mark={vineMark(stage.status, isCurrent)} last={isLast} />

            <div className="workflow-stage-body">
              <div className="workflow-stage-header">
                <span
                  className="workflow-stage-name"
                  style={{ fontWeight: isCurrent ? 600 : 500, color: meta.nameColor }}
                >
                  {stage.name}
                </span>
                {stage.optional ? <span className="workflow-stage-optional">optional</span> : null}
                <span
                  className="workflow-stage-status-pill"
                  style={{
                    background: meta.pillBg,
                    color: meta.pillFg,
                    animation: meta.pillAnimation,
                  }}
                >
                  {meta.label}
                </span>
              </div>

              {agent ? (
                <div className="workflow-stage-meta">
                  <span className="workflow-stage-agent">
                    <AgentRobotIcon />
                    {agent}
                  </span>
                  {skill ? <span className="workflow-stage-skill">{skill}</span> : null}
                </div>
              ) : null}

              {stage.note ? (
                <div className="workflow-stage-note" data-status={stage.status}>
                  {stage.note}
                </div>
              ) : null}

              {renderStageActions ? (
                <div className="workflow-stage-actions">{renderStageActions(stage)}</div>
              ) : null}

              {renderStageExtras ? renderStageExtras(stage) : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}
