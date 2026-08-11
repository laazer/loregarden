import { formatLocalTimestamp, formatRelativeAge, parseTimestamp, runTimestamp } from "../timestamps";

describe("parseTimestamp", () => {
  it("reads an offset-less server stamp as UTC, not as local time", () => {
    // The bug this guards: JS parses an offset-less ISO string as local time,
    // so a UTC instant rendered naively shifts by the viewer's whole offset.
    expect(parseTimestamp("2026-08-08T14:19:57.465660")?.toISOString()).toBe(
      "2026-08-08T14:19:57.465Z",
    );
  });

  it("honours an explicit offset rather than re-tagging it", () => {
    expect(parseTimestamp("2026-08-08T14:19:57+00:00")?.toISOString()).toBe(
      "2026-08-08T14:19:57.000Z",
    );
    expect(parseTimestamp("2026-08-08T10:19:57-04:00")?.toISOString()).toBe(
      "2026-08-08T14:19:57.000Z",
    );
  });

  it("returns null for absent or unparseable values", () => {
    expect(parseTimestamp(null)).toBeNull();
    expect(parseTimestamp(undefined)).toBeNull();
    expect(parseTimestamp("")).toBeNull();
    expect(parseTimestamp("not a date")).toBeNull();
  });
});

describe("formatLocalTimestamp", () => {
  it("renders both formats to the same local instant, with a zone", () => {
    const naive = formatLocalTimestamp("2026-08-08T14:19:57.465660");
    const offset = formatLocalTimestamp("2026-08-08T14:19:57.465660+00:00");
    expect(naive).toBe(offset);
    // A timestamp with no zone named is the ambiguity we are removing.
    expect(naive).toMatch(/\d{4}/);
    expect(naive.trim()).not.toBe("");
  });

  it("falls back rather than printing Invalid Date", () => {
    expect(formatLocalTimestamp(null)).toBe("—");
    expect(formatLocalTimestamp("garbage", "n/a")).toBe("n/a");
  });
});

describe("formatRelativeAge", () => {
  const now = new Date("2026-08-10T12:00:00Z");

  it("scales the unit to the age", () => {
    expect(formatRelativeAge("2026-08-10T11:59:30Z", now)).toBe("just now");
    expect(formatRelativeAge("2026-08-10T11:48:00Z", now)).toBe("12m ago");
    expect(formatRelativeAge("2026-08-10T08:00:00Z", now)).toBe("4h ago");
    // Floors, so 45.7h is "1d ago" — not rounded up to 2.
    expect(formatRelativeAge("2026-08-08T14:19:57Z", now)).toBe("1d ago");
    expect(formatRelativeAge("2026-08-08T11:00:00Z", now)).toBe("2d ago");
  });

  it("ages an offset-less stamp as UTC", () => {
    expect(formatRelativeAge("2026-08-10T08:00:00", now)).toBe("4h ago");
  });

  it("says nothing for a future or unparseable stamp", () => {
    expect(formatRelativeAge("2026-08-11T12:00:00Z", now)).toBe("");
    expect(formatRelativeAge(null, now)).toBe("");
  });
});

describe("runTimestamp", () => {
  it("prefers the stamp that best dates the run", () => {
    expect(
      runTimestamp({ created_at: "c", started_at: "s", finished_at: "f" }),
    ).toBe("f");
    expect(runTimestamp({ created_at: "c", started_at: "s" })).toBe("s");
    // A run that never started still has to be placeable in time.
    expect(runTimestamp({ created_at: "c", started_at: null, finished_at: null })).toBe("c");
    expect(runTimestamp({})).toBeNull();
  });
});
