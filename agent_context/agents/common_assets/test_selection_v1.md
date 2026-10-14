# Which tests a stage runs

A stage run is one headless turn. **When your turn ends, the process ends.** A
command you sent to the background never reports back, and a run that ends its
turn waiting on one exits with no stage report, which blocks the stage. In the 30
days to 2026-10-14, 11 of 48 `backend_implementer` runs ended exactly that way,
"waiting on the full-suite run".

So run only what finishes inside your turn, in the foreground.

- **Run the tests that reach the change, not a sweep.** The full suite, or a broad
  `pytest -k "a or b or c"` across the tree, is the pre-push hook's and CI's job,
  and CI runs it again before an integration branch reaches main. Inside a stage it
  costs a heavy capacity lease and the queue for it, which can be hours, and proves
  nothing the narrow run does not. One verify run sat 53 minutes in such a sweep
  and was killed with no output.
- **If the workspace ships a test selector, use it.** In loregarden:
  `python .lefthook/scripts/select_pytest_targets.py --repo . --base origin/main`
  prints the server test files the branch's changes reach (the import graph, the
  same set pre-push runs). Exit 2 means it could not map the change and would run
  everything; do not, and instead name the test files yourself. For the client,
  `npx jest --changedSince=origin/main`.
- **Otherwise, name the test files** that cover the changed modules and run those.
- **Size the capacity request to the run.** A few test files need no reservation.
  Many files under `-n` take `capacity-run.sh --footprint heavy`. The Bash tool's
  ceiling is 10 minutes (`timeout: 600000`), and a few hundred files run in about
  7. If it times out still queued, report `blocked` with `blocked_kind: harness`
  naming the holder it printed. Never wait out your own run timeout.
- **Never background a test run and end your turn.** If the foreground ceiling is
  too short for the run, the run is too wide, so narrow it.
- **A criterion that says "the full suite passes"** is met by the selected set plus
  CI. Say in your report which files you ran and that CI runs the rest. Do not
  block on it, and do not reject someone else's stage over it.
