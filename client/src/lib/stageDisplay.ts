import type { WorkflowStageView } from "../api/client";

export function isDoneStage(stage: WorkflowStageView): boolean {
  return stage.key === "done";
}

export function isParallelStage(stage: WorkflowStageView): boolean {
  return stage.stage_type === "parallel";
}

export function isClassifyStage(stage: WorkflowStageView): boolean {
  return stage.stage_type === "classify";
}

export function isGateStage(stage: WorkflowStageView): boolean {
  return stage.stage_type === "gate";
}

export function isHumanGateStage(stage: WorkflowStageView): boolean {
  if (isDoneStage(stage)) return false;
  if (isParallelStage(stage) || isClassifyStage(stage) || isGateStage(stage)) return false;
  return !stage.agent_id?.trim() && stage.agents.length === 0;
}

export function isAgentStage(stage: WorkflowStageView): boolean {
  if (isDoneStage(stage)) return false;
  if (isParallelStage(stage) || isClassifyStage(stage) || isGateStage(stage)) return true;
  return Boolean(stage.agent_id?.trim());
}

export function stageKindLabel(stage: WorkflowStageView): string | null {
  if (isDoneStage(stage)) return "terminal stage · marks ticket complete";
  if (isHumanGateStage(stage)) return "human approval gate";
  if (isParallelStage(stage)) return "parallel review";
  if (isClassifyStage(stage)) return "routes via ticket next_agent";
  if (isGateStage(stage)) return "acceptance criteria gate · can route upstream on reject";
  return null;
}

export function stageAgentSubtitle(stage: WorkflowStageView): string | null {
  if (isParallelStage(stage) && stage.agents.length) {
    return stage.agents.map((agent) => agent.agent_id).join(" · ");
  }
  if (isClassifyStage(stage) && stage.agents.length) {
    return stage.agents.map((agent) => agent.agent_id).join(" · ");
  }
  if (stage.agent_id) {
    return stage.skill_name ? `${stage.agent_id} · ${stage.skill_name}` : stage.agent_id;
  }
  return null;
}

export function stageRunButtonLabel(stage: WorkflowStageView, isRunning: boolean): string {
  if (isRunning) return "Running…";
  if (isDoneStage(stage)) {
    if (stage.status === "done") return "Complete";
    return "Complete ticket";
  }
  if (isHumanGateStage(stage)) {
    if (stage.status === "awaiting") return "Awaiting approval";
    if (stage.status === "done") return "Re-request";
    return "Request approval";
  }
  if (isParallelStage(stage)) {
    if (stage.status === "done") return "Re-run reviews";
    return "Run reviews";
  }
  if (stage.status === "done") return "Re-Run";
  return "Run";
}

export function currentStageRunLabel(stage: WorkflowStageView | undefined, isRunning: boolean): string {
  if (!stage) return "Run current stage";
  if (isRunning) return "Running…";
  if (isDoneStage(stage)) {
    if (stage.status === "done") return "Ticket complete";
    return "Complete ticket";
  }
  if (isHumanGateStage(stage)) {
    if (stage.status === "awaiting") return "Awaiting approval";
    if (stage.status === "done") return "Re-request approval";
    return "Request approval";
  }
  if (isParallelStage(stage)) {
    if (stage.status === "done") return "Re-run reviews";
    return "Run parallel reviews";
  }
  if (stage.status === "done") return "Re-run current stage";
  return "Run current stage";
}
