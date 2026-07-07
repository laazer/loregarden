import type { StageStatus, WorkflowStageView } from "../api/client";

export interface StageStatusMeta {
  label: string;
  dot: string;
  dotFill: string;
  pillBg: string;
  pillFg: string;
  nameColor: string;
  dotAnimation?: string;
  pillAnimation?: string;
}

const STAGE_STATUS_META: Record<StageStatus, StageStatusMeta> = {
  pending: {
    label: "Idle",
    dot: "#39424e",
    dotFill: "var(--bg0)",
    pillBg: "rgba(255,255,255,0.04)",
    pillFg: "var(--txl)",
    nameColor: "var(--txm)",
  },
  running: {
    label: "Running",
    dot: "var(--blue)",
    dotFill: "var(--blue)",
    pillBg: "rgba(75,155,255,0.15)",
    pillFg: "var(--bll)",
    nameColor: "var(--tx)",
    dotAnimation: "workflow-ring 1.8s ease-out infinite",
    pillAnimation: "pulse 1.8s ease-in-out infinite",
  },
  blocked: {
    label: "Blocked",
    dot: "var(--red)",
    dotFill: "var(--red)",
    pillBg: "rgba(255,106,84,0.15)",
    pillFg: "var(--rdl)",
    nameColor: "var(--tx)",
    pillAnimation: "pulse 1.8s ease-in-out infinite",
  },
  awaiting: {
    label: "Awaiting",
    dot: "var(--amb)",
    dotFill: "var(--amb)",
    pillBg: "rgba(229,167,44,0.16)",
    pillFg: "var(--aml)",
    nameColor: "var(--tx)",
    pillAnimation: "pulse 1.8s ease-in-out infinite",
  },
  done: {
    label: "Done",
    dot: "var(--grn)",
    dotFill: "var(--grn)",
    pillBg: "rgba(53,196,106,0.14)",
    pillFg: "var(--grl)",
    nameColor: "var(--txm)",
  },
  wont_do: {
    label: "Won't do",
    dot: "var(--amb)",
    dotFill: "transparent",
    pillBg: "rgba(229,167,44,0.12)",
    pillFg: "var(--aml)",
    nameColor: "var(--txm)",
  },
};

export function stageStatusMeta(status: StageStatus): StageStatusMeta {
  return STAGE_STATUS_META[status];
}

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
