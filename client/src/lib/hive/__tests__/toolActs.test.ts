/**
 * The act model: one real tool call becomes one physical act, without lying about time.
 * lg-hive-office-2784, S2 (the actor), S3 (one act per stretch, and the bound), S4 (a failure is
 * never hidden), S5 (success only from a result line), S6 (the run's status wins), S7 (activity).
 *
 * The contract these tests hold `client/src/lib/hive/toolActs.ts` to:
 *
 *   buildToolActs(run: ToolActRunInput, nowMs: number, prevShownId: string | null): ToolActsModel
 *
 *   ToolActRunInput  { runId, runCode, agentId, stageKey, status, adapter, callLinesExpected,
 *                      startedAt, lines }
 *   ToolAct          { id, runId, agentId, place, tool, label, state, callsInLog,
 *                      firstAtMs, lastAtMs }
 *   ToolActsModel    { runId, agentId, acts (oldest first), shown, lagMs, failuresSkipped,
 *                      activity, phase }
 *   ToolActState     "running" | "done" | "failed" | "unknown"
 *   RunActivity      "calls" | "no-calls-yet" | "unknown"
 *   RunPhase         "active" | "waiting" | "ended"
 *
 * `lagMs` is the model's own presentation lag: how long until the newest act in the log becomes
 * the shown one. 0 when it already is; `ACT_DWELL_MS` while an unshown failure holds the floor.
 * It is never a function of how many acts arrived — that is the difference between this and a
 * queue-and-drain design, which on the measured burst of 40 calls in 10 s would end ~34 s behind.
 */

import type { LogLine } from "../../../api/runLogTypes";
import { BUSIEST, BUSY, FAILED_CALL, FAILING, NO_CALLS, asActRun } from "../../../test/hiveRunFixtures";
import { MAP_PLACES, runCalls } from "../agentMap";
import {
  ACT_DWELL_MS,
  ACT_LAG_BUDGET_MS,
  buildToolActs,
  type ToolAct,
  type ToolActRunInput,
  type ToolActsModel,
} from "../toolActs";

const NOW = Date.parse("2026-11-20T16:00:00Z");
const START = "2026-11-20T10:00:00Z";

const tool = (text: string, time: string): LogLine => ({ tag: "TOOL", text, time });
const call = (name: string, subject: string, time: string) => tool(`call · ${name} · ${subject}`, time);
const result = (time: string) => tool("result\nok", time);
const failedResult = (time: string) => tool("result · failed\nboom", time);

function run(lines: LogLine[], overrides: Partial<ToolActRunInput> = {}): ToolActRunInput {
  return {
    runId: "r1",
    runCode: "run_a",
    agentId: "backend_implementer",
    stageKey: "implement",
    status: "running",
    adapter: "claude",
    callLinesExpected: true,
    startedAt: START,
    lines,
    ...overrides,
  };
}

const build = (input: ToolActRunInput, now = NOW, prevShownId: string | null = null): ToolActsModel =>
  buildToolActs(input, now, prevShownId);

const fixtureRun = (f: typeof BUSY, overrides: Partial<ToolActRunInput> = {}) =>
  asActRun(f, overrides) as ToolActRunInput;

// ---- S3: the bound is a named constant, and it is small -------------------------------------------

describe("the stated bound", () => {
  it("is 600 ms of dwell and 2000 ms of budget", () => {
    expect(ACT_DWELL_MS).toBe(600);
    expect(ACT_LAG_BUDGET_MS).toBe(2000);
  });

  it("cannot be raised past 3000 ms without this test failing", () => {
    // S3-AC2. The guard exists so the budget cannot be widened quietly to make a slow model pass.
    expect(ACT_LAG_BUDGET_MS).toBeLessThanOrEqual(3000);
    expect(ACT_DWELL_MS).toBeLessThan(ACT_LAG_BUDGET_MS);
  });

  it("states the operator-visible figure in the module's own words", async () => {
    // The criterion asks for the ~4 s figure (budget + one POLL_MS) to be written down where the
    // next reader finds it. Read off the file rather than trusted to a comment nobody checks.
    const fs = await import("node:fs");
    const path = await import("node:path");
    const source = fs.readFileSync(path.join(__dirname, "../toolActs.ts"), "utf8");
    const docstring = source.slice(0, source.indexOf("*/"));
    expect(docstring).toMatch(/2000|ACT_LAG_BUDGET_MS/);
    expect(docstring).toMatch(/\b4\s*s|\b4 seconds/);
  });
});

// ---- S2: the actor is the run ---------------------------------------------------------------------

