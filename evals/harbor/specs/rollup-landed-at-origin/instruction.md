# A parent's landed work is reported as unlanded once its target branch is pruned

The repository is at `/app`; the Python server is in `/app/server` (run its tests with `cd /app/server && uv run pytest <path>`).

`services/parent_own_work.py::unlanded_own_work` decides whether a parent ticket's own work has landed on its target. GitHub merges a PR and then deletes the target branch; once the local checkout prunes it, the target exists only as `refs/remotes/origin/<target>`. In that state the check reports the parent as closed over unlanded work, even though its work was squash-merged into the target. lg-improved-memory-662 and lg-initiatives-cross-732 are two live examples.

Make the check recognise work that has landed on a target (or base) branch that now exists only on origin.
