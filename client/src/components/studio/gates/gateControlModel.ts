/**
 * Every control that can stop or redirect a handoff, for one workspace and,
 * optionally, one of its workflows. Pure and synchronous, so the panel renders
 * it and the tests pin it without React (lg-gate-studio-863, after 631's R6).
 */
import type { OrchestrationProfileView } from "../../../api/gateTypes";
import type { StudioAgent, StudioWorkflow, WorkspaceSummary } from "../../../api/types";
import {
  describeRequirement,
  formatExplanation,
  formatScope,
  formatStoredIn,
  GATE_CONTROL_COPY,
  type GateControlKind,
  type GateCopyContext,
} from "./gateControlKinds";

export interface GateControl {
  id: string;
  kind: GateControlKind;
  title: string;
  value: string;
  explanation: string;
  storedIn: string;
  scope: string;
  enforcementBadge: string;
  sourceRef: {
    stageKey?: string;
    actionKey?: string;
    agentSlug?: string;
    checkRole?: "gate" | "handoff";
    index?: number;
  };
}

export interface GateControlInput {
  workspace: WorkspaceSummary | null;
  profile: OrchestrationProfileView | null;
  workflow: StudioWorkflow | null;
  agents: StudioAgent[] | null;
}

/** Ids are URL segments: each part encoded on its own, joined by ':'. */
export function controlId(parts: Array<string | number>): string {
  return parts.map((part) => encodeURIComponent(String(part))).join(":");
}

function makeControl(
  kind: GateControlKind,
  id: string,
  title: string,
  value: string,
  context: GateCopyContext,
  sourceRef: GateControl["sourceRef"],
): GateControl {
  return {
    id,
    kind,
    title,
    value,
    explanation: formatExplanation(kind, context),
    storedIn: formatStoredIn(kind, context),
    scope: formatScope(kind, context),
    enforcementBadge: GATE_CONTROL_COPY[kind].enforcementBadge,
    sourceRef,
  };
}

/** The workspace-wide controls: its transition commands and script. */
export function collectWorkspaceControls(
  workspace: WorkspaceSummary | null,
  profile: OrchestrationProfileView | null,
): GateControl[] {
  if (!workspace || !profile) return [];
  const context = { workspace: workspace.slug, file: profile.source_path };
  const controls = profile.gates_commands.map((command, index) =>
    makeControl(
      "workspace_transition_command",
      controlId(["transition-command", index]),
      command || `Command ${index + 1}`,
      command || "Blank command",
      context,
      { index },
    ),
  );
  if (profile.gates_transition_script.trim()) {
    controls.push(
      makeControl(
        "workspace_transition_command",
        controlId(["transition-script"]),
        "Transition script",
        profile.gates_transition_script,
        context,
        {},
      ),
    );
  }
  return controls;
}

/** The controls one workflow adds: gate stages, exit actions, agent checks. */
export function collectWorkflowControls(input: GateControlInput | null): GateControl[] {
  if (!input?.workspace || !input.workflow) return [];
  const workspace = input.workspace.slug;
  const workflow = input.workflow.slug;
  const controls: GateControl[] = [];

  for (const stage of input.workflow.stages) {
    const context = { workspace, workflow, stage: stage.key };
    if (stage.stage_type === "gate") {
      controls.push(
        makeControl(
          "workflow_gate_stage",
          controlId(["gate-stage", stage.key]),
          stage.name,
          `${stage.name} (${stage.key})`,
          context,
          { stageKey: stage.key },
        ),
      );
    }
    // One control per exit action: a stage that is also a gate stage yields
    // both, never one merged row (863's AC2, amended for exit actions).
    if (stage.exit_actions_enabled) {
      for (const action of stage.exit_actions) {
        controls.push(
          makeControl(
            "stage_exit_action",
            controlId(["exit-action", stage.key, action.key]),
            action.label || action.key,
            describeRequirement(action.requirement),
            context,
            { stageKey: stage.key, actionKey: action.key },
          ),
        );
      }
    }
  }

  const agentSlugs = new Set<string>();
  for (const stage of input.workflow.stages) {
    if (stage.agent_id) agentSlugs.add(stage.agent_id);
    stage.classify_routes.forEach((route) => agentSlugs.add(route.agent_id));
    stage.parallel_agents.forEach((entry) => agentSlugs.add(entry.agent_id));
  }
  const agentsBySlug = new Map((input.agents ?? []).map((agent) => [agent.slug, agent]));
  for (const agentSlug of agentSlugs) {
    const agent = agentsBySlug.get(agentSlug);
    if (!agent) continue;
    const context = { workspace, workflow, agent: agentSlug };
    agent.gate_checks.forEach((check, index) => {
      controls.push(
        makeControl(
          "agent_handoff_check",
          controlId(["handoff-check", agentSlug, "gate", index]),
          check.title || check.kind,
          [check.title, check.impact].filter(Boolean).join(" — "),
          context,
          { agentSlug, checkRole: "gate", index },
        ),
      );
    });
    agent.handoff_checks.forEach((check, index) => {
      controls.push(
        makeControl(
          "agent_handoff_check",
          controlId(["handoff-check", agentSlug, "handoff", index]),
          check.prompt || check.kind,
          check.prompt || check.kind,
          context,
          { agentSlug, checkRole: "handoff", index },
        ),
      );
    });
  }
  return controls;
}

/** The workspace runs this workflow: its own template, by slug either way round. */
export function runsHere(workflow: StudioWorkflow, workspace: WorkspaceSummary): boolean {
  const slug = workspace.workflow_template_slug;
  return Boolean(slug) && (workflow.slug === slug || workflow.published_template_slug === slug);
}

/** Exact lookup; a malformed or unknown id is a miss, never another control. */
export function findGateControl(controls: GateControl[], id: string): GateControl | null {
  const parts = id.split(":");
  for (const part of parts) {
    if (!part) return null;
    try {
      decodeURIComponent(part);
    } catch {
      /* silent-ok: an undecodable segment is the not-found answer this function returns */
      return null;
    }
  }
  return controls.find((control) => control.id === id) ?? null;
}