describe("the actor", () => {
  it("is the run itself, on every act of every fixture", () => {
    // S2-AC4. Nothing re-attributes a call by where it happened.
    for (const fixture of [BUSY, BUSIEST, FAILING, FAILED_CALL]) {
      const input = fixtureRun(fixture);
      const model = build(input);
      expect(model.runId).toBe(input.runId);
      expect(model.agentId).toBe(input.agentId);
      expect(model.acts.length).toBeGreaterThan(0);
      for (const act of model.acts) {
        expect(act.runId).toBe(input.runId);
        expect(act.agentId).toBe(input.agentId);
      }
    }
  });

  it("keeps two runs' acts apart even when they call the same tool in the same second", () => {
    const lines = [call("Read", "a.ts", "10:00:01"), result("10:00:01")];
    const one = build(run(lines));
    const two = build(run(lines, { runId: "r2", runCode: "run_b", agentId: "static_qa" }));
    expect(one.acts[0].agentId).toBe("backend_implementer");
    expect(two.acts[0].agentId).toBe("static_qa");
    expect(one.acts[0].id).not.toBe(two.acts[0].id);
  });

  it("exports no function that turns a place into a character or a crew", async () => {
    // S2-AC4's second half, and the reason this module exists separately: `crewForStation(skin,
    // station)` in worldModel.ts is the live violation, and the act model must not grow one.
    const mod = await import("../toolActs");
    for (const [name, value] of Object.entries(mod)) {
      expect(name).not.toMatch(/crew|character|cast|actorFor|bodyFor|skin/i);
      if (typeof value !== "function") continue;
      for (const id of MAP_PLACES.map((p) => p.id)) {
        // Called with a place id alone, nothing here may answer with a character.
        let out: unknown;
        try {
          out = (value as (x: unknown) => unknown)(id);
        } catch {
          continue; // a function that refuses a bare place id is exactly what is wanted
        }
        expect(typeof out === "string" && out !== id && out.length > 0).toBe(false);
      }
    }
  });

  it("emits a place id and never a tile or a coordinate", () => {
    // The floor (lg-hive-office-2785) places a place; this model does not know where one is.
    const ids = new Set<string>(MAP_PLACES.map((p) => p.id));
    for (const act of build(fixtureRun(BUSIEST)).acts) {
      expect(ids.has(act.place)).toBe(true);
      expect(act).not.toHaveProperty("x");
      expect(act).not.toHaveProperty("y");
      expect(act).not.toHaveProperty("tile");
    }
  });
});

// ---- S3: one act per continuous stretch ----------------------------------------------------------

describe("one act per continuous stretch", () => {
  it("folds consecutive calls at one place into one act carrying callsInLog", () => {
    const lines: LogLine[] = [];
    for (let i = 0; i < 5; i += 1) {
      lines.push(call("Read", `f${i}.ts`, `10:00:0${i}`), result(`10:00:0${i}`));
    }
    const model = build(run(lines));
    expect(model.acts).toHaveLength(1);
    expect(model.acts[0]).toMatchObject({ place: "files", callsInLog: 5, state: "done" });
  });

  it("starts a new act when the place changes, and back again", () => {
    const model = build(
      run([
        call("Read", "a.ts", "10:00:01"),
        result("10:00:01"),
        call("Bash", "pytest: Run tests", "10:00:02"),
        result("10:00:02"),
        call("Read", "b.ts", "10:00:03"),
        result("10:00:03"),
      ]),
    );
    expect(model.acts.map((a) => [a.place, a.callsInLog])).toEqual([
      ["files", 1],
      ["shell", 1],
      ["files", 1],
    ]);
  });

  it("does not fold two runs' calls together even at the same place", () => {
    const a = build(run([call("Read", "a.ts", "10:00:01"), result("10:00:01")]));
    const b = build(run([call("Read", "b.ts", "10:00:01"), result("10:00:01")], { runId: "r2" }));
    expect(a.acts).toHaveLength(1);
    expect(b.acts).toHaveLength(1);
  });

  it("collapses the busiest recorded run's 191 calls into 78 acts, one of them 31 calls long", () => {
    // S3-AC3, measured on `e38b0c24` (407 lines) with the real `classifyLine` on 2026-11-26.
    //
    // The criterion attributes these numbers to "the busy fixture", and S8 names `5e46fc12` as
    // busy. They are not that run's: `5e46fc12` has 117 calls collapsing to 65 acts (asserted
    // below). 191 → 78 with a 31-call act is `e38b0c24`, recorded as `busiest.json`. See the
    // test-design artifact's spec-gap list.
    const model = build(fixtureRun(BUSIEST));
    const calls = model.acts.reduce((n, a) => n + a.callsInLog, 0);
    expect(calls).toBe(191);
    expect(model.acts).toHaveLength(78);
    expect(Math.max(...model.acts.map((a) => a.callsInLog))).toBe(31);
    expect(model.acts.filter((a) => a.callsInLog === 31)).toHaveLength(1);
    // 2.4:1, the ratio the UI criterion sizes the feed's `+N` column for.
    expect(calls / model.acts.length).toBeCloseTo(2.45, 2);
  });

  it("collapses the busy fixture's own 117 calls into 65 acts", () => {
    const model = build(fixtureRun(BUSY));
    expect(model.acts.reduce((n, a) => n + a.callsInLog, 0)).toBe(117);
    expect(model.acts).toHaveLength(65);
    expect(Math.max(...model.acts.map((a) => a.callsInLog))).toBe(10);
  });

  it("counts every call line in the log into exactly one act, losing none", () => {
    // The strongest check there is on the folding: a dropped call and a double-counted one both
    // move this total, and a desk call is the one most likely to be dropped (S2's `continue`).
    for (const fixture of [BUSY, BUSIEST, FAILING, FAILED_CALL, NO_CALLS]) {
      const lines = fixture.lines.filter(
        (l) => l.tag === "TOOL" && l.text.split("\n", 1)[0].startsWith("call · "),
      );
      const model = build(fixtureRun(fixture));
      expect(model.acts.reduce((n, a) => n + a.callsInLog, 0)).toBe(lines.length);
    }
  });

  it("puts a desk act in the busy and busiest runs, and never a shell one in its place", () => {
    // Measured: `busy` makes 3 desk calls (Monitor, TaskStop ×2) over 2 acts; `busiest` 3
    // (TaskStop ×3) over 3 acts. Before S2 every one landed at Shell.
    expect(build(fixtureRun(BUSY)).acts.filter((a) => a.place === "desk")).toHaveLength(2);
    expect(build(fixtureRun(BUSIEST)).acts.filter((a) => a.place === "desk")).toHaveLength(3);
    for (const act of build(fixtureRun(BUSIEST)).acts.filter((a) => a.place === "desk")) {
      expect(act.label).toBe(act.tool);
      expect(act.tool).toBe("TaskStop");
    }
  });
});

