# A merge-queue retry stages the old head, ignoring the author's fix

The repository is at `/app`; the Python server is in `/app/server` (run its tests with `cd /app/server && uv run pytest <path>`).

When the merge queue (`services/merge_queue*`) retries an entry that failed on a conflict, it stages a commit cut from the entry's **recorded** head. If the PR author pushed a fix to their branch while the entry waited, the retry still stages the old, conflicting head and fails again. PR #879 hit this twice.

A retry must stage the author's current head, so a push that fixed the conflict is what gets staged. The push that lands the staged commit must still lease on the head it was cut from.
