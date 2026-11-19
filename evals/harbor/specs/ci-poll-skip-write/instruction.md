# Every CI poll rewrites every open PR's CI row, even when nothing changed

The repository is at `/app`; the Python server is in `/app/server` (run its tests with `cd /app/server && uv run pytest <path>`).

Every CI poll tick (`services/ci_*`) re-stamps `ci_run_results.updated_at` for every open PR, even when nothing about the run changed: one write per PR every two minutes, each needing SQLite's whole-file lock. One of them queued behind another writer past the 30 s busy timeout and failed with "database is locked".

Make a poll that learns nothing new write nothing. A write is still needed for a new commit, a status change, a newly attached failure, or a row that is no longer the ticket's latest (for example, a head pushed back to an older commit). `updated_at` should keep meaning "last change".
