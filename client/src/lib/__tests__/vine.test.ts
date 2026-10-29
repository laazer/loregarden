import { stemGrown, stemPath, vineMark, vineSide } from "../vine";

describe("vineMark", () => {
  it("grows a leaf for a finished step, buds for the rest, and a stub for a pruned one", () => {
    expect(vineMark("done", false)).toBe("leaf");
    expect(vineMark("running", false)).toBe("bud-open");
    expect(vineMark("awaiting", false)).toBe("bud-held");
    expect(vineMark("blocked", false)).toBe("bud-blocked");
    expect(vineMark("pending", false)).toBe("bud-closed");
    expect(vineMark("wont_do", false)).toBe("pruned");
  });

  it("opens the bud of the stage the run is on, before an agent picks it up", () => {
    expect(vineMark("pending", true)).toBe("bud-open");
    // Current never overrides a settled or stopped state.
    expect(vineMark("done", true)).toBe("leaf");
    expect(vineMark("blocked", true)).toBe("bud-blocked");
  });

  it("grows the stem below a step once the run is past it", () => {
    expect(stemGrown("leaf")).toBe(true);
    expect(stemGrown("pruned")).toBe(true);
    expect(stemGrown("bud-open")).toBe(false);
    expect(stemGrown("bud-closed")).toBe(false);
  });
});

describe("the vine's shape comes from the index alone", () => {
  it("alternates sides, starting right", () => {
    expect([0, 1, 2, 3].map(vineSide)).toEqual(["right", "left", "right", "left"]);
  });

  it("leans each stem toward its row's side and returns it to the centre", () => {
    expect(stemPath(0)).toBe("M 7 0 C 11 30, 11 70, 7 100");
    expect(stemPath(1)).toBe("M 7 0 C 3 30, 3 70, 7 100");
  });
});
