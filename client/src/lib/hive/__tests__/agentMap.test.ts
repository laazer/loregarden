import type { LogLine } from "../../../api/runLogTypes";
import {
  FEED_ROWS,
  RECENT_FINISH_MS,
  buildAgentMap,
  classifyLine,
  classifyTool,
  selectMapAttempts,
  type MapRunInput,
} from "../agentMap";

const tool = (text: string, time = "10:00:00"): LogLine => ({ tag: "TOOL", text, time });
const line = (tag: string, text: string, time = "10:00:00"): LogLine => ({ tag, text, time });

function run(lines: LogLine[], overrides: Partial<MapRunInput> = {}): MapRunInput {
  return {
    runId: "r1",
    runCode: "run_a",
    agentId: "backend_implementer",
    skillName: "",
    stageKey: "implement",
    status: "running",
    lines,
    ...overrides,
  };
}

describe("classifyTool", () => {
  it.each([
    ["Read", "hive/cast.ts", "files"],
    ["Grep", "foo", "files"],
    ["Bash", "git status: Show status", "files"],
    ["Bash", "git push: Push branch", "network"],
    ["Bash", "gh pr: Open PR", "network"],
    ["Bash", "pytest: Run tests", "shell"],
    ["WebFetch", "docs.example.com", "network"],
    ["Task", "Find the reader", "agents"],
    ["Skill", "refactor", "skills"],
    ["AskUserQuestion", "", "inbox"],
    ["mcp__loregarden__loregarden_search_memory", "", "memory"],
    ["mcp__loregarden__loregarden_append_learning", "", "memory"],
    ["mcp__loregarden__loregarden_get_ticket", "", "control"],
    ["mcp__Claude_Browser__navigate", "", "browser"],
    ["mcp__github__create_issue", "", "network"],
  ])("%s %s goes to %s", (name, subject, station) => {
    expect(classifyTool(name, subject).station).toBe(station);
  });

  it("labels a shell call by its description, not its command", () => {
    expect(classifyTool("Bash", "pytest: Run the hive tests").label).toBe("Run the hive tests");
  });
});

describe("classifyLine", () => {
  it("reads a call line written by the server", () => {
    expect(classifyLine(tool("call · Read · hive/cast.ts"))).toEqual({
      kind: "call",
      station: "files",
      tool: "Read",
      label: "hive/cast.ts",
      failed: false,
    });
  });

  it("treats a result as the end of a call, and a refusal as a failed inbox call", () => {
    expect(classifyLine(tool("result\nsome output"))).toEqual({ kind: "result" });
    expect(classifyLine(tool("Denied (out of scope): Write"))).toMatchObject({ station: "inbox", failed: true });
  });

  it("skips Auto-approved lines so a bridged call is not counted twice", () => {
    expect(classifyLine(tool("Auto-approved: Bash"))).toBeNull();
  });

  it("reads Codex's `$ command` shell lines", () => {
    expect(classifyLine(tool("$ git push origin main · rc 0"))).toMatchObject({ station: "network" });
    expect(classifyLine(tool("$ ls -la"))).toMatchObject({ station: "files" });
  });
});

