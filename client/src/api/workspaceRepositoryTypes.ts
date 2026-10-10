import type { WorkspaceSummary } from "./types";

/** What is at a workspace's path; `missing` and `empty` can have a repository created there. */
export type RepositoryState = "repository" | "missing" | "empty" | "not_a_repository" | "inside_repository";

/** States `POST /api/workspaces/{slug}/repository` can create a repository in. */
export const INITIALIZABLE_REPOSITORY_STATES: ReadonlySet<RepositoryState> = new Set(["missing", "empty"]);

export interface RepositoryProbe {
  repo_root: string;
  state: RepositoryState;
  /** Why, in words — what is there and what can be done with it. */
  detail: string;
}

export interface WorkspaceRepositoryCreated extends WorkspaceSummary {
  /** Empty when the repository is fully set up; otherwise what the operator still has to run. */
  follow_up: string;
}
