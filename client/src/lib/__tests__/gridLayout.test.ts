import { resizeSplit } from "../gridLayout";
import type { ViewLayout } from "../viewsApi";
import { assertServerAcceptableLayout } from "../../test/viewLayoutContract";

const panel = () => ({ kind: "panel", settings: {} });

function twoPanes(): ViewLayout {
  return {
    kind: "flex_grid",
    containers: { "c-1": panel(), "c-2": panel() },
    root: {
      node: "split",
      id: "n-root",
      size: 1,
      orientation: "horizontal",
      children: [
        { node: "leaf", id: "n-1", size: 0.4, container_id: "c-1" },
        { node: "leaf", id: "n-2", size: 0.6, container_id: "c-2" },
      ],
    },
  };
}

describe("resizing a split", () => {
  it("returns the layout itself when the sizes did not change", () => {
    // A click on a divider with no drag; the same layout back is what tells
    // `useViewLayoutEdit` there is nothing to PATCH.
    const layout = twoPanes();
    expect(resizeSplit(layout, "n-root", [0.4, 0.6])).toBe(layout);
  });

  it("writes new sizes", () => {
    const layout = twoPanes();
    const next = resizeSplit(layout, "n-root", [0.5, 0.5]);
    expect(next).not.toBe(layout);
    assertServerAcceptableLayout(next);
    const root = next.root as { children: { size: number }[] };
    expect(root.children.map((child) => child.size)).toEqual([0.5, 0.5]);
  });
});
