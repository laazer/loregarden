#!/usr/bin/env bash
# Import munder-difflin office art into LoreGarden Hive officeplace skin.
#
#   MUNDER_DIFFLIN_ROOT=/path/to/munder-difflin ./scripts/hive/import_officeplace.sh
#
# Agents: procedural portraits from portraitArt.ts (MIT).
# Floor/stations/props: crops from LimeZu tilesets (non-commercial — see ATTRIBUTION.md).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MD="${MUNDER_DIFFLIN_ROOT:-}"
OUT="$ROOT/client/src/assets/hive/officeplace"
ASSETS="$MD/src/renderer/src/assets"

if [[ -z "$MD" || ! -d "$ASSETS/tilesets" ]]; then
  echo "Set MUNDER_DIFFLIN_ROOT to a munder-difflin checkout (with src/renderer/src/assets/tilesets)." >&2
  exit 1
fi

mkdir -p "$OUT/agents" "$OUT/stations" "$OUT/artifacts" "$OUT/events"

OT="$ASSETS/tilesets/office-tileset.png"
A5="$ASSETS/tilesets/a5-office-floors-walls.png"

# Black-key LimeZu tileset crop → PNG with transparency, point-resize to target.
tile() {
  local src="$1" crop="$2" dest="$3" size="$4"
  magick "$src" -crop "$crop" +repage \
    -fuzz 4% -transparent '#000000' \
    -trim +repage \
    -filter point -resize "${size}x${size}" \
    -background none -gravity center -extent "${size}x${size}" \
    PNG32:"$dest"
  echo "  wrote $(basename "$(dirname "$dest")")/$(basename "$dest")"
}

# Office carpet checker from office.tmj floor gids 783/784 (a5 local tiles 270/271).
floor() {
  local dest="$1"
  magick "$A5" -crop "32x32+224+256" +repage \
    -filter point -resize 64x64 \
    PNG32:"$dest"
  echo "  wrote floor.png"
}

echo "Exporting procedural cast from munder-difflin..."
MUNDER_DIFFLIN_ROOT="$MD" npx --yes tsx "$ROOT/scripts/hive/export_officeplace_cast.ts"

echo "Cropping LimeZu office tiles..."

floor "$OUT/floor.png"

# office-tileset.png — 16×16 grid; crops verified against munder-difflin office.tmj
tile "$OT" "48x48+128+32" "$OUT/stations/planner_hq.png" 128
tile "$OT" "48x48+128+128" "$OUT/stations/research.png" 128
tile "$OT" "32x32+192+352" "$OUT/stations/coding.png" 128
tile "$OT" "64x32+64+304" "$OUT/stations/testing.png" 128
tile "$OT" "48x48+176+432" "$OUT/stations/deploy.png" 128

# Café cluster in office-tileset — avoid cup tiles (208/224+304); use binder folders + machine only.
tile "$OT" "16x16+160+320" "$OUT/artifacts/context.png" 32
tile "$OT" "16x16+176+320" "$OUT/artifacts/diff.png" 32

tile "$OT" "32x32+160+304" "$OUT/events/waiting.png" 64
tile "$OT" "32x48+192+304" "$OUT/events/error.png" 64

echo "Baking office scenery from munder-difflin map..."
MUNDER_DIFFLIN_ROOT="$MD" python3 "$ROOT/scripts/hive/bake_office_scenery.py"

echo "Exporting office walkability grid..."
MUNDER_DIFFLIN_ROOT="$MD" python3 "$ROOT/scripts/hive/export_office_walkability.py"

echo "Done. Hard-refresh Hive tab (officeplace skin)."
