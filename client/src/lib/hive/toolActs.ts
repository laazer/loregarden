/**
 * The act model: one real tool call becomes one physical act, without lying about time.
 *
 * A run's log holds `call · <tool> · <subject>` lines faster than any character could walk —
 * the worst measured burst is 40 calls in 10 seconds (run `5e46fc12`). So the rules below are
 * about rate, and they are written down rather than emergent:
 *
 *   R1  Consecutive calls by one run at one place are one act, carrying `callsInLog` — named
 *       for the log's 1000-line tail (`run_log_stream.MAX_LINES`), never a count of the run.
 *   R2  A failed call closes the open act and is its own single-call act, so no failure can
 *       ever hide inside a `+N`.
 *   R3  The newest act is the one shown and nothing queues. One exception: a failure that has
 *       not been shown pre-empts it for one `ACT_DWELL_MS = 600`, and any further unshown
 *       failure is counted in `failuresSkipped` rather than dropped in silence.
 *   R4  The run's own status wins, with no dwell: the moment it leaves the active statuses,
 *       nothing on it is running and anything unshown is dropped, failures included.
 *   R5  Therefore the shown act is at most `ACT_LAG_BUDGET_MS = 2000` ms behind the newest
 *       call line in the log — about **4 s** as the operator sees it, after `POLL_MS` = 2000
 *       in `HiveAgentMap.tsx`. There is no backlog to drain, so no rate can make the lag
 *       accumulate; that is the whole difference from a queue-and-drain design, which on the
 *       measured burst would end roughly 34 s behind.
 *   R6  Activity is three-valued, and observed lines beat the adapter flag: any call line at
 *       all means `calls`, even on an adapter not expected to write them.
 *   R7  Times are absolutised against the run's `started_at` and ordered by line order. A log
 *       line carries `HH:MM:SS` with no date, and run `c2113a06` spans 00:00:00-23:59:44.
 *
 * Pure and total: log lines in, a model out, with no clock of its own. The actor is always the
 * run — every act carries its own `runId`/`agentId` — and the place is only ever an id from
 * `MAP_PLACES`. Nothing here turns a place into a character or a crew: which body embodies a
 * run is decided where bodies are chosen (lg-hive-office-2783 / 2785), never here.
 */

import type { LogLine } from "../../api/runLogTypes";
import { ACTIVE_LEDGER_STATUSES } from "../ledgerStatus";
import { runCalls, type MapCall, type MapCallState, type MapPlaceId } from "./agentMap";

/** How long an unshown failure holds the floor ahead of the newest act. */
export const ACT_DWELL_MS = 600;
/** The stated bound: how far behind the newest call line the shown act may ever be. */
export const ACT_LAG_BUDGET_MS = 2000;

/** The ledger status that means a run is waiting on a person rather than working. */
const WAITING_STATUS = "awaiting_permission";

const DAY_MS = 86_400_000;

/** What the log says about one act, and nothing it does not say. See `MapCallState`. */
export type ToolActState = MapCallState;

/** Whether this run's tool calls are observable at all — three values, never two. */
export type RunActivity = "calls" | "no-calls-yet" | "unknown";

/** Where the run itself is, read from its ledger status and nothing else. */
export type RunPhase = "active" | "waiting" | "ended";

export interface ToolActRunInput {
  runId: string;
  runCode: string;
  agentId: string;
  stageKey: string;
  /** A `RunStatus` value. There is no `completed`. */
  status: string;
  /** Resolved from the run's `command`; "" when no adapter could be resolved. */
  adapter: string;
  /** Whether this adapter is one that writes call lines — a prediction, which lines outrank. */
  callLinesExpected: boolean;
  startedAt: string | null;
  lines: readonly LogLine[];
}

export interface ToolAct {
  /** Stable across polls while the act's first call stays in the log's tail. */
  id: string;
  /** The actor is the run. Nothing re-attributes an act by where it happened. */
  runId: string;
  agentId: string;
  place: MapPlaceId;
  /** The most recent call of the act: what the agent is doing now, not what it did first. */
  tool: string;
  label: string;
  state: ToolActState;
  /** Calls of this act **the log still holds**, which is not a count of the run (R1). */
  callsInLog: number;
  /** The clock string of the act's most recent call, as its log wrote it. */
  time: string;
  firstAtMs: number;
  lastAtMs: number;
}

export interface ToolActsModel {
  runId: string;
  agentId: string;
  /** Oldest first. */
  acts: ToolAct[];
  /** The one act the surface shows for this run, or null when there is none. */
  shown: ToolAct | null;
  /** The model's own presentation lag: 0, or one dwell while an unshown failure holds the floor. */
  lagMs: number;
  /** Failures no dwell could show. Never silently zero while a failure went unshown. */
  failuresSkipped: number;
  activity: RunActivity;
  phase: RunPhase;
}

/**
 * Where the run is, from its ledger status alone. Private on purpose: this module exports no
 * function that answers a question about a bare string, because the one question nobody may
 * ask it is "who is at this place?" — a place is never turned into a character here.
 */