// ---- S3: newest wins, nothing queues, and the lag is bounded -------------------------------------

describe("the shown act, and how far behind it can be", () => {
  const prefixes = (fixture: typeof BUSY) => {
    const input = fixtureRun(fixture);
    const out: ToolActsModel[] = [];
    let prev: string | null = null;
    for (let n = 1; n <= input.lines.length; n += 1) {
      const model = build({ ...input, lines: input.lines.slice(0, n) }, NOW, prev);
      prev = model.shown?.id ?? prev;
      out.push(model);
    }
    return out;
  };

  it("stays inside the budget over every prefix of the busy fixture", () => {
    // S3-AC1, asserted from the constant and not from a literal. The busy fixture is here because
    // of its rate: 40 of its 117 calls fall inside one 10 s window.
    for (const model of prefixes(BUSY)) {
      expect(model.lagMs).toBeLessThanOrEqual(ACT_LAG_BUDGET_MS);
      expect(model.lagMs).toBeGreaterThanOrEqual(0);
    }
  });

  it("stays inside the budget over every prefix of the busiest fixture too", () => {
    for (const model of prefixes(BUSIEST)) {
      expect(model.lagMs).toBeLessThanOrEqual(ACT_LAG_BUDGET_MS);
    }
  });

  it("never lets the lag grow with the number of acts", () => {
    // The mutation this kills: a queue that drains one act per dwell passes "lag <= budget" on a
    // slow log and only fails on a burst. Here the lag may only ever be 0 or one dwell, whatever
    // the backlog, so no rate can make it accumulate.
    const seen = new Set<number>();
    for (const model of prefixes(BUSY)) seen.add(model.lagMs);
    expect([...seen].sort((a, b) => a - b)).toEqual(
      expect.arrayContaining([0]),
    );
    for (const value of seen) expect([0, ACT_DWELL_MS]).toContain(value);
  });

  it("shows the newest act, not the next one after the last shown", () => {
    // 40 calls arrive between two polls at four different places. A queue would show the second.
    const lines: LogLine[] = [];
    const places = ["Read", "Bash", "WebFetch", "Task"];
    for (let i = 0; i < 40; i += 1) {
      const t = `10:00:${String(i).padStart(2, "0")}`;
      lines.push(call(places[i % 4], `x${i}`, t), result(t));
    }
    const first = build(run(lines.slice(0, 2)));
    const after = build(run(lines), NOW, first.shown?.id ?? null);
    expect(after.shown).not.toBeNull();
    expect(after.shown?.id).toBe(after.acts[after.acts.length - 1].id);
    expect(after.lagMs).toBe(0);
  });

  it("has nothing to show on a run with no call lines", () => {
    const model = build(fixtureRun(NO_CALLS));
    expect(model.acts).toEqual([]);
    expect(model.shown).toBeNull();
    expect(model.lagMs).toBe(0);
    expect(model.failuresSkipped).toBe(0);
  });
});

// ---- S3: time, absolutised and ordered by line order ---------------------------------------------

describe("time", () => {
  it("orders a run by line order, never by the clock string", () => {
    // `c2113a06` spans 00:00:00-23:59:44, so a `time.localeCompare` sort reverses it.
    const model = build(
      run(
        [
          call("Read", "before.ts", "23:59:58"),
          result("23:59:59"),
          call("Bash", "pytest: late", "00:00:01"),
          result("00:00:02"),
        ],
        { startedAt: "2026-11-20T23:59:50Z" },
      ),
    );
    expect(model.acts.map((a) => a.label)).toEqual(["before.ts", "late"]);
  });

  it("gives no act a negative duration on a run that crosses midnight", () => {
    // S3-AC4. The absolutising must roll the date forward, not read 00:00:01 as the same morning.
    const model = build(
      run(
        [
          call("Read", "a.ts", "23:59:58"),
          result("23:59:59"),
          call("Read", "b.ts", "00:00:05"),
          result("00:00:06"),
          call("Bash", "pytest: x", "00:00:07"),
          result("00:00:08"),
        ],
        { startedAt: "2026-11-20T23:59:50Z" },
      ),
    );
    for (const act of model.acts) {
      expect(act.lastAtMs - act.firstAtMs).toBeGreaterThanOrEqual(0);
      expect(Number.isFinite(act.firstAtMs)).toBe(true);
    }
    const starts = model.acts.map((a) => a.firstAtMs);
    expect([...starts].sort((a, b) => a - b)).toEqual(starts);
    expect(model.acts[1].firstAtMs).toBeGreaterThan(model.acts[0].lastAtMs);
    // One day later, not one day earlier.
    expect(model.acts[1].firstAtMs - model.acts[0].firstAtMs).toBeLessThan(60_000);
  });

  it("gives no act a negative duration on any recorded fixture", () => {
    for (const fixture of [BUSY, BUSIEST, FAILING, FAILED_CALL]) {
      const model = build(fixtureRun(fixture));
      for (const act of model.acts) expect(act.lastAtMs).toBeGreaterThanOrEqual(act.firstAtMs);
      const starts = model.acts.map((a) => a.firstAtMs);
      expect([...starts].sort((a, b) => a - b)).toEqual(starts);
    }
  });

  it("absolutises against the run's own started_at", () => {
    const model = build(run([call("Read", "a.ts", "10:00:30"), result("10:00:31")]));
    expect(model.acts[0].firstAtMs).toBe(Date.parse("2026-11-20T10:00:30Z"));
  });

  it("is total on a run whose started_at is missing", () => {
    // 1,449 rows predate the column this is read from; a null must not produce NaN arithmetic.
    const model = build(run([call("Read", "a.ts", "10:00:30"), result("10:00:31")], { startedAt: null }));
    expect(model.acts).toHaveLength(1);
    for (const act of model.acts) {
      expect(Number.isNaN(act.firstAtMs)).toBe(false);
      expect(act.lastAtMs).toBeGreaterThanOrEqual(act.firstAtMs);
    }
    expect(model.lagMs).toBeLessThanOrEqual(ACT_LAG_BUDGET_MS);
  });
});

