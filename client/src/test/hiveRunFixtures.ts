/**
 * The five recorded Hive run fixtures (lg-hive-office-2784, S8), and what each is for.
 *
 * Recorded from the live database and from a real Claude payload by
 * `server/tests/test_hive_run_fixtures.py`, which also fails when a committed file goes stale.
 * Never hand-edit one: a hand-written log passes every acceptance criterion while proving nothing
 * about a real run's rate, and rate is this ticket's whole difficulty.
 *
 * The numbers beside each name were measured over these exact files with the real `classifyLine`
 * on 2026-11-26, and the tests assert them. They are here so a reader can see which fixture a
 * number belongs to.
 */

import busy from "./fixtures/hive-runs/busy.json";
import busiest from "./fixtures/hive-runs/busiest.json";
import failedCall from "./fixtures/hive-runs/failed-call.json";
import failing from "./fixtures/hive-runs/failing.json";
import noCalls from "./fixtures/hive-runs/no-calls.json";

import type { LogLine, RunLog } from "../api/runLogTypes";

export interface HiveRunFixture {
  run_id: string;
  run_code: string;
  agent_id: string;
  skill_name: string;
  stage_key: string;
  status: string;
  adapter: string;
  call_lines_expected: boolean;
  started_at: string;
  lines: LogLine[];
}

/** 253 lines, 117 calls, 40 of them inside one 10 s window — the worst measured rate. */
export const BUSY = busy as HiveRunFixture;
/** 407 lines, 191 calls → 78 acts, one of 31 consecutive calls. The most calls of any run. */
export const BUSIEST = busiest as HiveRunFixture;
/** 180 lines, 82 clean calls, then run-level `ERR`/`FAIL`. S6's case, not S4's. */
export const FAILING = failing as HiveRunFixture;
/** A `cursor-agent` run: 100 lines, 0 call lines, and none expected. */
export const NO_CALLS = noCalls as HiveRunFixture;
/** 16 calls and 16 results, 2 of them `result · failed`. The only source of a failed call. */
export const FAILED_CALL = failedCall as HiveRunFixture;

export const HIVE_RUN_FIXTURES = { BUSY, BUSIEST, FAILING, NO_CALLS, FAILED_CALL } as const;

/** The fixture as the run-log endpoint serves it, for a test that mocks `api.runLog`. */
export function asRunLog(fixture: HiveRunFixture, overrides: Partial<RunLog> = {}): RunLog {
  return {
    id: fixture.run_id,
    run_code: fixture.run_code,
    agent_id: fixture.agent_id,
    skill_name: fixture.skill_name,
    stage_key: fixture.stage_key,
    status: fixture.status,
    command: "",
    adapter: fixture.adapter,
    call_lines_expected: fixture.call_lines_expected,
    started_at: fixture.started_at,
    finished_at: null,
    lines: fixture.lines,
    live: null,
    stderr: "",
    transport: "",
    attach_command: "",
    ...overrides,
  };
}

/** The fixture as `buildToolActs` takes it. */
export function asActRun(fixture: HiveRunFixture, overrides: Record<string, unknown> = {}) {
  return {
    runId: fixture.run_id,
    runCode: fixture.run_code,
    agentId: fixture.agent_id,
    stageKey: fixture.stage_key,
    status: fixture.status,
    adapter: fixture.adapter,
    callLinesExpected: fixture.call_lines_expected,
    startedAt: fixture.started_at,
    lines: fixture.lines,
    ...overrides,
  };
}
