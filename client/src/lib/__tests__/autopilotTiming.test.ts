import type { AutopilotView } from "../../api/initiativeApi";
import { describeTiming, formatDays, formatDuration, isoToLocalInput, localInputToIso } from "../autopilotTiming";

function view(overrides: Partial<AutopilotView> = {}): AutopilotView {
  return {
    enabled: false,
    max_parallel: 3,
    paused_reason: "",
    in_flight: 0,
    next_up: [],
    available: true,
    recent: [],
    stops: [],
    schedule: {},
    stops_at: null,
    next_start: null,
    ...overrides,
  };
}

test("durations read as hours and minutes", () => {
  expect(formatDuration(30)).toBe("30m");
  expect(formatDuration(120)).toBe("2h");
  expect(formatDuration(90)).toBe("1h 30m");
});

test("common day sets get their names", () => {
  expect(formatDays([0, 1, 2, 3, 4])).toBe("Mon–Fri");
  expect(formatDays([6, 5])).toBe("Weekends");
  expect(formatDays([0, 1, 2, 3, 4, 5, 6])).toBe("Every day");
  expect(formatDays([2, 0])).toBe("Mon, Wed");
});

test("a datetime-local value round-trips through an ISO instant", () => {
  const iso = localInputToIso("2026-11-06T07:30");
  expect(iso).not.toBeNull();
  expect(isoToLocalInput(iso)).toBe("2026-11-06T07:30");
  expect(localInputToIso("")).toBeNull();
});

test("nothing set says nothing", () => {
  expect(describeTiming(view())).toBeNull();
});

test("on, it names when it stops rather than the stops it holds", () => {
  const line = describeTiming(
    view({ enabled: true, stops: [{ kind: "run_for", minutes: 60 }], stops_at: "2026-11-06T07:00:00Z" }),
  );
  expect(line).toMatch(/^Stops /);
  expect(line).not.toMatch(/each time/);
});

test("off, it names its stops, its window and its next start", () => {
  const line = describeTiming(
    view({
      stops: [{ kind: "run_for", minutes: 120 }],
      schedule: { window: { days: [0, 1, 2, 3, 4], start: "22:00:00", end: "06:00:00", timezone: "UTC" } },
      next_start: "2026-11-06T22:00:00Z",
    }),
    "UTC",
  );
  expect(line).toContain("2h");
  expect(line).toContain("Mon–Fri 22:00–06:00");
  expect(line).not.toContain("(UTC)");
  expect(line).toContain("next start");
});

test("a window in another zone names its zone", () => {
  const line = describeTiming(
    view({ schedule: { window: { days: [5], start: "09:00", end: "10:00", timezone: "Asia/Tokyo" } } }),
    "UTC",
  );
  expect(line).toContain("(Asia/Tokyo)");
});
