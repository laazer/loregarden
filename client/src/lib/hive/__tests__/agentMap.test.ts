/**
 * The place table and the honest call states (lg-hive-office-2784, S2 and S5).
 *
 * Changed from the version this replaces, because the behaviour changed:
 *   - `classifyTool`/`classifyLine` return `place`, not `station`, and an unmapped tool goes to
 *     the desk under its own name instead of silently to Shell (S2).
 *   - `buildAgentMap` counts over all eleven places, so `model.places` replaces `model.stations`.
 *   - "marks the last call failed on an error line" is gone: `ERR`/`FAIL` are about the run, not
 *     about a call, and the inference blamed whatever tool the run touched last (S4).
 *   - a call is no longer `done` because the next call arrived; it is `unknown` (S5).
 *   - `AgentMapModel.feed`/`.calls`, `MapCall.folded`/`.lineIndex`/`.agentName` and `foldFeed` are
 *     gone (S10). No component ever read them, and `foldFeed` was a second collapse rule that
 *     disagreed with `buildToolActs` on an `unknown`-state run. Every assertion they carried is
 *     kept: the place ones here, over `model.places`; the collapsing and `+N` ones in
 *     `toolActs.test.ts`. A call's own fields are asserted through `runCalls`, which stays
 *     exported because `buildToolActs` is its one caller.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import type { LogLine } from "../../../api/runLogTypes";
import { ACTIVE_LEDGER_STATUSES } from "../../ledgerStatus";
import { BUSIEST, BUSY, FAILED_CALL, FAILING } from "../../../test/hiveRunFixtures";
import * as mapModule from "../agentMap";
import {
  FEED_ROWS,
  MAP_PLACES,
  MAP_STATIONS,
  MAP_VIEW,
  RECENT_FINISH_MS,
  buildAgentMap,
  classifyLine,
  classifyTool,
  runCalls,
  selectMapAttempts,
  stationPoint,
  type MapRunInput,
} from "../agentMap";

const tool = (text: string, time = "10:00:00"): LogLine => ({ tag: "TOOL", text, time });
const line = (tag: string, text: string, time = "10:00:00"): LogLine => ({ tag, text, time });
const result = (time = "10:00:00") => tool("result\nsome output", time);
const failed = (time = "10:00:00") => tool("result · failed\nsome output", time);

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

const place = (model: ReturnType<typeof buildAgentMap>, id: string) =>
  model.places.find((p) => p.place.id === id);

/** One run's calls, read through the reducer `buildToolActs` itself consumes. */
const callsOf = (lines: LogLine[], overrides: Partial<MapRunInput> = {}) => {
  const input = run(lines, overrides);
  return runCalls(input, ACTIVE_LEDGER_STATUSES.has(input.status.toLowerCase()));
};

// ---- S2: one table decides the place -------------------------------------------------------------

