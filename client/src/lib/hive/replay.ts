import type { StageStatus, WorkflowStageView } from "../../api/client";

function isReplayableStage(stage: WorkflowStageView): boolean {
  const agentId = stage.agent_id?.trim() ?? "";
  if (!agentId || agentId === "—" || agentId.toLowerCase().includes("human")) return false;
  if (stage.stage_type === "gate") return false;
  return true;
}

function cloneStages(stages: WorkflowStageView[]): WorkflowStageView[] {
  return stages.map((stage) => ({ ...stage, agents: stage.agents.map((a) => ({ ...a })) }));
}

function setStatus(
  stages: WorkflowStageView[],
  key: string,
  status: StageStatus,
): WorkflowStageView[] {
  return stages.map((stage) => (stage.key === key ? { ...stage, status } : stage));
}

/**
 * Build a cinematic stage timeline from the ticket's workflow.
 * Each keyframe is a full stages[] snapshot the hive world model can consume.
 */
export function buildHiveReplayFrames(stages: WorkflowStageView[]): WorkflowStageView[][] {
  const frames: WorkflowStageView[][] = [];
  const ordered = stages
    .map((stage, index) => ({ stage, index }))
    .filter(({ stage }) => isReplayableStage(stage))
    .sort((a, b) => (a.stage.order ?? a.index) - (b.stage.order ?? b.index));

  if (ordered.length === 0) return frames;

  let current = stages.map((stage) =>
    isReplayableStage(stage) ? { ...stage, status: "pending" as StageStatus } : { ...stage },
  );
  frames.push(cloneStages(current));

  for (const { stage: original } of ordered) {
    current = setStatus(current, original.key, "running");
    frames.push(cloneStages(current));

    if (original.status === "blocked" || original.status === "awaiting") {
      current = setStatus(current, original.key, original.status);
      frames.push(cloneStages(current));
    }

    const terminal: StageStatus = original.status === "wont_do" ? "wont_do" : "done";
    current = setStatus(current, original.key, terminal);
    frames.push(cloneStages(current));
  }

  return frames;
}

export const HIVE_REPLAY_FRAME_MS = 900;

/** @deprecated Use hiveReplayFrameMs from ./speed */
