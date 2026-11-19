# A chat's living map files plan steps out of order and unlinked

The repository is at `/app`; the Python server is in `/app/server` (run its tests with `cd /app/server && uv run pytest <path>`).

A chat's living map and its plan card show the same plan but disagree. All the records parsed from one chat message share a timestamp, and the map's filing order breaks that tie by the record's `source_ref`, so a plan's steps are filed alphabetically by step id and its `qa:` questions land ahead of its `todo:` steps. The steps are also filed as loose rows rather than under the plan.

File records from one message in the order the message presented them, and file each plan as a topic named like its card with its steps under it. The plan card's "continues from step N" footer should name the first open step, not assume steps are ticked in order.
