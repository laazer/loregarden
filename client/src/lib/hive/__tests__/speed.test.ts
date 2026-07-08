import {
  DEFAULT_HIVE_SPEED_MULTIPLIER,
  HIVE_SPEED_MULTIPLIERS,
  hiveReplayFrameMs,
  hiveScaledMs,
  hiveSpeedIndexFor,
  hiveSpeedLabel,
} from "../speed";

describe("hive speed", () => {
  it("labels multipliers", () => {
    expect(hiveSpeedLabel(1)).toBe("1×");
    expect(hiveSpeedLabel(2)).toBe("2×");
  });

  it("scales motion and replay durations inversely", () => {
    expect(hiveScaledMs(900, 2)).toBe(450);
    expect(hiveReplayFrameMs(4)).toBe(225);
    expect(hiveScaledMs(850, 0.5)).toBe(1700);
  });

  it("falls back to default speed index for unknown values", () => {
    expect(hiveSpeedIndexFor(99)).toBe(HIVE_SPEED_MULTIPLIERS.indexOf(DEFAULT_HIVE_SPEED_MULTIPLIER));
  });
});
