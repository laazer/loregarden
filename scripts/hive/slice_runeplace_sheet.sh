#!/usr/bin/env bash
# Slice Nano Banana Runeplace sprite sheet (5-column layout) into Hive assets.
#
# WARNING: Do not run this on JPEG exports — checkerboard removal + downscale destroys art.
# Prefer: PNG export from Gemini, slice at NATIVE resolution (128–256px), no despeckle/median.
# Hive CSS displays sprites with image-rendering: pixelated; only downscale slightly, never 32px.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SRC="${1:-$ROOT/client/src/assets/hive/source/runeplace_sheet.png}"
OUT="$ROOT/client/src/assets/hive/runeplace"
TMP="$(mktemp -d)"

cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

if [[ ! -f "$SRC" ]]; then
  echo "Sheet not found: $SRC" >&2
  exit 1
fi

mkdir -p "$OUT/agents" "$OUT/stations" "$OUT/artifacts" "$OUT/events"

dechecker() {
  local in="$1"
  local out="$2"
  magick "$in" \
    -alpha set \
    -fuzz 10% -transparent '#7F7F7F' \
    -fuzz 10% -transparent '#BFBFBF' \
    -fuzz 8% -transparent '#909090' \
    -fuzz 8% -transparent '#CFCFCF' \
    -statistic Median 1x1 \
    -despeckle \
    -trim +repage \
    PNG32:"$out"
}

fit() {
  local in="$1"
  local dest="$2"
  local size="$3"
  magick "$in" \
    -filter point -resize "${size}x${size}" \
    -background none -gravity center -extent "${size}x${size}" \
    PNG32:"$dest"
  echo "  wrote $(basename "$(dirname "$dest")")/$(basename "$dest") (${size}px)"
}

slice_soft() {
  local crop="$1"
  local dest="$2"
  local size="$3"
  local raw="$TMP/$(basename "$dest")"
  magick "$SRC" -crop "$crop" +repage \
    -alpha set \
    -fuzz 5% -transparent '#7F7F7F' \
    -fuzz 5% -transparent '#BFBFBF' \
    -trim +repage \
    PNG32:"$raw"
  fit "$raw" "$dest" "$size"
}

echo "Slicing Runeplace sheet -> $OUT"
echo "  source: $SRC"

# Five vertical columns (~205px each). Per column: agent | station | bottom props.
COL_X=(18 223 428 633 838)
COL_W=188

for i in 0 1 2 3 4; do
  x="${COL_X[$i]}"
  magick "$SRC" -crop "${COL_W}x548+${x}+5" +repage PNG32:"$TMP/col${i}.png"
done

# Agents — top of each column
for i in 0 1 2 3 4; do
  names=(worker planner implementer tester reviewer)
  raw="$TMP/agent_${names[$i]}.png"
  magick "$TMP/col${i}.png" -crop "${COL_W}x142+0+6" +repage PNG32:"$raw"
  dechecker "$raw" "$raw.clean.png"
  fit "$raw.clean.png" "$OUT/agents/${names[$i]}.png" 96
done

# Stations — middle of each column
for i in 0 1 2 3 4; do
  names=(planner_hq research coding testing deploy)
  raw="$TMP/station_${names[$i]}.png"
  magick "$TMP/col${i}.png" -crop "${COL_W}x118+0+156" +repage PNG32:"$raw"
  dechecker "$raw" "$raw.clean.png"
  fit "$raw.clean.png" "$OUT/stations/${names[$i]}.png" 128
done

# Bottom props (absolute sheet coordinates — not column-relative)
slice_soft "96x56+22+398" "$OUT/artifacts/context.png" 32
slice_soft "96x56+108+398" "$OUT/artifacts/diff.png" 32

magick "$TMP/col1.png" -crop "68x78+10+318" +repage PNG32:"$TMP/waiting.png"
dechecker "$TMP/waiting.png" "$TMP/waiting.clean.png"
fit "$TMP/waiting.clean.png" "$OUT/events/waiting.png" 64

magick "$TMP/col1.png" -crop "98x118+90+286" +repage PNG32:"$TMP/error.png"
dechecker "$TMP/error.png" "$TMP/error.clean.png"
fit "$TMP/error.clean.png" "$OUT/events/error.png" 64

# Floor: use seamless procedural tile (sheet grass tile has checkerboard edge when tiled).
python3 - <<'PY'
import struct, zlib
from pathlib import Path
OUT = Path("/Users/jacobbrandt/workspace/loregarden/client/src/assets/hive/runeplace/floor.png")
p = {"bg": (34, 48, 28)}
def write_png(path, w, h, rows):
    raw = bytearray()
    for row in rows:
        raw.append(0)
        for r,g,b,a in row:
            raw.extend((r,g,b,a))
    compressed = zlib.compress(bytes(raw), 9)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(b"IEND", b""))
px = []
for y in range(64):
    row = []
    for x in range(64):
        c = p["bg"] if (x // 8 + y // 8) % 2 == 0 else tuple(max(0, v - 18) for v in p["bg"])
        row.append((*c, 255))
    px.append(row)
write_png(OUT, 64, 64, px)
print("  wrote runeplace/floor.png (procedural seamless)")
PY

echo "Done."