// ---- S3: pure and total --------------------------------------------------------------------------

describe("purity", () => {
  it("returns the same model for the same input, twice", () => {
    const input = fixtureRun(BUSY);
    expect(build(input, NOW, null)).toEqual(build(input, NOW, null));
  });

  it("has no clock of its own: moving nowMs does not change the acts", () => {
    const input = fixtureRun(BUSY);
    const early = build(input, NOW, null);
    const late = build(input, NOW + 86_400_000, null);
    expect(late.acts).toEqual(early.acts);
    expect(late.activity).toBe(early.activity);
  });

  it("does not mutate the lines it was given", () => {
    const input = fixtureRun(BUSY);
    const before = JSON.stringify(input.lines);
    build(input);
    expect(JSON.stringify(input.lines)).toBe(before);
  });

  it("is total on an empty log, a log of nothing but noise, and an unknown status", () => {
    for (const input of [
      run([]),
      run([{ tag: "RUN", text: "invoked", time: "10:00:00" }]),
      run([tool("", "10:00:00"), tool("result", "10:00:00")]),
      run([call("Read", "a.ts", "10:00:00")], { status: "something_new" }),
    ]) {
      const model = build(input);
      expect(Array.isArray(model.acts)).toBe(true);
      expect(model.lagMs).toBeGreaterThanOrEqual(0);
      expect(model.failuresSkipped).toBeGreaterThanOrEqual(0);
    }
  });

  it("is stable when prevShownId names an act the log no longer holds", () => {
    // The log is a 1000-line tail, so the act shown a poll ago can fall out of it.
    const input = fixtureRun(BUSY);
    const model = build(input, NOW, "an-act-trimmed-out-of-the-tail");
    expect(model.shown?.id).toBe(model.acts[model.acts.length - 1].id);
    expect(model.lagMs).toBe(0);
  });
});

// ---- S3: the count is of the log, not of the run -------------------------------------------------

describe("callsInLog", () => {
  it("is named for the log's tail and never exceeds the call lines the log holds", () => {
    for (const fixture of [BUSY, BUSIEST, FAILING, FAILED_CALL]) {
      const lines = fixture.lines.filter(
        (l) => l.tag === "TOOL" && l.text.split("\n", 1)[0].startsWith("call · "),
      );
      for (const act of build(fixtureRun(fixture)).acts) {
        expect(act.callsInLog).toBeGreaterThanOrEqual(1);
        expect(act.callsInLog).toBeLessThanOrEqual(lines.length);
      }
    }
  });

  it("falls when the log's head is trimmed away, because it is a count of the log", () => {
    const input = fixtureRun(BUSIEST);
    const whole = build(input);
    const tail = build({ ...input, lines: input.lines.slice(-50) });
    expect(tail.acts.reduce((n, a) => n + a.callsInLog, 0)).toBeLessThan(
      whole.acts.reduce((n, a) => n + a.callsInLog, 0),
    );
  });
});

// ---- S5: success only from a result line ---------------------------------------------------------

describe("act states", () => {
  const states = (lines: LogLine[], overrides: Partial<ToolActRunInput> = {}) =>
    build(run(lines, overrides)).acts.map((a) => a.state);

  it("is done after a clean result, failed after result · failed", () => {
    expect(states([call("Read", "a.ts", "10:00:01"), result("10:00:01")])).toEqual(["done"]);
    expect(states([call("Read", "a.ts", "10:00:01"), failedResult("10:00:01")])).toEqual(["failed"]);
  });

  it("is running while a call has no result and no successor", () => {
    expect(states([call("Read", "a.ts", "10:00:01")])).toEqual(["running"]);
  });

  it("is unknown — not done, not forever running — when the next call supersedes it", () => {
    expect(
      states([
        call("Read", "a.ts", "10:00:01"),
        call("Bash", "pytest: x", "10:00:02"),
        result("10:00:02"),
      ]),
    ).toEqual(["unknown", "done"]);
  });

  it("never reports done without a clean result line before it, on any fixture", () => {
    // S5-AC2. `busy` has 2 superseded calls and so really does produce `unknown` from real data.
    for (const fixture of [BUSY, BUSIEST, FAILING, FAILED_CALL]) {
      const cleanResults = fixture.lines.filter(
        (l) => l.tag === "TOOL" && l.text.split("\n", 1)[0] === "result",
      ).length;
      const model = build(fixtureRun(fixture));
      const doneCalls = model.acts
        .filter((a) => a.state === "done")
        .reduce((n, a) => n + a.callsInLog, 0);
      expect(doneCalls).toBeLessThanOrEqual(cleanResults);
    }
    expect(build(fixtureRun(BUSY)).acts.some((a) => a.state === "unknown")).toBe(true);
  });

  it("uses one of exactly four states, and no other string", () => {
    const allowed = new Set(["running", "done", "failed", "unknown"]);
    for (const fixture of [BUSY, BUSIEST, FAILING, FAILED_CALL, NO_CALLS]) {
      for (const act of build(fixtureRun(fixture)).acts) expect(allowed.has(act.state)).toBe(true);
    }
  });
});

