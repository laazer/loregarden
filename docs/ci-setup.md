# CI Integration

Loregarden reads the checks on a ticket's pull request. When CI fails on a
ticket that is already done, it reopens the ticket for an agent to fix it.

## Status (2026-10-12)

| Piece | State |
|---|---|
| Reading CI results | **Polling, by default.** Every `ci_poll_tick_seconds`, one `gh pr list` per workspace. No setup beyond a signed-in `gh`. |
| GitHub webhook | Optional. A delivery makes that workspace poll now instead of waiting for the next tick. |
| Auto-fix | Done tickets that publish their own branch. Bounded by `ci_retry_limit`. |
| Tree roots (PR on `integration/<id>`) | **Auto-fixed** on the root, the same way and under the same budget. See *Tree roots* below. |
| GitLab, generic webhooks | **Removed.** The endpoint answers 400. |

Before this, the auto-fix was a stub. It logged "Would trigger…" below the
console's log level and left every attempt `running`. The webhook was its only
input, and GitHub cannot reach a server on localhost: the live database had no
CI results at all.

## How it works

Each tick reads every open pull request in each workspace. A PR whose head
branch is the one a ticket publishes (its own branch, or its tree's
integration branch) is matched to that ticket. Other PRs are ignored.

1. **Record.** Each (ticket, head commit) pair gets one row in `ci_run_results`, updated as
   checks settle. A commit is `pending` until no check is still running, then
   `failing` if any check failed, otherwise `passing` (or `skipped` when every
   check was skipped). The first time a commit reads as failing, the failed
   jobs' logs are fetched with `gh run view --job <id> --log-failed` and stored
   (tail only). A check that is not a GitHub Actions job is listed by name in
   the failure summary, since it has no log to fetch.
2. **Fix.** If the ticket is `done`, it is reopened at its terminal stage with
   the `repair` agent pinned. The agent's brief contains the failing checks and
   the logs. The fix turn runs through the normal orchestration and uses the
   slot pool, so a full pool queues it rather than dropping it. When the turn
   passes, the terminal stage lands the ticket again and publishing pushes the
   fix to the PR, which starts CI again. The ticket's history shows a
   `dispatched_ci_fix` decision.
3. **Settle.** The attempt closes on the first settled result for a newer
   commit: `succeeded` if it passed, `failed` if it is still red, in which case
   the next attempt starts. It also closes as `failed` if the turn blocks, if
   the ticket is abandoned, or if the turn finishes without a new commit.
4. **Give up.** Once a ticket has spent `ci_retry_limit` fix turns since CI last
   passed on it, the next failure blocks the ticket at its terminal stage, with
   the failing checks in the block message.

### Tree roots

A tree's PR is on `integration/<root id>`, where every child landed, but the
root's turns run on the root's own branch and landing skips a root. So before
the repair turn, the root's branch merges the integration branch (in the
checkout that holds it, if one does) and the point it reached is recorded as
`refs/loregarden/tree-fix-base/<root branch>`. When the turn passes, publishing
moves the integration branch onto the commits the turn made past that point,
fast-forwarding where it can, and pushes it. A turn that committed nothing
leaves the PR where it was, and its attempt fails like any other. One
exception: when the root's worktree had been retired, recreating it merges the
newest base into the root's branch, and that merge is folded and pushed, so CI
runs again on it. The rest of the loop,
including the budget and the block, is unchanged
(`services/root_integration_sync.py`).

The fix goes to the root rather than to the child whose work broke CI. A
failing check names a test, not a culprit, and a failure can come from two
children's work combined. A child's fix would also land without publishing,
because only the root's completion pushes the integration branch.

### What is not fixed automatically, and why

These are recorded as a `refused` attempt with the reason and logged at
warning. They are not charged to the budget.

- **The root's branch cannot take the integration branch.** The checkout
  holding it has uncommitted changes, or the merge conflicts. Nothing is
  discarded; the merge is aborted.
- **Unreachable branch.** The PR is on neither the ticket's own branch nor the
  integration branch it roots.
- **Head moved.** The PR's head is not the local branch tip, which means someone
  pushed to it from outside Loregarden. A fix built locally would not apply to it.
- **Dispatch failed.** There is no `repair` agent, no workflow with a terminal
  stage, or an orchestration is already running on the ticket.

These are expected and record nothing:

- **The ticket is not done.** Its own workflow publishes the next commit.
- **The commit already had its attempt.**

## Operator actions

The ticket's **PR** tab lists every attempt, newest first, with the reason for
each refused or failed one. While CI is failing on an open PR, it also offers
**Retry auto-fix** and **Skip CI gate**, which call the two endpoints below.

| Endpoint | Does |
|---|---|
| `GET /api/ci/status/{ticket_id}` | Latest result, and every attempt on the ticket |
| `POST /api/ci/trigger-auto-fix/{ticket_id}` | A fix turn on the latest result now, including a commit that already had its attempt. On refusal, 409 with the reason. |
| `POST /api/ci/manual-override/{ticket_id}` | Marks the latest commit `skipped`. Polling will not overwrite that, and it gets no fix turn. |

## Configuration

Environment variables (prefix `LOREGARDEN_`):

| Setting | Default | Meaning |
|---|---|---|
| `CI_ENABLED` | `true` | Off: no polling, the webhook records nothing, no fix turns |
| `CI_POLL_TICK_SECONDS` | `120` | How often open PRs are read; `0` disables polling |
| `CI_RETRY_LIMIT` | `3` | Fix turns per ticket between passing results |
| `CI_AUTO_FIX_TIMEOUT` | `600` | Agent run timeout for a fix turn, in seconds |
| `CI_LOG_RETENTION_DAYS` | `30` | Stored failed-job logs are cleared after this; the summary is kept |
| `CI_WEBHOOK_SECRET` | empty | Webhook signature secret; empty disables verification |

Neither the poller nor fix turns run in a sandbox (`task sandbox`). The sandbox's
database is a snapshot, and reopening its done tickets would dispatch agents
against work that has since moved on.

## Optional: GitHub webhook

Only useful if GitHub can reach this server (a tunnel, or a hosted instance).
It does not replace polling. It only shortens the wait.

1. Generate a secret and set `LOREGARDEN_CI_WEBHOOK_SECRET`:
   ```bash
   python3 -c "import secrets; print(secrets.token_hex(32))"
   ```
2. In the repository, go to **Settings → Webhooks → Add webhook** and set:
   - Payload URL: `https://<host>/api/ci/webhook/<workspace id or slug>`
   - Content type: `application/json`
   - Secret: the value from step 1
   - Events: **Workflow runs** only

Other GitHub events are answered `ignored`. A delivery without a GitHub event
header is refused with 400.

## Troubleshooting

- **No results for a ticket.** The PR's head branch must be the branch the
  ticket publishes. Check that the ticket's branch matches the PR, and that
  `gh pr list` works from the workspace root. A failed listing is logged at
  warning as `CI poll for <slug>: could not list pull requests`.
- **Attempt `refused`.** Its `result_summary` gives the reason. See *What is not
  fixed automatically* above.
- **Ticket blocked after CI failures.** The fix budget is spent. Fix the branch,
  then requeue the ticket's terminal stage.
