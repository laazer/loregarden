import { progressFillStyle } from "../progressFill";

it.each([
  [0, "scaleX(0)"],
  [42, "scaleX(0.42)"],
  [100, "scaleX(1)"],
  [180, "scaleX(1)"],
  [-5, "scaleX(0)"],
  [Number.NaN, "scaleX(0)"],
])("draws %p%% of the track as %s", (percent, transform) => {
  expect(progressFillStyle(percent)).toEqual({ transform });
});