// ---- S4: a failure is never hidden by collapsing -------------------------------------------------

describe("failures", () => {
  it("gives every failed act exactly one call, so no failure sits inside a +N", () => {
    // S4-AC1. The failing call must split out of the act it would otherwise have joined: in
    // `failed-call.json` the failing `sed` follows a clean `grep` at the same place.
    for (const fixture of [BUSY, BUSIEST, FAILING, FAILED_CALL]) {
      for (const act of build(fixtureRun(fixture)).acts) {
        if (act.state === "failed") expect(act.callsInLog).toBe(1);
      }
    }
    const model = build(fixtureRun(FAILED_CALL));
    expect(model.acts.filter((a) => a.state === "failed")).toHaveLength(2);
  });

  it("splits a failure out of the middle of a stretch at one place", () => {
    const model = build(
      run([
        call("Read", "a.ts", "10:00:01"),
        result("10:00:01"),
        call("Read", "b.ts", "10:00:02"),
        failedResult("10:00:02"),
        call("Read", "c.ts", "10:00:03"),
        result("10:00:03"),
      ]),
    );
    expect(model.acts.map((a) => [a.state, a.callsInLog, a.label])).toEqual([
      ["done", 1, "a.ts"],
      ["failed", 1, "b.ts"],
      ["done", 1, "c.ts"],
    ]);
  });

  it("collapses the whole of failed-call.json to 13 acts, 2 of them failed", () => {
    // Measured over the payload-derived fixture: 16 calls, 13 acts, max 3 calls in an act.
    const model = build(fixtureRun(FAILED_CALL));
    expect(model.acts).toHaveLength(13);
    expect(model.acts.filter((a) => a.state === "failed").map((a) => a.place)).toEqual([
      "files",
      "control",
    ]);
    expect(Math.max(...model.acts.map((a) => a.callsInLog))).toBe(3);
  });

  it("keeps a failed act in the list after a later act on the same run succeeds", () => {
    // S4-AC4. Both failures in `failed-call.json` are followed by clean acts at the same place.
    const model = build(fixtureRun(FAILED_CALL));
    const failedIndexes = model.acts.flatMap((a, i) => (a.state === "failed" ? [i] : []));
    expect(failedIndexes).toHaveLength(2);
    for (const i of failedIndexes) {
      expect(model.acts.slice(i + 1).some((a) => a.state === "done")).toBe(true);
      expect(model.acts[i].state).toBe("failed");
    }
  });

  it("shows an unshown failure ahead of the newest act, for one dwell", () => {
    // Replayed as a run still at work: S6 drops anything unshown the moment a run ends, and that
    // is asserted separately, so the pre-empt can only be measured while the run is active.
    const input = fixtureRun(FAILED_CALL, { status: "running" });
    const upToFirstFailure = input.lines.slice(0, 14); // through the first `result · failed`
    const seen = build({ ...input, lines: upToFirstFailure });
    expect(seen.shown?.state).toBe("failed");
    // The whole log, having shown nothing yet: the failure pre-empts the newest act.
    const fresh = build(input, NOW, null);
    expect(fresh.shown?.state).toBe("failed");
    expect(fresh.lagMs).toBe(ACT_DWELL_MS);
    // Once that failure has been shown, the newest act takes the floor again.
    const next = build(input, NOW, fresh.shown?.id ?? null);
    expect(next.shown?.state).not.toBe("failed");
    expect(next.lagMs).toBe(0);
  });

  it("counts the failures a dwell could not show rather than dropping them silently", () => {
    // S4-AC2. Two failures, one dwell: one is shown and the other is counted, never zero.
    const fresh = build(fixtureRun(FAILED_CALL, { status: "running" }), NOW, null);
    expect(fresh.failuresSkipped).toBe(1);
    expect(fresh.failuresSkipped).toBeGreaterThan(0);
  });

  it("reports no skipped failures when there were none to skip", () => {
    // The other half of "never silently zero": zero must still mean zero.
    expect(build(fixtureRun(BUSIEST)).failuresSkipped).toBe(0);
    expect(build(fixtureRun(FAILING)).failuresSkipped).toBe(0);
    const one = build(run([call("Read", "a.ts", "10:00:01"), failedResult("10:00:01")]));
    expect(one.failuresSkipped).toBe(0);
    expect(one.acts).toHaveLength(1);
    expect(one.shown?.state).toBe("failed");
  });

  it("counts every unshown failure beyond the one it shows", () => {
    const lines: LogLine[] = [];
    for (let i = 0; i < 6; i += 1) {
      const t = `10:00:0${i}`;
      lines.push(call("Read", `f${i}.ts`, t), failedResult(t));
    }
    const model = build(run(lines), NOW, null);
    expect(model.acts.filter((a) => a.state === "failed")).toHaveLength(6);
    expect(model.failuresSkipped).toBe(5);
  });

  it("marks no act failed from a run-level ERR or FAIL", () => {
    // S4-AC3, on the real run the inference used to blame: `fcc2a8a7` makes 82 clean calls and
    // then dies at the gate, with ERR/FAIL at lines 178-180 of 180.
    const model = build(fixtureRun(FAILING));
    expect(model.acts.filter((a) => a.state === "failed")).toEqual([]);
    expect(model.phase).toBe("ended");
    expect(model.acts.reduce((n, a) => n + a.callsInLog, 0)).toBe(82);
    expect(model.failuresSkipped).toBe(0);
  });

  it("marks no act failed from a result line whose suffix is not the failure marker", () => {
    const model = build(
      run([call("Read", "a.ts", "10:00:01"), tool("result · truncated\nbody", "10:00:02")]),
    );
    expect(model.acts.map((a) => a.state)).toEqual(["done"]);
  });

  it("marks no act failed from the word Error in a result body", () => {
    // 22,147 live result bodies carry it as ordinary content; the inference is rejected outright.
    const model = build(
      run([
        call("Bash", "pytest: Run tests", "10:00:01"),
        tool("result\nError: 1 failed\nexit code 1", "10:00:02"),
      ]),
    );
    expect(model.acts.map((a) => a.state)).toEqual(["done"]);
  });
});

