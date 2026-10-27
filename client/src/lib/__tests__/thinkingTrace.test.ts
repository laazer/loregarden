import { parseThinkingBlocks, summarizeThinking } from "../thinkingTrace";

const trace = (...steps: string[]) => steps.map((step) => `· ${step}`).join("\n");

describe("parseThinkingBlocks", () => {
  it("separates tool steps from the prose between them", () => {
    expect(parseThinkingBlocks("Look.\n\n· Read · a.py\n\nDone.")).toEqual([
      { kind: "prose", text: "Look." },
      { kind: "step", text: "Read · a.py" },
      { kind: "prose", text: "Done." },
    ]);
  });
});

describe("summarizeThinking", () => {
  it("names the trailing run of one tool by file name, with the step count", () => {
    const content = trace(
      "Grep · spawn",
      "Read · /repo/server/loregarden/agents/cli_adapters.py",
      "Read · /repo/server/loregarden/agents/agent_spawn.py",
    );
    expect(summarizeThinking(content, "Read · /repo/server/loregarden/agents/agent_spawn.py")).toBe(
      "Reading cli_adapters.py and agent_spawn.py · 3 steps",
    );
  });

  it("keeps the two most recent targets of a long run and counts the rest", () => {
    const content = trace("Read · a.py", "Read · b.py", "Read · c.py", "Read · d.py");
    expect(summarizeThinking(content, "")).toBe("Reading c.py, d.py and 2 more · 4 steps");
  });

  it("names a file once however often it is read", () => {
    expect(summarizeThinking(trace("Read · a.py", "Read · a.py"), "")).toBe("Reading a.py · 2 steps");
  });

  it("reads a shell command as what it does, past cd, timeout and env", () => {
    const read = (command: string) => summarizeThinking(trace(`Bash · ${command}`), "");
    expect(read("cd /repo/server && sed -n 568,612p loregarden/services/runner.py")).toBe("Reading runner.py · 1 step");
    expect(read("cat a.md | head -40")).toBe("Reading a.md · 1 step");
    expect(read('grep -rn "useChoiceOptions" client/src | head')).toBe("Searching for “useChoiceOptions” · 1 step");
    expect(read("ls /repo/project_board/ 2>/dev/null | head -50")).toBe("Looking in project_board · 1 step");
    expect(read("cd ~/repo && git log --oneline -5")).toBe("Running git log · 1 step");
    expect(read('sqlite3 data/x.db "select 1"')).toBe("Querying the database · 1 step");
    expect(read("curl -s http://127.0.0.1:8000/openapi.json")).toBe("Fetching 127.0.0.1 · 1 step");
    expect(read("timeout 300 npx tsc --noEmit")).toBe("Running npx · 1 step");
    expect(read("FOO=1 uv run pytest -q")).toBe("Running uv · 1 step");
    expect(read("env -u GIT_DIR uv run pytest")).toBe("Running uv · 1 step");
    expect(read("git -C /repo/.worktrees/x log -3")).toBe("Running git log · 1 step");
    expect(read('grep -n "a\\|b" x.py')).toBe("Searching for “a” · 1 step");
    expect(read("for f in a b; do echo $f; done")).toBe("Running a script · 1 step");
  });

  it("merges a read through the shell with a read through Read", () => {
    const content = trace("Read · /repo/cli_adapters.py", "Bash · sed -n 1,80p /repo/agent_spawn.py");
    expect(summarizeThinking(content, "")).toBe("Reading cli_adapters.py and agent_spawn.py · 2 steps");
  });

  it("says thinking or writing while he is, rather than his last tool", () => {
    const content = trace("Read · a.py");
    expect(summarizeThinking(content, "Thinking")).toBe("Thinking · 1 step");
    expect(summarizeThinking(content, "Writing the reply")).toBe("Writing the reply · 1 step");
    expect(summarizeThinking("Just reasoning.", "Thinking")).toBe("Thinking");
  });

  it("names a tool that has started before its call is written", () => {
    expect(summarizeThinking(trace("Read · a.py"), "Grep")).toBe("Searching for… · 1 step");
    expect(summarizeThinking("", "Read · runner.py")).toBe("Reading runner.py");
  });

  it("reads loregarden's MCP tools and sites in words", () => {
    expect(summarizeThinking(trace("mcp__loregarden__loregarden_get_ticket · lg-x-1"), "")).toBe(
      "Calling get ticket lg-x-1 · 1 step",
    );
    // A ticket's uuid is noise at full length; eight characters still tell
    // two apart, and the trace keeps the whole id.
    expect(
      summarizeThinking(trace("mcp__loregarden__loregarden_land_ticket · ef5d0d8a-c811-4c35-9eac-c114540efe47"), ""),
    ).toBe("Calling land ticket ef5d0d8a · 1 step");
    expect(summarizeThinking(trace("WebFetch · https://docs.example.com/a/b"), "")).toBe(
      "Fetching docs.example.com · 1 step",
    );
    expect(summarizeThinking(trace("Grep · def spawn"), "")).toBe("Searching for “def spawn” · 1 step");
  });

  it("drops targets for tools whose argument says nothing to the operator", () => {
    expect(summarizeThinking(trace("ToolSearch · select:a__b,c__d"), "")).toBe("Loading tools · 1 step");
    expect(summarizeThinking(trace("AskUserQuestion"), "")).toBe("Asking you a question · 1 step");
  });
});
