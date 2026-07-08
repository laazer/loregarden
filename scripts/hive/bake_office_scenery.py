#!/usr/bin/env python3
"""Bake munder-difflin office.tmj layers into a single PNG for Hive CSS floor."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

TILE_ID_MASK = 0x1FFFFFFF
LAYERS = ("floor", "walls", "furniture-below", "furniture-above")
BATCH_SIZE = 25
A5_FIRST_GID = 513
# Warm cream recolor for stark white/grey a5 wall tiles (office cubicle walls).
WALL_WARM_FILL = "#ead8bc"
WALL_WARM_COLORIZE = "28%"


def resolve_tile(gid: int, tilesets: list[dict]) -> tuple[dict, int, int] | None:
    gid &= TILE_ID_MASK
    if gid == 0:
        return None
    for ts in reversed(tilesets):
        first = ts["firstgid"]
        count = ts.get("tilecount", 512)
        if first <= gid < first + count:
            local = gid - first
            cols = ts["columns"]
            tw = ts["tilewidth"]
            th = ts["tileheight"]
            return ts, (local % cols) * tw, (local // cols) * th
    return None


def run_magick(args: list[str]) -> None:
    subprocess.run(["magick", *args], check=True, stdout=subprocess.DEVNULL)


def tile_pipeline(
    src: Path,
    crop: str,
    *,
    warm_wall: bool,
) -> list[str]:
    """ImageMagick args inside (...) for one 16×16 tile crop."""
    args = [
        str(src),
        "-crop",
        crop,
        "+repage",
        "-colorspace",
        "sRGB",
        "-type",
        "TrueColorAlpha",
    ]
    if warm_wall:
        args.extend(["-fill", WALL_WARM_FILL, "-colorize", WALL_WARM_COLORIZE])
    return args


def should_warm_wall(layer_name: str, gid: int) -> bool:
    if layer_name != "walls":
        return False
    return (gid & TILE_ID_MASK) >= A5_FIRST_GID


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    md = Path(os.environ.get("MUNDER_DIFFLIN_ROOT", "/tmp/munder-difflin"))
    map_path = md / "src/renderer/src/assets/maps/office.tmj"
    assets = md / "src/renderer/src/assets/tilesets"
    out = root / "client/src/assets/hive/officeplace/scenery.png"

    if not map_path.is_file():
        print(f"Missing map: {map_path}", file=sys.stderr)
        return 1

    with map_path.open() as f:
        m = json.load(f)

    width = m["width"]
    height = m["height"]
    tw = m["tilewidth"]
    th = m["tileheight"]
    pw, ph = width * tw, height * th

    tilesets = [
        {
            "firstgid": 1,
            "path": assets / "office-tileset.png",
            "columns": 16,
            "tilewidth": 16,
            "tileheight": 16,
            "tilecount": 512,
        },
        {
            "firstgid": 513,
            "path": assets / "a5-office-floors-walls.png",
            "columns": 16,
            "tilewidth": 16,
            "tileheight": 16,
            "tilecount": 512,
        },
        {
            "firstgid": 1025,
            "path": assets / "interiors.png",
            "columns": 16,
            "tilewidth": 16,
            "tileheight": 16,
            "tilecount": 1424,
        },
    ]

    for ts in tilesets:
        if not ts["path"].is_file():
            print(f"Missing tileset: {ts['path']}", file=sys.stderr)
            return 1

    layer_data: dict[str, list[int]] = {}
    for layer in m["layers"]:
        if layer.get("type") == "tilelayer" and layer.get("name") in LAYERS:
            layer_data[layer["name"]] = layer["data"]

    placements: list[tuple[int, int, Path, int, int, str, int]] = []
    for layer_name in LAYERS:
        data = layer_data.get(layer_name)
        if not data:
            continue
        for idx, gid in enumerate(data):
            resolved = resolve_tile(gid, tilesets)
            if not resolved:
                continue
            ts, sx, sy = resolved
            tx = idx % width
            ty = idx // width
            placements.append((tx * tw, ty * th, ts["path"], sx, sy, layer_name, gid))

    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        canvas = tmp_path / "canvas.png"
        run_magick(
            [
                "-size",
                f"{pw}x{ph}",
                "xc:none",
                "-type",
                "TrueColorAlpha",
                "-colorspace",
                "sRGB",
                f"PNG32:{canvas}",
            ],
        )

        for i in range(0, len(placements), BATCH_SIZE):
            chunk = placements[i : i + BATCH_SIZE]
            cmd: list[str] = [str(canvas)]
            for px, py, src, sx, sy, layer_name, gid in chunk:
                crop = f"{tw}x{th}+{sx}+{sy}"
                warm = should_warm_wall(layer_name, gid)
                cmd.append("(")
                cmd.extend(tile_pipeline(src, crop, warm_wall=warm))
                cmd.append(")")
                cmd.extend(
                    [
                        "-geometry",
                        f"+{px}+{py}",
                        "-compose",
                        "Over",
                        "-composite",
                    ],
                )
            cmd.extend(["-type", "TrueColorAlpha", "-colorspace", "sRGB", f"PNG32:{canvas}"])
            run_magick(cmd)

        run_magick([str(canvas), "-type", "TrueColorAlpha", "-colorspace", "sRGB", f"PNG32:{out}"])

    print(f"  wrote scenery.png ({pw}x{ph}, {len(placements)} tiles)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
