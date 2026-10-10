/** Transition-gate config as the orchestration profile API serves it (Gate Studio). */

export interface OrchestrationProfileView {
  slug: string;
  name: string;
  driver: string;
  workflow_template: string;
  orchestrator_skill: string;
  /** Whether gates can actually run: switched on, and something runnable resolves. */
  gates_enabled: boolean;
  /** The raw on/off switch the editor toggles; differs from `gates_enabled` when nothing would run. */
  gates_configured: boolean;
  gates_commands: string[];
  gates_transition_script: string;
  /** The script a transition would run, relative to the workspace root, or "" when none exists. */
  gates_transition_script_resolved: string;
  gates_autofix_commands: string[];
  gates_autofix_agent_fallback: boolean;
  gates_autofix_max_agent_attempts: number;
  /** Every placeholder a gate command may use, mapped to a sample value. */
  gates_placeholders: Record<string, string>;
  /** The fallback profile's workspace-agnostic checks, offered as one-click adds. */
  gates_suggested_commands: string[];
  max_stages_per_run: number;
  /** The profile file these settings live in, repo-relative; null when no file stores them yet. */
  source_path: string | null;
}

export interface GatesConfigUpdate {
  enabled: boolean;
  commands: string[];
  transition_script: string;
  autofix_commands?: string[];
  autofix_agent_fallback?: boolean;
  autofix_max_agent_attempts?: number;
}

export type GateTestOutcome = "passed" | "skipped" | "disabled" | "failed" | "unavailable";

export interface GateTestCommandResult {
  template: string;
  command: string;
  outcome: GateTestOutcome;
  message: string;
  stdout: string;
  stderr: string;
  duration_ms: number;
}

export interface GateTestReport {
  repo_root: string;
  transition: string;
  results: GateTestCommandResult[];
}
