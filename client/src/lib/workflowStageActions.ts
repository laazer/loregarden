import type { StageStatus, TicketDetail, WorkflowStageView } from "../api/types";
import {
  isAgentStage,
  isDoneStage,
  isHumanGateStage,
  stageRunButtonLabel,
} from "./stageDisplay";
import { isAgentWorkflowTicket } from "./terminalCommands";
import { stageRouteHints } from "./workflowTransitions";

export interface StageMenuAction {
  id: string;
  label: string;
  disabled?: boolean;
  title?: string;
  danger?: boolean;
  section?: "run" | "route" | "cursor" | "status" | "edit";
  kind:
    | "run"
    | "copy-terminal"
    | "set-cursor"
    | "route-upstream"
    | "mark-done"
    | "reset-pending"
    | "mark-wont-do"
    | "edit-state";
  payload?: {
    targetStageKey?: string;
    nextAgent?: string;
    status?: StageStatus;
  };
}

export function buildStageMenuActions({
  ticket,
  stage,
  runCheck,
  isRunning,
  workflowBusy,
  canCopyTerminal,
}: {
  ticket: TicketDetail;
  stage: WorkflowStageView;
  runCheck: { allowed: boolean; reason: string };
  isRunning: boolean;
  workflowBusy: boolean;
  canCopyTerminal: boolean;
}): StageMenuAction[] {
  const actions: StageMenuAction[] = [];
  const runDisabled = !runCheck.allowed || workflowBusy || isRunning;

  actions.push({
    id: "run",
    kind: "run",
    section: "run",
    label: stageRunButtonLabel(stage, isRunning),
    disabled: runDisabled,
    title: runCheck.reason,
  });

  if (canCopyTerminal && isAgentWorkflowTicket(ticket) && isAgentStage(stage)) {
    actions.push({
      id: "copy-terminal",
      kind: "copy-terminal",
      section: "run",
      label: "Copy terminal command",
      title: `Copy CLI command to run ${stage.name}`,
    });
  }

  if (stage.key !== ticket.workflow_stage_key) {
    actions.push({
      id: "set-cursor",
      kind: "set-cursor",
      section: "cursor",
      label: "Set as current step",
      title: `Move workflow cursor to ${stage.name}`,
      payload: { targetStageKey: stage.key },
    });
  }

  const rejectRoutes = stageRouteHints(stage.key, ticket.workflow_transitions ?? [], ticket.stages).filter(
    (hint) => hint.when === "reject" || hint.upstream,
  );
  for (const route of rejectRoutes) {
    actions.push({
      id: `route-${route.targetKey}`,
      kind: "route-upstream",
      section: "route",
      label: `Route to ${route.targetName}`,
      title: route.agentId
        ? `Send rework to ${route.targetName} (${route.agentId})`
        : `Send rework to ${route.targetName}`,
      payload: {
        targetStageKey: route.targetKey,
        nextAgent: route.agentId,
      },
    });
  }

  if (!isDoneStage(stage) && stage.status !== "done") {
    actions.push({
      id: "mark-done",
      kind: "mark-done",
      section: "status",
      label: "Mark step done",
      payload: { status: "done" },
    });
  }

  if (stage.status === "done" || stage.status === "blocked") {
    actions.push({
      id: "reset-pending",
      kind: "reset-pending",
      section: "status",
      label: "Reset to pending",
      payload: { status: "pending" },
    });
  }

  if (stage.optional && stage.status !== "wont_do") {
    actions.push({
      id: "mark-wont-do",
      kind: "mark-wont-do",
      section: "status",
      label: "Mark won't do",
      danger: true,
      payload: { status: "wont_do" },
    });
  }

  if (!isHumanGateStage(stage) || stage.status !== "awaiting") {
    actions.push({
      id: "edit-state",
      kind: "edit-state",
      section: "edit",
      label: "Edit workflow state…",
    });
  }

  return actions;
}
