/**
 * What a workspace still needs from loregarden, each with the one fix for it.
 *
 * Read from two answers the page already has: the workspace row (is there a
 * repository at its path?) and the installers' `--check` (are the gates and
 * the AGENTS.md section there, and current?). The fix for a missing or stale
 * block is a pull request in that repository, never an edit to its checkout;
 * the fix for a missing repository is creating one, where a folder can hold it.
 */

import type {
  InstallState,
  WorkspaceInstaller,
  WorkspaceIntegration,
} from "../api/localInstancesTypes";
import type { WorkspaceSummary } from "../api/types";
import { INITIALIZABLE_REPOSITORY_STATES, type RepositoryState } from "../api/workspaceRepositoryTypes";

export type WorkspaceFix =
  | { kind: "pull_request"; installer: WorkspaceInstaller; label: string }
  | { kind: "create_repository"; label: string };

export interface WorkspaceNeed {
  id: string;
  /** "AGENTS.md outdated", "repo missing". */
  label: string;
  /** Said when there is no button: what the operator does instead. */
  explain?: string;
  fix?: WorkspaceFix;
}

const BLOCK_NAME: Record<WorkspaceInstaller, string> = { hooks: "Gates", docs: "AGENTS.md" };

/** The repository problems, in the words of the workspace setup card they replace. */
const REPO_NEED: Record<Exclude<RepositoryState, "repository">, { label: string; explain: string }> = {
  missing: { label: "Repo missing", explain: "Nothing exists at this path yet." },
  empty: { label: "Repo empty", explain: "This folder is empty." },
  not_a_repository: {
    label: "Not a git repo",
    explain: "The folder has files but no repository. Run `git init` there yourself.",
  },
  inside_repository: {
    label: "Inside another repo",
    explain: "Point the workspace at that repository's root instead.",
  },
};

/** What each install state needs, if anything; `unavailable` and `built_in` need nothing a button can give. */
const BLOCK_NEED: Partial<Record<InstallState, (name: string) => { label: string; fix: string }>> = {
  missing: (name) => ({ label: `${name} missing`, fix: `Add ${name}…` }),
  outdated: (name) => ({ label: `${name} outdated`, fix: `Update ${name}…` }),
};

export function workspaceNeeds(
  summary: WorkspaceSummary | undefined,
  integration: WorkspaceIntegration | undefined,
): WorkspaceNeed[] {
  const needs: WorkspaceNeed[] = [];
  const repoState = summary?.repo_state;
  if (repoState && repoState !== "repository") {
    const need = REPO_NEED[repoState];
    needs.push(
      INITIALIZABLE_REPOSITORY_STATES.has(repoState)
        ? { id: "repo", label: need.label, fix: { kind: "create_repository", label: "Create repository…" } }
        : { id: "repo", ...need },
    );
    // No repository: the installers can only say so too, so they add nothing.
    return needs;
  }
  for (const installer of integration?.installers ?? []) {
    const need = BLOCK_NEED[installer.state]?.(BLOCK_NAME[installer.installer]);
    if (need) {
      needs.push({
        id: installer.installer,
        label: need.label,
        fix: { kind: "pull_request", installer: installer.installer, label: need.fix },
      });
    }
  }
  return needs;
}

const GATES_WORD: Record<InstallState, string> = {
  current: "Installed",
  missing: "Missing",
  outdated: "Outdated",
  unavailable: "n/a",
  built_in: "Built in",
};

/** The Gates column: the hook block's state in a word, and its detail when the word is "n/a". */
export function gatesCell(integration: WorkspaceIntegration | undefined): { word: string; detail: string } | null {
  const hooks = integration?.installers.find((installer) => installer.installer === "hooks");
  if (!hooks) return null;
  return { word: GATES_WORD[hooks.state], detail: hooks.state === "unavailable" ? hooks.detail : "" };
}
