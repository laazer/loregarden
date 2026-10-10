import {
  expandGateCommand,
  gateCommandLabel,
  hasUnbalancedQuotes,
  unknownPlaceholders,
} from "../gateCommand";

describe("gateCommand", () => {
  it("names a check by the script it runs, not its launcher", () => {
    expect(
      gateCommandLabel(
        "bash {loregarden_root}/.lefthook/scripts/server_python.sh {loregarden_root}/.lefthook/scripts/py_organization_check.py --repo {workspace_root}",
      ),
    ).toBe("py_organization_check.py");
    expect(gateCommandLabel("npx oxlint --deny-warnings")).toBe("oxlint");
    expect(gateCommandLabel("ruff check .")).toBe("ruff check .");
    expect(gateCommandLabel('bash -c "cd server && uv run ruff format --check ."')).toBe(
      "ruff format",
    );
    expect(gateCommandLabel("   ")).toBe("Empty check");
  });

  it("finds placeholders the server doesn't know", () => {
    const known = { workspace_root: "/r", external_id: "X-1" };
    expect(unknownPlaceholders("ruff {workspace_root} {ticket} {ticket}", known)).toEqual(["ticket"]);
  });

  it("expands known placeholders and leaves unknown ones verbatim", () => {
    expect(expandGateCommand("run {external_id} {nope}", { external_id: "X-1" })).toBe(
      "run X-1 {nope}",
    );
  });

  it("detects an unclosed quote the way shlex would reject it", () => {
    expect(hasUnbalancedQuotes('echo "hi')).toBe(true);
    expect(hasUnbalancedQuotes("echo 'it\"s'")).toBe(false);
    expect(hasUnbalancedQuotes('echo "a \\" b"')).toBe(false);
    expect(hasUnbalancedQuotes("echo it\\'s")).toBe(false);
  });
});
