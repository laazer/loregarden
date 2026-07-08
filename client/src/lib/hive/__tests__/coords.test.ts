import { tilePercent } from "../coords";

describe("tilePercent", () => {
  const map = { width: 34, height: 22 };

  it("anchors at tile center", () => {
    expect(tilePercent({ x: 0, y: 0 }, map)).toEqual({
      left: `${(0.5 / 34) * 100}%`,
      top: `${(0.5 / 22) * 100}%`,
    });
    expect(tilePercent({ x: 16, y: 20 }, map)).toEqual({
      left: `${(16.5 / 34) * 100}%`,
      top: `${(20.5 / 22) * 100}%`,
    });
  });
});
