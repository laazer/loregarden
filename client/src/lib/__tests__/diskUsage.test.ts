import type { DiskNode } from "../../api/diskTypes";
import { formatDiskBytes, layoutTreemap, outermostPaths } from "../diskUsage";

const GB = 1024 ** 3;

function node(name: string, size: number, children: DiskNode[] = []): DiskNode {
  return {
    name,
    path: `/r/${name}`,
    size_bytes: size,
    kind: children.length ? "directory" : "file",
    is_dir: children.length > 0,
    file_count: 1,
    children,
    folded_count: 0,
  };
}

describe("layoutTreemap", () => {
  it("gives each child an area in proportion to its size, inside the frame", () => {
    const focus = node("root", 4 * GB, [node("big", 3 * GB), node("small", 1 * GB)]);
    const tiles = layoutTreemap(focus, 400, 300);
    const area = (name: string) => {
      const tile = tiles.find((t) => t.node.name === name)!;
      return tile.width * tile.height;
    };
    expect(area("big") / area("small")).toBeCloseTo(3, 0);
    for (const tile of tiles) {
      expect(tile.x).toBeGreaterThanOrEqual(0);
      expect(tile.y).toBeGreaterThanOrEqual(0);
      expect(tile.x + tile.width).toBeLessThanOrEqual(400);
      expect(tile.y + tile.height).toBeLessThanOrEqual(300);
    }
  });

  it("draws two levels and marks the outer one as a group", () => {
    const focus = node("root", 2 * GB, [node("dir", 2 * GB, [node("a", GB), node("b", GB)])]);
    const tiles = layoutTreemap(focus, 400, 300);
    expect(tiles.map((t) => [t.node.name, t.depth, t.isGroup])).toEqual([
      ["dir", 1, true],
      ["a", 2, false],
      ["b", 2, false],
    ]);
  });

  it("draws nothing for an empty focus or an unmeasured frame", () => {
    expect(layoutTreemap(node("empty", 0), 400, 300)).toEqual([]);
    expect(layoutTreemap(node("root", GB, [node("a", GB)]), 0, 0)).toEqual([]);
  });
});

describe("outermostPaths", () => {
  it("drops a path inside another marked path, and only on a path boundary", () => {
    expect(outermostPaths(["/a/b/c", "/a/b", "/a/bc", "/x"]).sort()).toEqual(["/a/b", "/a/bc", "/x"]);
  });
});

describe("formatDiskBytes", () => {
  it("uses binary units", () => {
    expect(formatDiskBytes(512)).toBe("512 B");
    expect(formatDiskBytes(1.5 * GB)).toBe("1.5 GB");
    expect(formatDiskBytes(250 * GB)).toBe("250 GB");
  });
});
