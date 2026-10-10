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
