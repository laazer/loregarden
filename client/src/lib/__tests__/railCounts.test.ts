import type { TicketStatusSummary } from "../../api/types";
import { DEFAULT_PARALLEL_STATS } from "../queueSocket";
import { railCounts } from "../railCounts";

function summary(overrides: Partial<TicketStatusSummary> = {}): TicketStatusSummary {
  return {
    backlog: 0,
    in_progress: 0,
    blocked: 0,
    parked: 0,
    done: 0,
    wont_do: 0,
    running: 0,
    awaiting: 0,
    queued: 0,
    idle: 0,
    ...overrides,
  };
}

const quiet = {
  summary: summary(),
  runningCount: 0,
  stats: DEFAULT_PARALLEL_STATS,
  queueLive: false,
  usageNearLimit: false,
};

test("tickets waiting on a person are one violet count on Work items", () => {
  const counts = railCounts({ ...quiet, summary: summary({ parked: 3, awaiting: 18 }) });
  expect(counts["work-items"]).toEqual({ text: "21", label: "21 need you", tone: "you" });
});

test("a count of zero is not drawn", () => {
  expect(railCounts(quiet)).toEqual({});
});

test("no summary yet means no need-you count, not a zero", () => {
  expect(railCounts({ ...quiet, summary: undefined })["work-items"]).toBeUndefined();
});

test("Console counts the tickets running now", () => {
  expect(railCounts({ ...quiet, runningCount: 2 }).dashboard).toEqual({
    text: "2",
    label: "2 running",
    tone: "plain",
  });
});

test("the lane fraction sits on Machine, and comes only from a live queue snapshot", () => {
  const stats = { ...DEFAULT_PARALLEL_STATS, max_concurrent: 3, active_count: 1 };
  expect(railCounts({ ...quiet, stats }).machine).toBeUndefined();
  expect(railCounts({ ...quiet, stats, queueLive: true }).queue).toBeUndefined();
  expect(railCounts({ ...quiet, stats, queueLive: true }).machine).toEqual({
    text: "1/3",
    label: "1 of 3 lanes busy",
    tone: "plain",
  });
});

test("an idle machine still shows its lanes, since 0/3 answers the question", () => {
  expect(railCounts({ ...quiet, queueLive: true }).machine?.text).toBe("0/3");
});

test("a usage warning outranks the lane count on Machine, and its label keeps both", () => {
  expect(railCounts({ ...quiet, queueLive: true, usageNearLimit: true }).machine).toEqual({
    text: "!",
    label: "usage limits are getting close; 0 of 3 lanes busy",
    tone: "warn",
  });
});

test("usage near a limit flags Machine in amber, where Usage now lives", () => {
  expect(railCounts({ ...quiet, usageNearLimit: true }).machine).toEqual({
    text: "!",
    label: "usage limits are getting close",
    tone: "warn",
  });
  expect(railCounts(quiet).machine).toBeUndefined();
});
