# A stopped merge-queue attempt stays on the board after its PR was re-queued and merged

The repository is at `/app`; the Python server is in `/app/server` (run its tests with `cd /app/server && uv run pytest <path>`).

When a stopped merge-queue entry (failed, timed out or withdrawn) is put back in the queue, the queue adds a **new** entry rather than moving the old one. The old attempt stays listed: it is returned by the queue's entry listing (so the dashboard, the Lanes page, Home's stopped list and `loregarden_merge_queue_status` all show it) and it still offers Retry, even after the PR's later attempt has merged.

A stopped entry that a later entry for the same PR has replaced should be history: not listed, not retryable, and a retry of it refused. Merged entries keep their current behaviour.
