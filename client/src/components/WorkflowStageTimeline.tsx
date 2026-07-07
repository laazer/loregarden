import type { ReactNode } from "react";

import type { StageStatus, WorkflowStageView } from "../api/types";
import { stageStatusMeta } from "../lib/stageDisplay";

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

function stageAgentMeta(stage: WorkflowStageView): { agent: string | null; skill: string | null } {
  if (stage.agent_id?.trim()) {
    return { agent: stage.agent_id, skill: stage.skill_name?.trim() || null };
  }
  if (stage.agents.length === 1) {
    return {
      agent: stage.agents[0].agent_id,
      skill: stage.agents[0].skill_name?.trim() || null,
    };
  }
  if (stage.agents.length > 1) {
    return { agent: stage.agents.map((entry) => entry.agent_id).join(" · "), skill: null };
  }
  return { agent: null, skill: null };
}

function connectorColor(status: StageStatus): string {
  return status === "done" ? "var(--grn)" : "var(--bd)";
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
            <div className="workflow-stage-rail">
              {!isLast ? (
                <div
                  className="workflow-stage-line"
                  style={{ background: connectorColor(stage.status) }}
                />
              ) : null}
              <div
                className={`workflow-stage-dot ${stage.status}${isCurrent ? " current" : ""}`}
                style={{
                  borderColor: meta.dot,
                  background: meta.dotFill,
                  animation: meta.dotAnimation,
                }}
              >
                {stage.status === "done" ? (
                  <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="var(--onac)" strokeWidth="4" aria-hidden>
                    <path d="M20 6 9 17l-5-5" />
                  </svg>
                ) : null}
              </div>
            </div>

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
