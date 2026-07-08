#!/usr/bin/env python3
"""Export office.tmj walkability grid for Hive pathfinding."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

TILE_ID_MASK = 0x1FFFFFFF
WALKABLE_PREFIXES = ("desk-", "pc-", "warroom-", "entrance", "cafe-")


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    md = Path(os.environ.get("MUNDER_DIFFLIN_ROOT", "/tmp/munder-difflin"))
    map_path = md / "src/renderer/src/assets/maps/office.tmj"
    out = root / "client/src/lib/hive/layouts/officeplaceWalkability.ts"

    if not map_path.is_file():
        print(f"Missing map: {map_path}", file=sys.stderr)
        return 1

    with map_path.open() as f:
        m = json.load(f)

    w, h = m["width"], m["height"]
    grid = [[True] * w for _ in range(h)]

    for layer in m["layers"]:
        if layer.get("name") == "collision" and layer.get("type") == "tilelayer":
            for idx, gid in enumerate(layer["data"]):
                if (gid & TILE_ID_MASK) != 0:
                    grid[idx // w][idx % w] = False

    for layer in m["layers"]:
        if layer.get("name") == "spawn-points":
            for obj in layer.get("objects", []):
                name = obj.get("name", "")
                if any(name.startswith(p) for p in WALKABLE_PREFIXES):
                    x = int(obj["x"] // 16)
                    y = int(obj["y"] // 16)
                    if 0 <= x < w and 0 <= y < h:
                        grid[y][x] = True

    rows = ["".join("1" if cell else "0" for cell in row) for row in grid]
    lines = [
        "/** Auto-generated from munder-difflin office.tmj — do not edit by hand. */",
        f"export const OFFICEPLACE_WALK_ROWS = [",
    ]
    for row in rows:
        lines.append(f'  "{row}",')
    lines.append("] as const;")
    lines.append("")
    lines.append(f"export const OFFICEPLACE_WALK_WIDTH = {w};")
    lines.append(f"export const OFFICEPLACE_WALK_HEIGHT = {h};")
    lines.append("")

    out.write_text("\n".join(lines))
    walkable = sum(row.count("1") for row in rows)
    print(f"  wrote {out.name} ({walkable} walkable / {w * h} tiles)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