describe("buildAgentMap", () => {
  it("keeps a call running until its result arrives", () => {
    const open = buildAgentMap([run([tool("call · Read · a.ts")])]);
    expect(open.stations.find((s) => s.station.id === "files")).toMatchObject({ uses: 1, running: 1 });

    const closed = buildAgentMap([run([tool("call · Read · a.ts"), tool("result\nx")])]);
    expect(closed.stations.find((s) => s.station.id === "files")).toMatchObject({ uses: 1, running: 0 });
  });

  it("counts a stretch of streamed THINK/OUT lines as one model step, kept out of the feed", () => {
    const model = buildAgentMap([
      run([line("THINK", "a"), line("THINK", "b"), line("OUT", "c"), tool("call · Read · a.ts")]),
    ]);
    expect(model.stations.find((s) => s.station.id === "model")?.uses).toBe(1);
    expect(model.feed.map((c) => c.station)).toEqual(["files"]);
  });

  it("marks the last call failed on an error line", () => {
    const model = buildAgentMap([run([tool("call · Bash · pytest"), line("ERR", "boom")])]);
    expect(model.stations.find((s) => s.station.id === "shell")?.failures).toBe(1);
    expect(model.agents[0].failures).toBe(1);
  });

  it("leaves nothing running on a finished run", () => {
    const model = buildAgentMap([run([tool("call · Read · a.ts")], { status: "succeeded" })]);
    expect(model.stations.every((s) => s.running === 0)).toBe(true);
    expect(model.anyActive).toBe(false);
  });

  it("folds back-to-back finished calls at one station and caps the feed", () => {
    const lines: LogLine[] = [];
    for (let i = 0; i < 40; i += 1) {
      lines.push(tool(`call · Read · f${i}.ts`, `10:00:${String(i).padStart(2, "0")}`), tool("result\nx"));
    }
    const folded = buildAgentMap([run(lines)]);
    expect(folded.feed).toHaveLength(1);
    expect(folded.feed[0]).toMatchObject({ label: "f39.ts", folded: 39 });

    const varied = lines.map((l, i) => (i % 4 === 0 ? tool(`call · Bash · pytest`, l.time) : l));
    expect(buildAgentMap([run(varied)]).feed.length).toBeLessThanOrEqual(FEED_ROWS);
  });

  it("gives every call a stable id across polls", () => {
    const lines = [tool("call · Read · a.ts", "10:00:01"), tool("call · Read · a.ts", "10:00:01")];
    const first = buildAgentMap([run(lines)]).calls.map((c) => c.id);
    const again = buildAgentMap([run([...lines, tool("result\nx")])]).calls.map((c) => c.id);
    expect(new Set(first).size).toBe(2);
    expect(again).toEqual(first);
  });

  it("tracks each run's spokes separately", () => {
    const model = buildAgentMap([
      run([tool("call · Read · a.ts")]),
      run([tool("call · Bash · pytest")], { runId: "r2", runCode: "run_b", agentId: "static_qa" }),
    ]);
    expect(model.stations.find((s) => s.station.id === "files")?.usesByRun).toEqual({ r1: 1 });
    expect(model.stations.find((s) => s.station.id === "shell")?.usesByRun).toEqual({ r2: 1 });
  });
});

describe("selectMapAttempts", () => {
  const now = Date.parse("2026-11-10T12:00:00Z");
  const attempt = (id: string, status: string, started: string, finished: string | null = null) => ({
    run_id: id,
    run_code: `run_${id}`,
    agent_id: "a",
    skill_name: "",
    status,
    started_at: started,
    finished_at: finished,
  });

  it("draws running lanes and lanes that just finished beside them", () => {
    const justNow = new Date(now - RECENT_FINISH_MS / 2).toISOString();
    const longAgo = new Date(now - RECENT_FINISH_MS * 3).toISOString();
    const ledger = {
      visits: [
        {
          stage_key: "review",
          attempts: [
            attempt("a", "running", longAgo),
            attempt("b", "succeeded", longAgo, justNow),
            attempt("c", "succeeded", longAgo, longAgo),
          ],
        },
      ],
    };
    expect(selectMapAttempts(ledger, now).map((a) => a.runId)).toEqual(["a", "b"]);
  });

  it("falls back to the most recent run when nothing is at work", () => {
    const ledger = {
      visits: [
        { stage_key: "spec", attempts: [attempt("old", "succeeded", "2026-11-10T09:00:00Z", "2026-11-10T09:10:00Z")] },
        { stage_key: "test", attempts: [attempt("new", "failed", "2026-11-10T10:00:00Z", "2026-11-10T10:05:00Z")] },
      ],
    };
    expect(selectMapAttempts(ledger, now)).toEqual([
      expect.objectContaining({ runId: "new", stageKey: "test" }),
    ]);
    expect(selectMapAttempts({ visits: [] }, now)).toEqual([]);
  });
});