// ---- S6: the run's own status wins ---------------------------------------------------------------

describe("the run's status", () => {
  const ENDED = ["succeeded", "failed", "cancelled"];
  const ACTIVE = ["queued", "running"];

  it.each(ENDED)("%s ends the run, with no act running and nothing unshown", (status) => {
    // S6-AC1. The fixture's last call is left open on purpose, so an implementation that only
    // clamps `running` on the final line is not enough.
    const input = fixtureRun(BUSY, {
      status,
      lines: [...BUSY.lines, { tag: "TOOL", text: "call · Read · open.ts", time: "15:35:50" }],
    });
    const model = build(input, NOW, null);
    expect(model.phase).toBe("ended");
    expect(model.acts.every((a) => a.state !== "running")).toBe(true);
    expect(model.failuresSkipped).toBe(0);
  });

  it.each(ACTIVE)("%s leaves the run active", (status) => {
    const model = build(run([call("Read", "a.ts", "10:00:01")], { status }));
    expect(model.phase).toBe("active");
    expect(model.acts[0].state).toBe("running");
  });

  it("reads awaiting_permission as waiting, not as active and not as ended", () => {
    const model = build(run([call("Read", "a.ts", "10:00:01")], { status: "awaiting_permission" }));
    expect(model.phase).toBe("waiting");
  });

  it("drops an unshown failure the moment the run ends", () => {
    // S6: the status wins with no dwell, "including unshown failures".
    const input = fixtureRun(FAILED_CALL, { status: "succeeded" });
    const model = build(input, NOW, null);
    expect(model.failuresSkipped).toBe(0);
    expect(model.shown?.id).toBe(model.acts[model.acts.length - 1].id);
    expect(model.lagMs).toBe(0);
    // The failed acts are still in the list — they are kept, just not pre-empting the floor.
    expect(model.acts.filter((a) => a.state === "failed")).toHaveLength(2);
  });

  it("has no completed status, so nothing reads one", () => {
    // `RunStatus` is queued/running/awaiting_permission/succeeded/failed/cancelled. An inherited
    // criterion said `✓ completed`; shipping it would render a string no status produces.
    const model = build(run([call("Read", "a.ts", "10:00:01")], { status: "completed" }));
    expect(model.phase).not.toBe("active");
    expect(model.phase).toBe("ended");
  });

  it("replays one fixture under every status without a running act surviving the end", () => {
    for (const status of [...ACTIVE, "awaiting_permission", ...ENDED]) {
      const model = build(fixtureRun(BUSY, { status }), NOW, null);
      const anyRunning = model.acts.some((a) => a.state === "running");
      expect(anyRunning).toBe(false); // every call in `busy` has a result, in every status
      expect(["active", "waiting", "ended"]).toContain(model.phase);
    }
  });
});

// ---- S7: activity is three-valued, and lines beat the flag ---------------------------------------

describe("activity", () => {
  it("is calls when the log holds any call line", () => {
    expect(build(fixtureRun(BUSY)).activity).toBe("calls");
    expect(build(fixtureRun(BUSIEST)).activity).toBe("calls");
  });

  it("is calls even on an adapter that is not expected to write them", () => {
    // S7-AC4. The lines are evidence; the flag is a prediction. `failing.json` is the real case:
    // its `command` is empty, so no adapter resolves and `call_lines_expected` is false — and it
    // carries 82 call lines, because the writer keys on Claude-shaped `tool_use` blocks.
    expect(FAILING.adapter).toBe("");
    expect(FAILING.call_lines_expected).toBe(false);
    expect(build(fixtureRun(FAILING)).activity).toBe("calls");
    // And the same for an explicitly non-Claude adapter. No live run has this shape — every
    // non-empty non-claude command in the database has zero call lines — so it is built by
    // relabelling a recorded log rather than left untested.
    const relabelled = fixtureRun(BUSY, { adapter: "cursor", callLinesExpected: false });
    expect(build(relabelled).activity).toBe("calls");
  });

  it("is no-calls-yet for a Claude run that has logged none", () => {
    // S7-AC2. Measured: 22 Claude runs since 2026-11-11 logged no call line.
    const model = build(run([], { adapter: "claude", callLinesExpected: true }));
    expect(model.activity).toBe("no-calls-yet");
    const thinking = build(
      run([{ tag: "THINK", text: "considering", time: "10:00:01" }], {
        adapter: "claude",
        callLinesExpected: true,
      }),
    );
    expect(thinking.activity).toBe("no-calls-yet");
  });

  it("is unknown for an adapter that writes none and has written none", () => {
    // S7-AC1, on the recorded `cursor-agent` run: 100 lines, 80 of them THINK, 0 calls.
    expect(NO_CALLS.adapter).toBe("cursor");
    expect(NO_CALLS.call_lines_expected).toBe(false);
    expect(build(fixtureRun(NO_CALLS)).activity).toBe("unknown");
  });

  it("is unknown for a run whose command recorded no adapter at all", () => {
    // S7-AC3. 790 of 6,211 live runs have an empty `command`, the only place an adapter is read
    // from, so this is the largest single group — not an edge.
    const model = build(run([], { adapter: "", callLinesExpected: false }));
    expect(model.activity).toBe("unknown");
  });

  it("uses one of exactly three values, over every fixture and every flag combination", () => {
    const allowed = new Set(["calls", "no-calls-yet", "unknown"]);
    for (const fixture of [BUSY, BUSIEST, FAILING, FAILED_CALL, NO_CALLS]) {
      for (const expected of [true, false]) {
        const model = build(fixtureRun(fixture, { callLinesExpected: expected }));
        expect(allowed.has(model.activity)).toBe(true);
      }
    }
  });

  it("does not read idleness out of a finished run that logged nothing", () => {
    // The whole point of the three values: unknown must survive the run ending.
    for (const status of ["succeeded", "failed", "cancelled"]) {
      expect(build(fixtureRun(NO_CALLS, { status })).activity).toBe("unknown");
    }
  });
});

