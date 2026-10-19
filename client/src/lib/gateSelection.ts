import { api } from "../api/client";
import type { GatePresets } from "../api/gatePresetTypes";
import type { OrchestrationProfileView } from "../api/gateTypes";

/** What the picker starts with ticked: the detected toolchains' fast checks. */
export function defaultGateSelection(presets: GatePresets | undefined): Set<string> {
  return new Set(
    (presets?.toolchains ?? [])
      .filter((toolchain) => toolchain.detected)
      .flatMap((toolchain) => toolchain.commands.filter((c) => c.default_on).map((c) => c.command)),
  );
}

/** Every command the presets offer, for telling a preset command from one written by hand. */
export function presetCommands(presets: GatePresets | undefined): Set<string> {
  return new Set((presets?.toolchains ?? []).flatMap((toolchain) => toolchain.commands.map((c) => c.command)));
}

/**
 * Each preset the workspace already runs, mapped to the configured command
 * that runs it: the preset itself, or an equivalent spelled another way.
 */
export function runningPresets(presets: GatePresets | undefined): Map<string, string> {
  const running = new Map<string, string>();
  for (const toolchain of presets?.toolchains ?? []) {
    for (const preset of toolchain.commands) {
      if (preset.running_as) running.set(preset.command, preset.running_as);
    }
  }
  return running;
}

/**
 * `existing` with `add` appended and `remove` dropped, order kept and nothing doubled.
 * Commands neither list names — loregarden's guardrails, anything written in Studio — stay.
 */
export function mergeGateCommands(existing: string[], add: Iterable<string>, remove: Iterable<string>): string[] {
  const removed = new Set(remove);
  const kept = existing.filter((command) => !removed.has(command));
  const present = new Set(kept);
  for (const command of add) {
    if (!present.has(command)) {
      kept.push(command);
      present.add(command);
    }
  }
  return kept;
}

/**
 * Save a change to the workspace's gate commands through its profile, keeping the
 * transition script and everything not added or removed. Turns gates on: a
 * command chosen here is meant to run.
 */
export async function saveGateSelection(
  slug: string,
  add: Iterable<string>,
  remove: Iterable<string>,
): Promise<OrchestrationProfileView> {
  const profile = await api.orchestrationProfile(slug);
  return api.updateWorkspaceGates(slug, {
    enabled: true,
    commands: mergeGateCommands(profile.gates_commands, add, remove),
    transition_script: profile.gates_transition_script,
  });
}

/** loregarden's own gate scripts, run in every workspace through the shared dispatcher. */
const GUARDRAIL_SCRIPTS = "{loregarden_root}/.lefthook/scripts/";

/** A workspace's configured gate commands, by where each came from. */
export interface RunningGates {
  /** Commands a preset offers or does under another spelling; the picker shows these as ticked. */
  presets: string[];
  /** loregarden's guardrail scripts. */
  guardrails: string[];
  /** Everything else: written in Studio or by hand, under any spelling. */
  custom: string[];
}

/**
 * Sort `commands` by origin, so a panel offering presets can still say what
 * else runs. `presetLike` holds the preset commands and the configured
 * commands the server found equivalent to one (`running_as`); a command it
 * could not match is listed as custom, never silently dropped.
 */
export function classifyGateCommands(commands: readonly string[], presetLike: ReadonlySet<string>): RunningGates {
  const running: RunningGates = { presets: [], guardrails: [], custom: [] };
  for (const command of commands) {
    if (presetLike.has(command)) running.presets.push(command);
    else if (command.includes(GUARDRAIL_SCRIPTS)) running.guardrails.push(command);
    else running.custom.push(command);
  }
  return running;
}

/** The script a guardrail command runs, e.g. `workspace-gates.sh`, for a short label. */
export function guardrailScript(command: string): string {
  const after = command.slice(command.indexOf(GUARDRAIL_SCRIPTS) + GUARDRAIL_SCRIPTS.length);
  return after.split(/\s/)[0] || command;
}
