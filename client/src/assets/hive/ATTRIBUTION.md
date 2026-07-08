# Hive skin assets

Pixel sprites for LoreGarden Hive skins (The Runeplace, The Officeplace, The Netplace, The Starplace).

## Procedural baseline

`scripts/hive/generate_sprites.py` — original placeholder sprites for all four skins.

## The Officeplace (`officeplace/`)

Imported from [munder-difflin](https://github.com/chaitanyagiri/munder-difflin) via `scripts/hive/import_officeplace.sh`:

| Asset | Source | License |
|-------|--------|---------|
| `agents/*.png` | Procedural portraits in `portraitArt.ts` | MIT (munder-difflin source) |
| `floor.png`, `scenery.png`, `stations/`, `artifacts/`, `events/` | Crops / bake from LimeZu tilesets bundled in munder-difflin | **Non-commercial only** — see `LIMEZUASSETS-LICENSE.txt` in munder-difflin |

`scenery.png` is baked from `office.tmj` (floor, walls, desks, water coolers, conference room, etc.) via `scripts/hive/bake_office_scenery.py`.

## Other skins

The Runeplace, The Netplace, and The Starplace packs use the procedural baseline unless regenerated (e.g. Gemini Nano Banana).