// ---- the acts a feed row is built from -----------------------------------------------------------

describe("act identity", () => {
  it("keeps an act's id stable across polls while its lines stay in the log", () => {
    const input = fixtureRun(BUSY);
    const half = Math.floor(input.lines.length / 2);
    const earlier = build({ ...input, lines: input.lines.slice(0, half) }).acts;
    const later = build(input).acts;
    // Every act that had closed by the halfway point keeps its id, so the feed does not re-animate
    // history on each 2 s poll.
    const closed = earlier.filter((a) => a.state !== "running");
    const ids = new Set(later.map((a: ToolAct) => a.id));
    for (const act of closed.slice(0, -1)) expect(ids.has(act.id)).toBe(true);
  });

  it("gives two acts at the same place in the same second different ids", () => {
    const model = build(
      run([
        call("Read", "a.ts", "10:00:01"),
        failedResult("10:00:01"),
        call("Read", "a.ts", "10:00:01"),
        failedResult("10:00:01"),
      ]),
    );
    expect(model.acts).toHaveLength(2);
    expect(model.acts[0].id).not.toBe(model.acts[1].id);
  });
});

// ---- S9: the pseudo-call is filtered by its tool id, never by its place --------------------------

describe("the THINK/OUT pseudo-call", () => {
  // `runCalls` synthesises one call per stretch of THINK/OUT lines, keyed `tool: "model"`
  // (`agentMap.ts:399`), and it is the only producer of that tool id. Three real tools —
  // TodoWrite, ExitPlanMode, EnterPlanMode (`MODEL_TOOLS`) — classify to the same *place*, so a
  // filter written on the place swallows them and S7 ("any `call ·` line at all ⇒ calls") becomes
  // false for every tool on the Model place.

  it("keeps one act for a run whose only call line is TodoWrite", () => {
    const model = build(run([call("TodoWrite", "3 items", "10:00:01"), result("10:00:01")]));
    expect(model.acts).toHaveLength(1);
    expect(model.acts[0]).toMatchObject({ place: "model", tool: "TodoWrite", callsInLog: 1, state: "done" });
    expect(model.activity).toBe("calls");
    expect(model.shown?.tool).toBe("TodoWrite");
  });

  it("keeps an act for every real tool on the model place, not only TodoWrite", () => {
    // MODEL_TOOLS, `agentMap.ts:163`. A filter narrowed to the one tool the criterion names by
    // example would pass the test above and still swallow the other two.
    for (const name of ["TodoWrite", "ExitPlanMode", "EnterPlanMode"]) {
      const model = build(run([call(name, "x", "10:00:01"), result("10:00:01")]));
      expect(model.acts.map((a) => [a.place, a.tool])).toEqual([["model", name]]);
      expect(model.activity).toBe("calls");
    }
  });

  it("yields two acts for a mixed TodoWrite-then-Read run", () => {
    const model = build(
      run([
        call("TodoWrite", "3 items", "10:00:01"),
        result("10:00:01"),
        call("Read", "a.ts", "10:00:02"),
        result("10:00:02"),
      ]),
    );
    expect(model.acts.map((a) => [a.place, a.tool, a.callsInLog])).toEqual([
      ["model", "TodoWrite", 1],
      ["files", "Read", 1],
    ]);
  });

  it("yields no act, and no activity, for a run of THINK/OUT lines alone", () => {
    const model = build(
      run([
        { tag: "THINK", text: "weighing it up", time: "10:00:01" },
        { tag: "THINK", text: "still weighing", time: "10:00:02" },
        { tag: "OUT", text: "here is the plan", time: "10:00:03" },
      ]),
    );
    expect(model.acts).toEqual([]);
    expect(model.shown).toBeNull();
    expect(model.activity).toBe("no-calls-yet");
  });

  it("drops the pseudo-call by its tool id, so a real call labelled `thinking` survives", () => {
    // Kills a filter written on the pseudo-call's *label* ("thinking") rather than its tool id.
    // A model-place tool's label is its own name (`classifyTool`'s `words(tool)`), so the case
    // that separates the two filters is a real call whose subject happens to read "thinking".
    const model = build(run([call("Read", "thinking", "10:00:01"), result("10:00:01")]));
    expect(model.acts.map((a) => [a.place, a.tool, a.label])).toEqual([["files", "Read", "thinking"]]);
    // And the model-place tool is labelled by its own name, never by its subject.
    expect(build(run([call("TodoWrite", "3 items", "10:00:01")])).acts[0].label).toBe("TodoWrite");
  });

  it("keeps a model-place call that the next call supersedes with no result of its own", () => {
    // Kills a filter written on the pseudo-call's *state*: the synthesised step is `unknown`, and
    // so is any real call the next call supersedes. The states must not be the discriminator.
    const model = build(
      run([call("TodoWrite", "3 items", "10:00:01"), call("Read", "a.ts", "10:00:02"), result("10:00:02")]),
    );
    expect(model.acts.map((a) => [a.tool, a.state])).toEqual([
      ["TodoWrite", "unknown"],
      ["Read", "done"],
    ]);
  });

  it("filters the pseudo-call before acts are built, so THINK lines do not split a stretch", () => {
    // Kills two wrong shapes at once: no filter at all (3 acts, the middle one "thinking"), and a
    // filter applied to the finished act list rather than to the calls (2 files acts, not 1).
    const model = build(
      run([
        call("Read", "a.ts", "10:00:01"),
        result("10:00:01"),
        { tag: "THINK", text: "hmm", time: "10:00:02" },
        { tag: "OUT", text: "so", time: "10:00:02" },
        call("Read", "b.ts", "10:00:03"),
        result("10:00:03"),
      ]),
    );
    expect(model.acts.map((a) => [a.place, a.callsInLog])).toEqual([["files", 2]]);
  });

  it("splits the pseudo-call out before collapsing, so a THINK run does not inflate a TodoWrite act", () => {
    // Kills the ordering mistake: filter the pseudo-call *after* folding and it joins the
    // TodoWrite act at the same place, which then claims two calls where the log holds one.
    const model = build(
      run([
        { tag: "THINK", text: "listing the work", time: "10:00:01" },
        { tag: "OUT", text: "three things", time: "10:00:01" },
        call("TodoWrite", "3 items", "10:00:02"),
        result("10:00:02"),
      ]),
    );
    expect(model.acts.map((a) => [a.tool, a.callsInLog])).toEqual([["TodoWrite", 1]]);
  });

  it("keeps a failed model-place call as its own failed act, so S4 holds there too", () => {
    const model = build(
      run([
        call("TodoWrite", "a", "10:00:01"),
        result("10:00:01"),
        call("TodoWrite", "b", "10:00:02"),
        failedResult("10:00:02"),
        call("TodoWrite", "c", "10:00:03"),
        result("10:00:03"),
      ]),
    );
    expect(model.acts.map((a) => [a.state, a.callsInLog])).toEqual([
      ["done", 1],
      ["failed", 1],
      ["done", 1],
    ]);
    expect(build(run([call("TodoWrite", "b", "10:00:02"), failedResult("10:00:02")])).failuresSkipped).toBe(0);
  });

  it("leaves every recorded fixture's act count exactly where it was", () => {
    // S9's confinement claim, and the evidence for it: in each committed fixture every
    // model-*place* call is the synthesised pseudo-call, so none of them exercises the change.
    // Measured 2026-11-26 over these exact files.
    const expected: Record<string, number> = { BUSY: 65, BUSIEST: 78, FAILING: 27, FAILED_CALL: 13, NO_CALLS: 0 };
    for (const [name, fixture] of Object.entries({ BUSY, BUSIEST, FAILING, FAILED_CALL, NO_CALLS })) {
      const model = build(fixtureRun(fixture));
      expect(model.acts).toHaveLength(expected[name]);
      const modelPlace = model.acts.filter((a) => a.place === "model");
      expect(modelPlace).toEqual([]);
      const synthesised = runCalls(
        { runId: fixture.run_id, agentId: fixture.agent_id, lines: fixture.lines },
        false,
      ).filter((c) => c.place === "model");
      expect(synthesised.every((c) => c.tool === "model")).toBe(true);
    }
  });
});

