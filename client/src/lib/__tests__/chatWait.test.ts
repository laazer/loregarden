import { formatWaitRemaining } from "../chatWait";

const NOW = new Date("2026-10-27T21:00:00Z");

function deadlineAfter(seconds: number): string {
  return new Date(NOW.getTime() + seconds * 1000).toISOString();
}

describe("formatWaitRemaining", () => {
  it("uses floor hours and minutes from the 2h deadline", () => {
    expect(formatWaitRemaining(deadlineAfter(7200), NOW)).toBe("2h 0m left");
    expect(formatWaitRemaining(deadlineAfter(7199), NOW)).toBe("1h 59m left");
    expect(formatWaitRemaining(deadlineAfter(3661), NOW)).toBe("1h 1m left");
    expect(formatWaitRemaining(deadlineAfter(3600), NOW)).toBe("1h 0m left");
    expect(formatWaitRemaining(deadlineAfter(3599), NOW)).toBe("0h 59m left");
    expect(formatWaitRemaining(deadlineAfter(61), NOW)).toBe("0h 1m left");
    expect(formatWaitRemaining(deadlineAfter(60), NOW)).toBe("0h 1m left");
  });

  it("says less than a minute when under 60s remain", () => {
    expect(formatWaitRemaining(deadlineAfter(59), NOW)).toBe("less than a minute left");
    expect(formatWaitRemaining(deadlineAfter(1), NOW)).toBe("less than a minute left");
    expect(formatWaitRemaining(deadlineAfter(0), NOW)).toBe("less than a minute left");
  });

  it("does not go negative after the deadline", () => {
    expect(formatWaitRemaining(deadlineAfter(-1), NOW)).toBe("less than a minute left");
  });
});
