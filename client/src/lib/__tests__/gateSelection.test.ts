import { classifyGateCommands, guardrailScript } from "../gateSelection";

const GUARDRAIL = "bash {loregarden_root}/.lefthook/scripts/workspace-gates.sh --repo {workspace_root} --scope worktree";
const PRESET = "uv run --directory server ruff check";
const SAME_TOOL_OTHER_SPELLING = 'bash -c "cd server && uv run ruff check ."';

describe("classifyGateCommands", () => {
  it("keeps every configured command, by where it came from, in order", () => {
    const running = classifyGateCommands([GUARDRAIL, SAME_TOOL_OTHER_SPELLING, PRESET], new Set([PRESET]));
    expect(running).toEqual({
      presets: [PRESET],
      guardrails: [GUARDRAIL],
      // Matching a preset is exact: another spelling of the same check is listed, not dropped.
      custom: [SAME_TOOL_OTHER_SPELLING],
    });
  });

  it("returns nothing for a workspace with no gates", () => {
    expect(classifyGateCommands([], new Set([PRESET]))).toEqual({ presets: [], guardrails: [], custom: [] });
  });
});

describe("guardrailScript", () => {
  it("names the script a guardrail command runs", () => {
    expect(guardrailScript(GUARDRAIL)).toBe("workspace-gates.sh");
  });
});
