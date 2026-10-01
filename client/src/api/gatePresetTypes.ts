/** Gate commands offered for a repository's toolchains (`/api/gate-presets`). */

export type GateKind = "lint" | "format" | "typecheck" | "test";

export interface PresetCommand {
  /** The gate command exactly as it would be saved: no shell, run from the checkout root. */
  command: string;
  label: string;
  kind: GateKind;
  /** Ticked to start with, for a detected toolchain; tests never are. */
  default_on: boolean;
}

export interface ToolchainPreset {
  key: "python" | "node" | "rust" | "go" | "godot";
  label: string;
  /** Relative to the repository root; "." for the root. */
  directory: string;
  /** False for toolchains offered generically because nothing at the path uses them yet. */
  detected: boolean;
  commands: PresetCommand[];
  /** What the commands assume, or why one was left out; "" when nothing needs saying. */
  note: string;
}

export interface GatePresets {
  repo_root: string;
  toolchains: ToolchainPreset[];
}
