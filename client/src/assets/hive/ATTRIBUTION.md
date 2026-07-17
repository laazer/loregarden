# Hive skin assets

Pixel sprites for LoreGarden Hive skins (The Runeplace, The Officeplace, The Netplace, The Starplace).

## Procedural baseline

`scripts/hive/generate_sprites.py` — original placeholder sprites for all four skins.

## The Officeplace (`officeplace/`)

Imported from [munder-difflin](https://github.com/chaitanyagiri/munder-difflin) via `scripts/hive/import_officeplace.sh`:

| Asset | Source | License |
|-------|--------|---------|
| `agents/*.png` | Procedural portraits in `portraitArt.ts` | MIT (munder-difflin source) |
| `floor.png`, `stations/`, `artifacts/`, `events/`, `props/` | Crops from LimeZu tilesets bundled in munder-difflin | **Non-commercial only** — see `LIMEZUASSETS-LICENSE.txt` in munder-difflin |

`props/*.png` are 16px-grid crops of `office-tileset.png` (desks, chairs, shelves, fridge,
vending machine, plants, monitors, toilets…), placed by `layouts/officeplaceFloorPlan.ts`.

Only `office-tileset.png` is drawn top-down; `interiors.png` is a home/shop set in
perspective and has no top-down office furniture. Reception's wrap-around desk is built
from `desk-wood` pieces rather than a single sprite — the tileset has no L/U desk.

`floor-bg.png` is an AI-generated (Gemini "Nano Banana") top-down render of the office,
used as the floor background — same regime as the regenerated skin art noted below. When
it is present, the drawn floor-plan layer (rooms/desks/props/walls in `HiveCssRooms`) is
suppressed and only the NPC overlays render on top. NPC coordinates in
`layouts/officeplaceLayout.ts` are hand-aligned to the image. Collision
(`createOfficeplaceOpenWalkGrid`) is a set of room rectangles bridged by 1-tile doorways,
so agents route through doors rather than across walls; it was traced against the image
with a walkability overlay. The green **MDR** room and the foundation strip are excluded —
roaming agents never enter MDR. MDR holds the `testing` station and static placeholder QA
staff (`OFFICEPLACE_MDR_STAFF`); dedicated QA NPCs will replace the placeholders later.

`scenery.png` (the old baked bullpen bitmap) is **no longer used** — `floor-bg.png`
replaces it. The map bake in `scripts/hive/bake_office_scenery.py` and the walkability
export no longer feed the app.

## Other skins

The Runeplace, The Netplace, and The Starplace packs use the procedural baseline unless regenerated (e.g. Gemini Nano Banana).
