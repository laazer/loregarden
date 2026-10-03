/**
 * The four things that can stop or redirect a handoff, kept distinct.
 *
 * Gate Studio's job is to answer "what can stop this ticket here, where is it
 * configured, and what does it actually do?" — and the honest answer differs by
 * kind: a transition command blocks, a gate stage blocks or reroutes, an exit
 * action must be met before a stage can be left, and an agent handoff check is
 * prompt text nothing enforces. One flattened "gate" type would misstate three
 * of the four (lg-gate-studio-863, after 631).
 */
import type { StudioExitActionRequirement } from "../../../api/types";

export const GATE_CONTROL_KINDS = [
  "workspace_transition_command",
  "workflow_gate_stage",
  "stage_exit_action",
  "agent_handoff_check",
] as const;

export type GateControlKind = (typeof GATE_CONTROL_KINDS)[number];
export type GateEnforcementTone = "blocking" | "parking" | "advisory";

export interface GateCopyContext {
  workspace?: string;
  workflow?: string;
  stage?: string;
  agent?: string;
  file?: string | null;
}

interface GateControlCopy {
  label: string;
  plural: string;
  explanation: string;
  enforcementBadge: string;
  tone: GateEnforcementTone;
  storedInTemplate: string;
  scopeTemplate: string;
  /** Shown when a workflow or workspace has none of this kind. */
  emptyText: string;
}

export const GATE_CONTROL_COPY: Readonly<Record<GateControlKind, GateControlCopy>> = Object.freeze({
  workspace_transition_command: Object.freeze({
    label: "Transition command",
    plural: "Transition commands",
    explanation:
      "Runs in the ticket's worktree when a stage finishes; the first that exits non-zero stops the handoff, after fixers and an agent retry get a chance to repair it.",
    enforcementBadge: "Blocks handoff",
    tone: "blocking",
    storedInTemplate: "{file}, under gates",
    scopeTemplate: "Every stage transition in workspace {workspace}",
    emptyText: "No transition commands — every transition in this workspace passes straight through.",
  }),
  workflow_gate_stage: Object.freeze({
    label: "Gate stage",
    plural: "Gate stages",
    explanation: "A workflow stage whose verdict decides whether the ticket moves on, is rerouted, or blocks.",
    enforcementBadge: "Blocks or reroutes",
    tone: "blocking",
    storedInTemplate: "the stages of workflow {workflow}",
    scopeTemplate: "Stage {stage} in workflow {workflow}",
    emptyText: "This workflow has no gate stage.",
  }),
  stage_exit_action: Object.freeze({
    label: "Stage exit action",
    plural: "Stage exit actions",
    explanation:
      "Must be met before stage {stage} can be left. Whatever this run's agent can do is handed to it; only the rest — and every operator judgement — reaches a person.",
    enforcementBadge: "Required to leave the stage",
    tone: "parking",
    storedInTemplate: "the exit actions of stage {stage} in workflow {workflow}",
    scopeTemplate: "Leaving stage {stage} in workflow {workflow}",
    emptyText: "No stage in this workflow has exit actions.",
  }),
  agent_handoff_check: Object.freeze({
    label: "Agent handoff check",
    plural: "Agent handoff checks",
    explanation: "Prompt text given to agent {agent}. Nothing enforces it.",
    enforcementBadge: "Advisory prompt",
    tone: "advisory",
    storedInTemplate: "the gate and handoff checks of agent {agent}",
    scopeTemplate: "Every stage of workflow {workflow} that runs agent {agent}",
    emptyText: "No agent this workflow runs has handoff checks.",
  }),
});

const REQUIREMENT_LABEL: Record<StudioExitActionRequirement["kind"], string> = {
  operator_judgment: "Operator judgement",
  runtime_capability: "Runtime capability",
  credential: "Credential",
  authority: "Authority",
};

/** "Operator judgement — Is the release note accurate?" */
export function describeRequirement(requirement: StudioExitActionRequirement): string {
  const detail =
    requirement.kind === "operator_judgment"
      ? requirement.decision_prompt
      : requirement.kind === "runtime_capability"
        ? requirement.capability_id
        : requirement.kind === "credential"
          ? requirement.credential_key
          : requirement.authority_scope;
  return detail ? `${REQUIREMENT_LABEL[requirement.kind]} — ${detail}` : REQUIREMENT_LABEL[requirement.kind];
}

function fill(template: string, context: GateCopyContext): string {
  return template.replace(/\{(workspace|workflow|stage|agent|file)\}/g, (_match, key: keyof GateCopyContext) =>
    String(context[key] ?? "unknown"),
  );
}

export function formatStoredIn(kind: GateControlKind, context: GateCopyContext): string {
  if (kind === "workspace_transition_command" && context.file === null) {
    return "Built-in defaults — no orchestration profile file stores this yet; the first save creates one.";
  }
  return fill(GATE_CONTROL_COPY[kind].storedInTemplate, context);
}

export function formatScope(kind: GateControlKind, context: GateCopyContext): string {
  return fill(GATE_CONTROL_COPY[kind].scopeTemplate, context);
}

export function formatExplanation(kind: GateControlKind, context: GateCopyContext): string {
  return fill(GATE_CONTROL_COPY[kind].explanation, context);
}