describe("the place table", () => {
  it("holds eleven places, of which exactly ten are on the ring", () => {
    expect(MAP_PLACES).toHaveLength(11);
    expect(MAP_PLACES.filter((p) => p.ring)).toHaveLength(10);
    expect(MAP_PLACES.filter((p) => !p.ring).map((p) => p.id)).toEqual(["desk"]);
  });

  it("derives MAP_STATIONS as the ring rows in table order", () => {
    expect(MAP_STATIONS).toEqual(MAP_PLACES.filter((p) => p.ring));
    expect(MAP_STATIONS.map((s) => s.id)).toEqual([
      "inbox",
      "model",
      "memory",
      "control",
      "files",
      "shell",
      "network",
      "browser",
      "agents",
      "skills",
    ]);
  });

  it("gives every place a label and an id used once", () => {
    expect(new Set(MAP_PLACES.map((p) => p.id)).size).toBe(11);
    expect(MAP_PLACES.every((p) => p.label.length > 0 && p.hint.length > 0)).toBe(true);
    expect(MAP_PLACES.find((p) => p.id === "desk")?.label).toBe("Desk");
  });

  it("keeps the ten ring positions exactly where they are", () => {
    // S2-AC2. `stationPoint` divides the circle by the number of *ring* rows: an eleventh row in
    // the shared table must not become an eleventh division, or all ten existing points — and the
    // CSS geometry pinned to them — move. Asserted against a tenth-turn computed here, so the
    // test fails if the divisor becomes MAP_PLACES.length, and against the measured coordinates,
    // so it also fails if MAP_VIEW moves.
    const tenth = (index: number) => {
      const angle = (index / 10) * Math.PI * 2 - Math.PI / 2;
      return {
        x: MAP_VIEW.centre + Math.cos(angle) * MAP_VIEW.stationRadius,
        y: MAP_VIEW.centre + Math.sin(angle) * MAP_VIEW.stationRadius,
      };
    };
    const measured = [
      [200, 42],
      [292.8700698622, 72.1753148888],
      [350.2669295746, 151.1753148888],
      [350.2669295746, 248.8246851112],
      [292.8700698622, 327.8246851112],
      [200, 358],
      [107.1299301378, 327.8246851112],
      [49.7330704254, 248.8246851112],
      [49.7330704254, 151.1753148888],
      [107.1299301378, 72.1753148888],
    ];
    for (let i = 0; i < 10; i += 1) {
      expect(stationPoint(i)).toEqual(tenth(i));
      expect(stationPoint(i).x).toBeCloseTo(measured[i][0], 6);
      expect(stationPoint(i).y).toBeCloseTo(measured[i][1], 6);
    }
  });
});

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
  ])("%s %s goes to %s", (name, subject, expected) => {
    expect(classifyTool(name, subject).place).toBe(expected);
  });

  it("labels a shell call by its description, not its command", () => {
    expect(classifyTool("Bash", "pytest: Run the hive tests").label).toBe("Run the hive tests");
  });

  // S2-AC1. These seven are every tool name in the live corpus with no station: 255 of 27,319
  // call lines (0.93%) — TaskOutput 105, TaskStop 74, ScheduleWakeup 36, Monitor 23, ListAgents 14,
  // ReportFindings 2, SendMessage 1 — all of which landed silently at Shell.
  const STATIONLESS = [
    "TaskOutput",
    "TaskStop",
    "ScheduleWakeup",
    "Monitor",
    "ListAgents",
    "ReportFindings",
    "SendMessage",
  ];

  it.each(STATIONLESS)("%s goes to the desk under its own name, never to shell", (name) => {
    expect(classifyTool(name, "")).toEqual({ place: "desk", label: name });
    expect(classifyTool(name, "a subject").place).toBe("desk");
  });

  it("sends a tool nobody has ever heard of to the desk too, not to shell", () => {
    // The fallback is the point: `shell` was wrong for every unmapped tool, not only these seven.
    expect(classifyTool("SomeToolShippedNextWeek", "").place).toBe("desk");
    expect(classifyTool("SomeToolShippedNextWeek", "").label).toBe("SomeToolShippedNextWeek");
  });

  it("still sends a real shell command to shell", () => {
    // The desk fallback must not swallow Bash, which is 146 of 191 calls in the busiest run.
    expect(classifyTool("Bash", "pytest: Run tests").place).toBe("shell");
    expect(classifyTool("shell", "make").place).toBe("shell");
  });

  it("only ever returns an id the table holds", () => {
    const ids = new Set(MAP_PLACES.map((p) => p.id));
    for (const name of [...STATIONLESS, "Read", "Bash", "Task", "mcp__x__y", "", "???"]) {
      expect(ids.has(classifyTool(name, "").place)).toBe(true);
    }
  });
});

