import type { MapItem } from "../../api/livingMapApi";
import { MAP_ROW_PX, inLens, itemMark, lensCounts, tendrilPath } from "../livingMap";

function item(overrides: Partial<MapItem>): MapItem {
  return {
    id: "i1",
    seq: 1,
    kind: "question",
    state: "open",
    source: "record",
    title: "A question",
    detail: "",
    topic_id: null,
    happened_at: "2026-10-27T10:00:00Z",
    ...overrides,
  };
}

describe("lenses", () => {
  const items = [
    item({ id: "t", kind: "topic", state: "open" }),
    item({ id: "q", kind: "question", state: "waiting" }),
    item({ id: "k", kind: "task", state: "open" }),
    item({ id: "a", kind: "assumption", state: "settled" }),
    item({ id: "r", kind: "change", state: "failed" }),
    item({ id: "d", kind: "task", state: "dropped" }),
  ];

  it("answers one question each: waiting, open, decided, changed", () => {
    const ids = (lens: Parameters<typeof inLens>[1]) => items.filter((i) => inLens(i, lens)).map((i) => i.id);
    expect(ids("all")).toEqual(["t", "q", "k", "a", "r", "d"]);
    expect(ids("waiting")).toEqual(["q"]);
    // A topic is what items are about, not an item to resolve; a run is a change.
    expect(ids("open")).toEqual(["k"]);
    expect(ids("decided")).toEqual(["a"]);
    expect(ids("changed")).toEqual(["r"]);
  });

  it("counts every lens at once", () => {
    expect(lensCounts(items)).toEqual({ all: 6, waiting: 1, open: 1, decided: 1, changed: 1 });
  });
});

describe("itemMark", () => {
  it("draws a topic as a fork and every other item by its state", () => {
    expect(itemMark(item({ kind: "topic" }))).toBe("fork");
    expect(itemMark(item({ state: "settled" }))).toBe("leaf");
    expect(itemMark(item({ state: "waiting" }))).toBe("bud-held");
    expect(itemMark(item({ state: "failed" }))).toBe("bud-blocked");
    expect(itemMark(item({ state: "dropped" }))).toBe("pruned");
    expect(itemMark(item({ state: "open" }))).toBe("bud-open");
  });
});

describe("tendrilPath", () => {
  it("runs from the topic's row down the gutter and curls into each member's row", () => {
    const mid = (row: number) => row * MAP_ROW_PX + MAP_ROW_PX / 2;
    const path = tendrilPath(0, [2, 4], 12);

    expect(path.startsWith(`M 0 ${mid(0)}`)).toBe(true);
    // Down to the last member, then one curl ending at each member's row.
    expect(path).toContain(`V ${mid(4) - MAP_ROW_PX / 2}`);
    expect(path).toContain(`0 ${mid(2)}`);
    expect(path).toContain(`0 ${mid(4)}`);
  });

  it("draws nothing for a topic with no rows showing", () => {
    expect(tendrilPath(3, [], 12)).toBe("");
  });
});
