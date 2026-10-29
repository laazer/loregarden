import type { InstallState, WorkspaceIntegration } from "../../api/localInstancesTypes";
import type { WorkspaceSummary } from "../../api/types";
import type { RepositoryState } from "../../api/workspaceRepositoryTypes";
import { gatesCell, workspaceNeeds } from "../workspaceNeeds";

const summary = (repo_state: RepositoryState) => ({ repo_state }) as WorkspaceSummary;
const integration = (hooks: InstallState, docs: InstallState, detail = ""): WorkspaceIntegration => ({
  slug: "w",
  repo_root: "/w",
  installers: [
    { installer: "hooks", state: hooks, detail },
    { installer: "docs", state: docs, detail: "" },
  ],
});

describe("workspaceNeeds", () => {
  it("needs nothing when both blocks are current", () => {
    expect(workspaceNeeds(summary("repository"), integration("current", "current"))).toEqual([]);
  });

  it("offers a pull request for each missing or outdated block", () => {
    const needs = workspaceNeeds(summary("repository"), integration("outdated", "missing"));
    expect(needs.map((need) => [need.label, need.fix])).toEqual([
      ["Gates outdated", { kind: "pull_request", installer: "hooks", label: "Update Gates…" }],
      ["AGENTS.md missing", { kind: "pull_request", installer: "docs", label: "Add AGENTS.md…" }],
    ]);
  });

  it("asks nothing of a block that cannot be installed, or of loregarden itself", () => {
    expect(workspaceNeeds(summary("repository"), integration("unavailable", "built_in"))).toEqual([]);
  });

  it("offers to create a repository where a folder can hold one, and says only that", () => {
    const needs = workspaceNeeds(summary("missing"), integration("unavailable", "unavailable"));
    expect(needs).toEqual([
      { id: "repo", label: "Repo missing", fix: { kind: "create_repository", label: "Create repository…" } },
    ]);
  });

  it("explains, with no button, a folder loregarden will not initialize", () => {
    const [need] = workspaceNeeds(summary("not_a_repository"), undefined);
    expect(need.label).toBe("Not a git repo");
    expect(need.fix).toBeUndefined();
    expect(need.explain).toMatch(/git init/);
  });
});

describe("gatesCell", () => {
  it("names the hook block's state, with the reason when it cannot be installed", () => {
    expect(gatesCell(integration("current", "current"))).toEqual({ word: "Installed", detail: "" });
    expect(gatesCell(integration("unavailable", "current", "no lefthook.yml"))).toEqual({
      word: "n/a",
      detail: "no lefthook.yml",
    });
    expect(gatesCell(undefined)).toBeNull();
  });
});