function runPhase(status: string): RunPhase {
  const value = status.toLowerCase();
  if (value === WAITING_STATUS) return "waiting";
  return ACTIVE_LEDGER_STATUSES.has(value) ? "active" : "ended";
}

/** `HH:MM:SS` into milliseconds since that day's midnight; 0 for anything unparseable. */
function clockMs(time: string): number {
  const parts = /^(\d{1,2}):(\d{2}):(\d{2})/.exec(time);
  if (parts === null) return 0;
  return (Number(parts[1]) * 3600 + Number(parts[2]) * 60 + Number(parts[3])) * 1000;
}

/**
 * The log's clock strings against the run's own start date, rolling the date forward whenever
 * the clock goes backwards (R7). The first line anchors on the start date as written: a run
 * that begins at 23:59:50 and logs 00:00:05 is six days-of-the-week later, not one day early.
 * A run with no `started_at` — 1,449 rows predate the column — anchors at the epoch, which
 * keeps every duration real even though the wall-clock date is unknown.
 */
function absolutise(startedAt: string | null, times: readonly string[]): number[] {
  const started = startedAt === null ? Number.NaN : Date.parse(startedAt);
  const base = Number.isFinite(started) ? Math.floor(started / DAY_MS) * DAY_MS : 0;
  const out: number[] = [];
  let day = 0;
  let previous = Number.NEGATIVE_INFINITY;
  for (const time of times) {
    let at = base + day * DAY_MS + clockMs(time);
    if (at < previous) {
      day += 1;
      at += DAY_MS;
    }
    out.push(at);
    previous = at;
  }
  return out;
}

function startAct(call: MapCall, agentId: string, atMs: number): ToolAct {
  return {
    id: `act|${call.id}`,
    runId: call.runId,
    agentId,
    place: call.place,
    tool: call.tool,
    label: call.label,
    state: call.state,
    callsInLog: 1,
    time: call.time,
    firstAtMs: atMs,
    lastAtMs: atMs,
  };
}

/**
 * The acts of one run, and which one its surface shows.
 *
 * `_nowMs` is the caller's clock. It is taken as an argument and deliberately not read: every
 * time reported here comes from the log, so a model that consulted a clock would stop being
 * replayable — and a caller that had to pass one cannot be tempted to let this module call
 * `Date.now()` itself. `prevShownId` is the act the caller showed last poll, which is how a
 * dwell is measured without a clock.
 */
export function buildToolActs(
  run: ToolActRunInput,
  _nowMs: number,
  prevShownId: string | null,
): ToolActsModel {
  const phase = runPhase(run.status);
  // The THINK/OUT pseudo-call is not a call: it has no result line, and no act. Dropped by its
  // tool id, which `runCalls` is the sole producer of, never by its place — TodoWrite,
  // ExitPlanMode and EnterPlanMode are real calls that classify to the same Model place, and a
  // place filter swallowed all three (S9).
  const calls = runCalls(run, phase !== "ended").filter((call) => call.tool !== "model");
  const times = absolutise(run.startedAt, calls.map((call) => call.time));

  const acts: ToolAct[] = [];
  calls.forEach((call, index) => {
    const open = acts[acts.length - 1];
    const joins =
      open !== undefined &&
      open.place === call.place &&
      // R2: a failure neither joins an act nor lets one join it.
      open.state !== "failed" &&
      call.state !== "failed";
    if (open !== undefined && joins) {
      open.tool = call.tool;
      open.label = call.label;
      open.state = call.state;
      open.time = call.time;
      open.lastAtMs = times[index];
      open.callsInLog += 1;
      return;
    }
    acts.push(startAct(call, run.agentId, times[index]));
  });

  const newest = acts.length > 0 ? acts[acts.length - 1] : null;
  let shown = newest;
  let lagMs = 0;
  let failuresSkipped = 0;

  // R3, and R4's exception to it: only a run still at work can have a failure pre-empt the
  // floor. The moment it is waiting or ended its status is the whole answer, with no dwell,
  // and anything unshown — a failure included — is dropped rather than held over.
  if (phase === "active" && newest !== null) {
    const after = prevShownId === null ? -1 : acts.findIndex((act) => act.id === prevShownId);
    const unshown = acts.filter((act, index) => index > after && act.state === "failed");
    if (unshown.length > 0) {
      shown = unshown[unshown.length - 1];
      failuresSkipped = unshown.length - 1;
      lagMs = shown.id === newest.id ? 0 : ACT_DWELL_MS;
    }
  }

  return {
    runId: run.runId,
    agentId: run.agentId,
    acts,
    shown,
    lagMs,
    failuresSkipped,
    // R6: the lines are evidence, the flag only a prediction — the call-line writer keys on
    // Claude-shaped `tool_use` blocks, not on the adapter's name.
    activity: acts.length > 0 ? "calls" : run.callLinesExpected ? "no-calls-yet" : "unknown",
    phase,
  };
}