describe("classifyLine", () => {
  it("reads a call line written by the server", () => {
    expect(classifyLine(tool("call · Read · hive/cast.ts"))).toEqual({
      kind: "call",
      place: "files",
      tool: "Read",
      label: "hive/cast.ts",
      failed: false,
    });
  });

  it("reads a desk call line end to end, from the server's own text", () => {
    expect(classifyLine(tool("call · TaskOutput · agent_1"))).toMatchObject({
      kind: "call",
      place: "desk",
      tool: "TaskOutput",
    });
  });

  it("tells a clean result from a failed one", () => {
    // `result · failed` is the one marker the server writes (`tool_call_line.RESULT_FAILED`), and
    // this literal is the contract between the two halves of this ticket.
    expect(classifyLine(tool("result\nsome output"))).toEqual({ kind: "result", failed: false });
    expect(classifyLine(tool("result · failed\nsome output"))).toEqual({ kind: "result", failed: true });
  });

  it("reads the marker, not merely a dotted suffix on a result line", () => {
    // Today's corpus holds exactly one result headline, `result` (97,933 of them), so any second
    // spelling is new. Treating every `result · <x>` as a failure would invent failures from the
    // next thing anyone appends — a truncation note, a size, a duration.
    expect(classifyLine(tool("result · truncated\nbody"))).toEqual({ kind: "result", failed: false });
    expect(classifyLine(tool("result · 4.2 kB\nbody"))).toEqual({ kind: "result", failed: false });
  });

  it("does not read a failure out of a result body that merely says Error", () => {
    // 22,147 live result bodies carry "Error" or "exit code" as ordinary content (S4).
    expect(classifyLine(tool("result\nError: no such file\nexit code 1"))).toEqual({
      kind: "result",
      failed: false,
    });
  });

  it("treats a refusal as a failed inbox call", () => {
    expect(classifyLine(tool("Denied (out of scope): Write"))).toMatchObject({
      place: "inbox",
      failed: true,
    });
  });

  it("skips Auto-approved lines so a bridged call is not counted twice", () => {
    expect(classifyLine(tool("Auto-approved: Bash"))).toBeNull();
  });

  it("reads Codex's `$ command` shell lines", () => {
    expect(classifyLine(tool("$ git push origin main · rc 0"))).toMatchObject({ place: "network" });
    expect(classifyLine(tool("$ ls -la"))).toMatchObject({ place: "files" });
  });

  it("still reports ERR and FAIL as run-level errors, which no call reads", () => {
    expect(classifyLine(line("ERR", "boom"))).toEqual({ kind: "error" });
    expect(classifyLine(line("FAIL", "run failed"))).toEqual({ kind: "error" });
  });
});

// ---- S5: success only from a result line ---------------------------------------------------------

describe("call states", () => {
  const stateOf = (lines: LogLine[], overrides: Partial<MapRunInput> = {}) =>
    callsOf(lines, overrides).map((c) => c.state);

  it("is done only after a clean result line", () => {
    expect(stateOf([tool("call · Read · a.ts"), result()])).toEqual(["done"]);
  });

  it("is failed after a result · failed line", () => {
    expect(stateOf([tool("call · Read · a.ts"), failed()])).toEqual(["failed"]);
  });

  it("is running while a call has no result and no successor", () => {
    expect(stateOf([tool("call · Read · a.ts")])).toEqual(["running"]);
  });

  it("is unknown — never done — when the next call arrives with no result in between", () => {
    // Today's inference marked the first call `done` the moment the second arrived. Nothing in the
    // log says it succeeded.
    expect(stateOf([tool("call · Read · a.ts"), tool("call · Read · b.ts"), result()])).toEqual([
      "unknown",
      "done",
    ]);
  });

  it("is unknown, not running, for the last call of a run that has ended", () => {
    // Not `done` either: a run killed mid-call did not finish that call.
    expect(stateOf([tool("call · Read · a.ts")], { status: "succeeded" })).toEqual(["unknown"]);
    expect(stateOf([tool("call · Read · a.ts")], { status: "cancelled" })).toEqual(["unknown"]);
  });

  it("stays running for the last call of a run that is still at work", () => {
    expect(stateOf([tool("call · Read · a.ts")], { status: "awaiting_permission" })).toEqual(["running"]);
  });

  it("does not let a run-level ERR or FAIL blame the last call", () => {
    // S4-AC3 at the call level: `fcc2a8a7`'s ERR/FAIL arrive at seq 178-180, after 82 clean calls.
    expect(stateOf([tool("call · Bash · pytest"), result(), line("ERR", "boom"), line("FAIL", "run failed")])).toEqual(
      ["done"],
    );
    const model = buildAgentMap([
      run([tool("call · Bash · pytest"), result(), line("ERR", "boom")], { status: "failed" }),
    ]);
    expect(place(model, "shell")?.failures).toBe(0);
    expect(model.agents[0].failures).toBe(0);
  });

  it("produces no done state anywhere in a real log without a clean result line before it", () => {
    // S5-AC2, over every recorded fixture rather than a constructed case.
    for (const fixture of [BUSY, BUSIEST, FAILING, FAILED_CALL]) {
      const calls = callsOf([...fixture.lines], { status: fixture.status });
      let cleanResults = 0;
      let done = 0;
      for (const l of fixture.lines) {
        const head = l.text.split("\n", 1)[0];
        if (head === "result") cleanResults += 1;
      }
      for (const call of calls) if (call.state === "done") done += 1;
      expect(done).toBeLessThanOrEqual(cleanResults);
      expect(done).toBeGreaterThan(0);
    }
  });
});