// ---- S10: collapsing exists in exactly one place -------------------------------------------------

describe("collapsing, moved here from agentMap's feed", () => {
  // These two assertions were `agentMap.test.ts`'s, over `AgentMapModel.feed` — a field no
  // component ever read. `foldFeed` and `buildToolActs` disagreed on an `unknown`-state run
  // (`foldFeed` folded only `done` pairs), so the rule the operator saw was not the rule the
  // tests checked. S10 deletes that second rule; its assertions live on this one.

  it("collapses forty back-to-back calls at one place into a single act", () => {
    const lines: LogLine[] = [];
    for (let i = 0; i < 40; i += 1) {
      const t = `10:00:${String(i).padStart(2, "0")}`;
      lines.push(call("Read", `f${i}.ts`, t), result(t));
    }
    const model = build(run(lines));
    expect(model.acts).toHaveLength(1);
    expect(model.acts[0]).toMatchObject({ label: "f39.ts", callsInLog: 40, place: "files" });
  });

  it("collapses a stretch of unknown-state calls too, which the old feed rule would not", () => {
    // The disagreement itself: forty calls at one place and not one result line. `foldFeed` left
    // forty rows (it folded only `done`); one act is the honest answer, and the only one now.
    const lines: LogLine[] = [];
    for (let i = 0; i < 40; i += 1) lines.push(call("Read", `f${i}.ts`, `10:00:${String(i).padStart(2, "0")}`));
    const model = build(run(lines));
    expect(model.acts).toHaveLength(1);
    expect(model.acts[0]).toMatchObject({ callsInLog: 40, state: "running" });
  });

  it("never collapses either failed call of a four-call stretch", () => {
    const model = build(
      run([
        call("Read", "a.ts", "10:00:01"),
        result("10:00:01"),
        call("Read", "b.ts", "10:00:02"),
        failedResult("10:00:02"),
        call("Read", "c.ts", "10:00:03"),
        result("10:00:03"),
        call("Read", "d.ts", "10:00:04"),
        failedResult("10:00:04"),
      ]),
    );
    const failures = model.acts.filter((a) => a.state === "failed");
    expect(failures.map((a) => a.callsInLog)).toEqual([1, 1]);
    expect(failures.map((a) => a.label)).toEqual(["b.ts", "d.ts"]);
    expect(model.acts).toHaveLength(4);
  });
});
