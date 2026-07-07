import type { WorkflowStageView } from "../api/types";

export interface WorkflowTransition {
  from: string;
  to: string;
  when?: string;
  agent_id?: string;
}

export interface StageRouteHint {
  when: "pass" | "reject" | "default";
  targetKey: string;
  targetName: string;
  agentId?: string;
  upstream: boolean;
}

type StageRef = Pick<WorkflowStageView, "key" | "name" | "order">;

function transitionWhen(transition: WorkflowTransition): string {
  return transition.when?.trim() || "default";
}

function stageOrder(stages: StageRef[], key: string): number {
  const stage = stages.find((item) => item.key === key);
  if (stage?.order != null) {
    return stage.order;
  }
  const index = stages.findIndex((item) => item.key === key);
  return index >= 0 ? index : -1;
}

export function isUpstreamRoute(
  stages: StageRef[],
  fromKey: string,
  toKey: string,
): boolean {
  const fromOrder = stageOrder(stages, fromKey);
  const toOrder = stageOrder(stages, toKey);
  if (fromOrder < 0 || toOrder < 0) return false;
  return toOrder < fromOrder;
}

export function stageRouteHints(
  stageKey: string,
  transitions: WorkflowTransition[],
  stages: StageRef[],
): StageRouteHint[] {
  const stageNameByKey = new Map(stages.map((stage) => [stage.key, stage.name]));
  const hints: StageRouteHint[] = [];

  for (const transition of transitions) {
    if (transition.from !== stageKey || !transition.to) continue;
    const when = transitionWhen(transition);
    if (when !== "pass" && when !== "reject" && when !== "default") continue;
    hints.push({
      when: when === "pass" || when === "reject" ? when : "default",
      targetKey: transition.to,
      targetName: stageNameByKey.get(transition.to) ?? transition.to.replaceAll("_", " "),
      agentId: transition.agent_id?.trim() || undefined,
      upstream: isUpstreamRoute(stages, stageKey, transition.to),
    });
  }

  const rank = { reject: 0, pass: 1, default: 2 };
  return hints.sort((a, b) => rank[a.when] - rank[b.when]);
}

export function stageRouteLabel(hint: StageRouteHint): string {
  const arrow = hint.upstream || hint.when === "reject" ? "↩" : "→";
  const agentSuffix = hint.agentId ? ` · ${hint.agentId}` : "";
  if (hint.when === "reject") {
    return `${arrow} ${hint.targetName}${agentSuffix} on reject`;
  }
  if (hint.when === "pass") {
    return `${arrow} ${hint.targetName}${agentSuffix} on pass`;
  }
  return `${arrow} ${hint.targetName}${agentSuffix}`;
}

export function stageHasUpstreamRoutes(
  stageKey: string,
  transitions: WorkflowTransition[],
  stages: StageRef[],
): boolean {
  return stageRouteHints(stageKey, transitions, stages).some((hint) => hint.upstream);
}