describe("buildAgentMap", () => {
  it("keeps a call running until its result arrives", () => {
    const open = buildAgentMap([run([tool("call · Read · a.ts")])]);
    expect(place(open, "files")).toMatchObject({ uses: 1, running: 1 });

    const closed = buildAgentMap([run([tool("call · Read · a.ts"), result()])]);
    expect(closed.places.find((p) => p.place.id === "files")).toMatchObject({ uses: 1, running: 0 });
  });

  it("counts over all eleven places, so a desk call is never dropped", () => {
    // S2-AC1 asserted on `buildAgentMap`'s output, not only on `classifyTool`: the old
    // `if (!state) continue;` is exactly where a place missing from the counting table vanishes.
    expect(buildAgentMap([run([])]).places).toHaveLength(11);
    for (const name of ["TaskOutput", "TaskStop", "ScheduleWakeup", "Monitor", "ListAgents", "ReportFindings", "SendMessage"]) {
      const model = buildAgentMap([run([tool(`call · ${name} · x`), result()])]);
      expect(place(model, "desk")).toMatchObject({ uses: 1 });
      expect(place(model, "shell")?.uses).toBe(0);
      // Through the model a component actually reads: the desk is the only place with a use, and
      // every other place — Shell included — has none, so the call cannot have landed twice.
      expect(model.places.filter((p) => p.uses > 0).map((p) => p.place.id)).toEqual(["desk"]);
      expect(place(model, "desk")?.usesByRun).toEqual({ r1: 1 });
      expect(model.agents[0].calls).toBe(1);
      expect(callsOf([tool(`call · ${name} · x`), result()]).map((c) => [c.place, c.label, c.tool])).toEqual([
        ["desk", name, name],
      ]);
    }
  });

  it("counts a stretch of streamed THINK/OUT lines as one model step", () => {
    const lines = [line("THINK", "a"), line("THINK", "b"), line("OUT", "c"), tool("call · Read · a.ts")];
    expect(place(buildAgentMap([run(lines)]), "model")?.uses).toBe(1);
    // S9's anchor: this synthesised step is the *only* producer of `tool: "model"`, which is the
    // id `buildToolActs` filters on. Keyed on the place instead, it would also swallow a real
    // TodoWrite/ExitPlanMode/EnterPlanMode call, which classifies to the same place.
    const modelCalls = callsOf(lines).filter((c) => c.place === "model");
    expect(modelCalls.map((c) => [c.tool, c.label])).toEqual([["model", "thinking"]]);
    expect(callsOf([tool("call · TodoWrite · 3 items"), result()]).map((c) => [c.place, c.tool])).toEqual([
      ["model", "TodoWrite"],
    ]);
  });

  it("leaves nothing running on a finished run", () => {
    const model = buildAgentMap([run([tool("call · Read · a.ts")], { status: "succeeded" })]);
    expect(model.places.every((p) => p.running === 0)).toBe(true);
    expect(model.anyActive).toBe(false);
  });

  it("gives every call a stable id across polls", () => {
    const lines = [tool("call · Read · a.ts", "10:00:01"), tool("call · Read · a.ts", "10:00:01")];
    const first = callsOf(lines).map((c) => c.id);
    const again = callsOf([...lines, result("10:00:01")]).map((c) => c.id);
    expect(new Set(first).size).toBe(2);
    expect(again).toEqual(first);
  });

  it("tracks each run's spokes separately", () => {
    const model = buildAgentMap([
      run([tool("call · Read · a.ts")]),
      run([tool("call · Bash · pytest")], { runId: "r2", runCode: "run_b", agentId: "static_qa" }),
    ]);
    expect(place(model, "files")?.usesByRun).toEqual({ r1: 1 });
    expect(place(model, "shell")?.usesByRun).toEqual({ r2: 1 });
  });

  it("orders a run's calls by line order, never by their clock string", () => {
    // Log times are HH:MM:SS with no date, and run `c2113a06` spans 00:00:00-23:59:44, so a
    // `time.localeCompare` sort puts the end of a midnight-spanning run before its start.
    const lines = [
      tool("call · Read · before.ts", "23:59:58"),
      result("23:59:59"),
      tool("call · Bash · pytest: after midnight", "00:00:01"),
      result("00:00:02"),
    ];
    expect(callsOf(lines).map((c) => c.label)).toEqual(["before.ts", "after midnight"]);
    // The run's last call is the one that arrived later in the log, not the one with the later
    // clock string: a `localeCompare` sort would read "00:00:01" as the older of the two.
    expect(buildAgentMap([run(lines)]).agents[0].current?.label).toBe("after midnight");
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

// ---- S10: one model shape, and exactly one collapse rule -----------------------------------------

describe("the model a component actually reads", () => {
  const source = (name: string) =>
    readFileSync(join(__dirname, "..", name), "utf8");

  it("exposes only agents, places and anyActive", () => {
    // `feed` and `calls` had no reader: `buildAgentMap` has one caller (`HiveAgentMap.tsx:114`),
    // and between it and `HiveMapPicture.tsx` the model is read through these three fields alone.
    // A field only its own tests read is a second contract that drifts from the rendered one.
    expect(Object.keys(buildAgentMap([run([tool("call · Read · a.ts"), result()])])).sort()).toEqual([
      "agents",
      "anyActive",
      "places",
    ]);
  });

  it("gives a call no feed-row fields of its own", () => {
    // `folded` was the old fold rule's counter, `lineIndex`/`agentName` the feed row's columns.
    // Collapsing now happens once, in `buildToolActs`, which counts into `callsInLog`.
    const call = callsOf([tool("call · Read · a.ts"), result()])[0];
    expect(Object.keys(call).sort()).toEqual(["id", "label", "place", "runId", "state", "time", "tool"]);
  });

  it("keeps no second collapse rule anywhere in the map or the pane", () => {
    // S10's own grep, as a test, because a rule that is merely unexported still drifts: `foldFeed`
    // folded only `done` pairs, so an `unknown`-state run read as N rows here and one act there.
    const forbidden = /foldFeed|\.folded|lineIndex|agentName/;
    const roots = [join(__dirname, ".."), join(__dirname, "..", "..", "..", "components", "dashboard", "hive")];
    const offenders: string[] = [];
    for (const root of roots) {
      for (const entry of readdirSync(root, { withFileTypes: true })) {
        if (!entry.isFile() || !/\.(ts|tsx|css)$/.test(entry.name)) continue;
        const text = readFileSync(join(root, entry.name), "utf8");
        if (forbidden.test(text)) offenders.push(entry.name);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("exports no fold or collapse function to replace the one that went", () => {
    // Kills the re-export: the only collapse rule is `buildToolActs`'s, and it lives elsewhere.
    expect(Object.keys(mapModule).filter((n) => /fold|collapse|feedRows/i.test(n))).toEqual([]);
    expect(source("agentMap.ts")).not.toMatch(/\bfunction\s+(fold|collapse)/i);
  });

  it("keeps FEED_ROWS, because the pane still caps its feed with it", () => {
    expect(FEED_ROWS).toBe(16);
    const pane = readFileSync(
      join(__dirname, "..", "..", "..", "components", "dashboard", "hive", "HiveAgentMap.tsx"),
      "utf8",
    );
    expect(pane).toMatch(/FEED_ROWS/);
  });
});
