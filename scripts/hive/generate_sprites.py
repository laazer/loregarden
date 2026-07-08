#!/usr/bin/env python3
"""Generate LoreGarden hive skin sprites.

Default: procedural pixel-art PNGs (no network) so builds ship complete assets.
Optional: set GEMINI_API_KEY to regenerate via Nano Banana (gemini-*-flash-image).

Usage:
  python3 scripts/hive/generate_sprites.py
  GEMINI_API_KEY=... python3 scripts/hive/generate_sprites.py --nano-banana
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "client" / "src" / "assets" / "hive"

SKINS = ("warcraft", "dunder_mifflin", "cyberpunk", "starcraft")
CASTS = ("worker", "planner", "implementer", "tester", "reviewer")
STATIONS = ("planner_hq", "research", "coding", "testing", "deploy")
ARTIFACTS = ("context", "diff")
EVENTS = ("waiting", "error")

# RGB palettes per skin (bg key color 255,0,255 is transparent)
PALETTES = {
    "warcraft": {
        "bg": (34, 48, 28),
        "ink": (20, 16, 12),
        "a": (196, 160, 64),
        "b": (72, 120, 56),
        "c": (180, 64, 48),
        "d": (240, 220, 160),
        "accent": (80, 160, 220),
    },
    "dunder_mifflin": {
        "bg": (200, 184, 150),
        "ink": (40, 36, 32),
        "a": (90, 130, 170),
        "b": (220, 210, 190),
        "c": (180, 70, 60),
        "d": (70, 70, 70),
        "accent": (240, 200, 80),
    },
    "cyberpunk": {
        "bg": (18, 10, 28),
        "ink": (240, 80, 200),
        "a": (40, 220, 255),
        "b": (255, 60, 140),
        "c": (120, 40, 200),
        "d": (255, 230, 80),
        "accent": (60, 255, 180),
    },
    "starcraft": {
        "bg": (28, 32, 26),
        "ink": (10, 12, 10),
        "a": (80, 120, 200),
        "b": (60, 180, 80),
        "c": (180, 180, 190),
        "d": (220, 180, 60),
        "accent": (40, 200, 220),
    },
}

MAGENTA = (255, 0, 255, 0)


def png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def write_png(path: Path, width: int, height: int, rgba_rows: list[list[tuple[int, int, int, int]]]) -> None:
    raw = bytearray()
    for row in rgba_rows:
        raw.append(0)
        for r, g, b, a in row:
            raw.extend((r, g, b, a))
    compressed = zlib.compress(bytes(raw), 9)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", ihdr) + png_chunk(b"IDAT", compressed) + png_chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def blank(w: int, h: int) -> list[list[tuple[int, int, int, int]]]:
    return [[MAGENTA for _ in range(w)] for _ in range(h)]


def set_px(px: list[list[tuple[int, int, int, int]]], x: int, y: int, color: tuple[int, int, int], a: int = 255) -> None:
    if 0 <= y < len(px) and 0 <= x < len(px[0]):
        r, g, b = color
        px[y][x] = (r, g, b, a)


def fill_rect(px, x0, y0, w, h, color, a=255):
    for y in range(y0, y0 + h):
        for x in range(x0, x0 + w):
            set_px(px, x, y, color, a)


def stroke_rect(px, x0, y0, w, h, color):
    for x in range(x0, x0 + w):
        set_px(px, x, y0, color)
        set_px(px, x, y0 + h - 1, color)
    for y in range(y0, y0 + h):
        set_px(px, x0, y, color)
        set_px(px, x0 + w - 1, y, color)


def draw_agent(skin: str, cast: str) -> list[list[tuple[int, int, int, int]]]:
    p = PALETTES[skin]
    px = blank(32, 32)
    body = {
        "worker": p["b"],
        "planner": p["a"],
        "implementer": p["c"],
        "tester": p["accent"],
        "reviewer": p["d"],
    }[cast]
    # shadow
    fill_rect(px, 10, 28, 12, 3, p["ink"], 80)
    # legs
    fill_rect(px, 12, 22, 3, 6, p["ink"])
    fill_rect(px, 17, 22, 3, 6, p["ink"])
    # torso
    fill_rect(px, 11, 14, 10, 9, body)
    stroke_rect(px, 11, 14, 10, 9, p["ink"])
    # head
    fill_rect(px, 13, 7, 6, 6, p["d"])
    stroke_rect(px, 13, 7, 6, 6, p["ink"])
    # cast accent hat / gear
    if cast == "planner":
        fill_rect(px, 12, 5, 8, 2, p["accent"])
    elif cast == "implementer":
        fill_rect(px, 20, 16, 4, 4, p["a"])
    elif cast == "tester":
        fill_rect(px, 14, 4, 4, 2, p["c"])
    elif cast == "reviewer":
        fill_rect(px, 12, 9, 8, 1, p["accent"])
    return px


def draw_station(skin: str, station: str) -> list[list[tuple[int, int, int, int]]]:
    p = PALETTES[skin]
    px = blank(48, 48)
    palette_pick = {
        "planner_hq": p["a"],
        "research": p["accent"],
        "coding": p["c"],
        "testing": p["b"],
        "deploy": p["d"],
    }[station]
    fill_rect(px, 6, 18, 36, 24, palette_pick)
    stroke_rect(px, 6, 18, 36, 24, p["ink"])
    # roof / top
    fill_rect(px, 10, 8, 28, 12, p["d"] if station != "planner_hq" else p["accent"])
    stroke_rect(px, 10, 8, 28, 12, p["ink"])
    # door
    fill_rect(px, 20, 30, 8, 12, p["ink"])
    # window
    fill_rect(px, 12, 24, 5, 5, p["a"])
    fill_rect(px, 31, 24, 5, 5, p["a"])
    if station == "planner_hq":
        fill_rect(px, 21, 4, 6, 6, p["c"])
    return px


def draw_artifact(skin: str, kind: str) -> list[list[tuple[int, int, int, int]]]:
    p = PALETTES[skin]
    px = blank(16, 16)
    if kind == "context":
        fill_rect(px, 3, 2, 10, 12, p["d"])
        stroke_rect(px, 3, 2, 10, 12, p["ink"])
        fill_rect(px, 5, 4, 6, 1, p["a"])
        fill_rect(px, 5, 7, 6, 1, p["a"])
    else:
        fill_rect(px, 2, 3, 12, 10, p["accent"])
        stroke_rect(px, 2, 3, 12, 10, p["ink"])
        fill_rect(px, 4, 5, 8, 1, p["ink"])
        fill_rect(px, 4, 8, 5, 1, p["ink"])
    return px


def draw_event(skin: str, kind: str) -> list[list[tuple[int, int, int, int]]]:
    p = PALETTES[skin]
    px = blank(32, 32)
    if kind == "waiting":
        fill_rect(px, 12, 18, 8, 8, p["c"])
        fill_rect(px, 10, 14, 12, 4, p["accent"])
        stroke_rect(px, 10, 14, 12, 12, p["ink"])
        fill_rect(px, 14, 8, 4, 6, p["a"])
    else:
        # error burst
        fill_rect(px, 8, 8, 16, 16, p["c"])
        stroke_rect(px, 8, 8, 16, 16, p["ink"])
        fill_rect(px, 14, 10, 4, 8, p["d"])
        fill_rect(px, 14, 20, 4, 2, p["d"])
    return px


def draw_floor(skin: str) -> list[list[tuple[int, int, int, int]]]:
    p = PALETTES[skin]
    px = blank(64, 64)
    for y in range(64):
        for x in range(64):
            c = p["bg"] if (x // 8 + y // 8) % 2 == 0 else tuple(max(0, v - 18) for v in p["bg"])
            set_px(px, x, y, c)
    return px


PROMPTS = {
    "warcraft": "1990s Warcraft fantasy RTS pixel art sprite, clean 32x32 game asset, limited palette, hard edges, transparent background, no text",
    "dunder_mifflin": "retro office sitcom pixel art sprite, The Office vibe, muted pastel, clean 32x32 game asset, hard edges, transparent background, no text",
    "cyberpunk": "Cyberpunk Edgerunners neon pixel art sprite, magenta cyan high contrast, clean 32x32 game asset, hard edges, transparent background, no text",
    "starcraft": "StarCraft Brood War Terran pixel art sprite, sci-fi mechanical, clean 32x32 game asset, hard edges, transparent background, no text",
}


def generate_procedural(skin: str) -> dict:
    base = OUT / skin
    rel = {
        "skin": skin,
        "floor": f"{skin}/floor.png",
        "agents": {},
        "stations": {},
        "artifacts": {},
        "events": {},
    }
    write_png(base / "floor.png", 64, 64, draw_floor(skin))
    for cast in CASTS:
        write_png(base / "agents" / f"{cast}.png", 32, 32, draw_agent(skin, cast))
        rel["agents"][cast] = f"{skin}/agents/{cast}.png"
    for station in STATIONS:
        write_png(base / "stations" / f"{station}.png", 48, 48, draw_station(skin, station))
        rel["stations"][station] = f"{skin}/stations/{station}.png"
    for art in ARTIFACTS:
        write_png(base / "artifacts" / f"{art}.png", 16, 16, draw_artifact(skin, art))
        rel["artifacts"][art] = f"{skin}/artifacts/{art}.png"
    for ev in EVENTS:
        write_png(base / "events" / f"{ev}.png", 32, 32, draw_event(skin, ev))
        rel["events"][ev] = f"{skin}/events/{ev}.png"
    return rel


def nearest_downsample_rgba(src: list[list[tuple[int, int, int, int]]], tw: int, th: int):
    sh = len(src)
    sw = len(src[0]) if sh else 0
    out = blank(tw, th)
    for y in range(th):
        for x in range(tw):
            sx = min(sw - 1, int(x * sw / tw))
            sy = min(sh - 1, int(y * sh / th))
            out[y][x] = src[sy][sx]
    return out


def chroma_to_alpha(px):
    out = []
    for row in px:
        nrow = []
        for r, g, b, a in row:
            if r > 240 and g < 30 and b > 240:
                nrow.append(MAGENTA)
            elif r > 250 and g > 250 and b > 250:
                nrow.append(MAGENTA)
            else:
                nrow.append((r, g, b, a))
        out.append(nrow)
    return out


def try_nano_banana(skin: str, subject: str, size: tuple[int, int]) -> list[list[tuple[int, int, int, int]]] | None:
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return None
    try:
        import base64
        import urllib.request

        prompt = f"{PROMPTS[skin]}. Subject: {subject}. Exactly {size[0]}x{size[1]} logical pixels, front view, single sprite centered."
        body = json.dumps(
            {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
            }
        ).encode()
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent?key={api_key}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode())
        for cand in payload.get("candidates", []):
            for part in cand.get("content", {}).get("parts", []):
                inline = part.get("inlineData") or part.get("inline_data")
                if not inline:
                    continue
                data = base64.b64decode(inline.get("data", ""))
                # Prefer Pillow when available
                try:
                    from io import BytesIO
                    from PIL import Image

                    img = Image.open(BytesIO(data)).convert("RGBA")
                    img = img.resize(size, Image.NEAREST)
                    px = [[img.getpixel((x, y)) for x in range(size[0])] for y in range(size[1])]
                    return chroma_to_alpha(px)
                except Exception:
                    return None
    except Exception as exc:
        print(f"[nano-banana] skip {skin}/{subject}: {exc}")
    return None


def write_manifest(rel: dict) -> None:
    path = OUT / "manifests" / f"{rel['skin']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rel, indent=2) + "\n")


def write_manifest_data_ts(all_rels: list[dict]) -> None:
    """Keep client/src/lib/hive/manifestData.ts in sync for Jest/TS imports."""
    ts_path = ROOT / "client" / "src" / "lib" / "hive" / "manifestData.ts"
    lines = [
        'import type { HiveSkinId } from "./skins";',
        "",
        "export type HiveManifestJson = {",
        "  skin: string;",
        "  floor: string;",
        "  agents: Record<string, string>;",
        "  stations: Record<string, string>;",
        "  artifacts: Record<string, string>;",
        "  events: Record<string, string>;",
        "};",
        "",
        "export const HIVE_MANIFEST_DATA: Record<HiveSkinId, HiveManifestJson> = {",
    ]
    for rel in all_rels:
        skin = rel["skin"]
        blob = json.dumps(rel, indent=2)
        # indent body under skin key
        indented = "\n".join("  " + line if line else line for line in blob.splitlines())
        lines.append(f"  {skin}: {indented[2:]},")
    lines.append("};")
    lines.append("")
    ts_path.write_text("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nano-banana", action="store_true", help="Attempt Gemini image gen when API key set")
    args = parser.parse_args()

    all_rels: list[dict] = []
    for skin in SKINS:
        print(f"Generating {skin}...")
        rel = generate_procedural(skin)
        if args.nano_banana:
            subjects = [
                ("agents", CASTS, (32, 32), "agent character {name}"),
                ("stations", STATIONS, (48, 48), "building station {name}"),
                ("artifacts", ARTIFACTS, (16, 16), "inventory item {name}"),
                ("events", EVENTS, (32, 32), "status prop {name}"),
            ]
            for folder, names, size, tmpl in subjects:
                for name in names:
                    px = try_nano_banana(skin, tmpl.format(name=name), size)
                    if px:
                        write_png(OUT / skin / folder / f"{name}.png", size[0], size[1], px)
                        print(f"  nano-banana: {skin}/{folder}/{name}")
        write_manifest(rel)
        all_rels.append(rel)
        print(f"  wrote manifest for {skin}")

    write_manifest_data_ts(all_rels)
    print("  wrote manifestData.ts")

    attribution = OUT / "ATTRIBUTION.md"
    attribution.write_text(
        "# Hive skin assets\n\n"
        "Pixel sprites for LoreGarden Hive skins (Warcraft, Dunder Mifflin, Cyberpunk, StarCraft).\n"
        "Generated for this project via `scripts/hive/generate_sprites.py` "
        "(procedural baseline; optional Gemini Nano Banana regeneration).\n"
        "Not copied from munder-difflin.\n"
    )
    print("Done.")


if __name__ == "__main__":
    main()
