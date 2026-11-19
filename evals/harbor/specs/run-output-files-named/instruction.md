# A run whose output files never appeared fails with no reason

The repository is at `/app`; the Python server is in `/app/server` (run its tests with `cd /app/server && uv run pytest <path>`).

A detached agent run is settled from its `.out`, `.err` and `.rc` files. When **none** of the three exists (the run wrote them somewhere else, or never started), the live settle path records an empty stderr, so the run fails with "Agent run failed with no reason recorded". That gives the operator nowhere to look.

The path that settles a reattached run (`services/run_resupervise.py`) already says the run "exited without recording an exit code" in this situation, so the two paths disagree about the same run.

Make both paths settle stderr the same way. With no exit code, say so. When none of the three output files exists, the stderr must also name all three expected paths, so the operator can see where the run was supposed to write.
