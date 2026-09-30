import type { MemoryBriefingStats } from "../../api/memoryApi";
import { holeShare, isEmptyWindow, SEAM_LAG_LIMIT_MS, seamVerdict } from "../memoryHealth";

function stats(overrides: Partial<MemoryBriefingStats> = {}): MemoryBriefingStats {
  return {
    window_days: 7,
    window_from: "2026-09-19T00:00:00Z",
    window_to: "2026-09-26T00:00:00Z",
    runs_in_window: 10,
    runs_with_briefing_row: 10,
    runs_with_no_briefing_row: 0,
    rows_in_window: 10,
    newest_run_at: "2026-09-25T12:00:00",
    last_row_at: "2026-09-25T12:00:05",
    built: 10,
    empty: 0,
    store_error: 0,
    no_store: 0,
    skipped: 0,
    ...overrides,
  };
}

const EMPTY = stats({
  runs_in_window: 0,
  runs_with_briefing_row: 0,
  rows_in_window: 0,
  newest_run_at: null,
  last_row_at: null,
  built: 0,
});

it("reads zeros with null timestamps as an empty window, not a failure", () => {
  expect(isEmptyWindow(EMPTY)).toBe(true);
  expect(seamVerdict(EMPTY).tone).toBe("none");
});

it("says the seam is dead when runs started and none recorded anything", () => {
  const verdict = seamVerdict(
    stats({
      runs_with_briefing_row: 0,
      runs_with_no_briefing_row: 10,
      rows_in_window: 0,
      last_row_at: null,
    }),
  );
  expect(verdict.tone).toBe("dead");
  expect(verdict.text).toMatch(/not one recorded a briefing/);
});

it("says the seam died when the last row trails the newest run", () => {
  const verdict = seamVerdict(
    stats({ newest_run_at: "2026-09-25T12:00:00", last_row_at: "2026-09-22T12:00:00" }),
  );
  expect(verdict.tone).toBe("dead");
  expect(verdict.text).toMatch(/3 days before the newest run/);
});

it("tolerates the normal gap between a run starting and its briefing", () => {
  const justInside = new Date(Date.parse("2026-09-25T12:00:00Z") - SEAM_LAG_LIMIT_MS + 1000);
  const verdict = seamVerdict(stats({ last_row_at: justInside.toISOString() }));
  expect(verdict.tone).toBe("ok");
});

it("reports holes as a share of runs", () => {
  expect(holeShare(stats({ runs_with_no_briefing_row: 3 }))).toBe(30);
  expect(holeShare(EMPTY)).toBe(0);
});
