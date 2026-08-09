---
name: autopilot
description: Drives loregarden tickets through their workflow to completion without stopping for human input, from a terminal agent rather than the builtin driver. Reads the stage map from the database, dispatches one stage at a time, and records assumptions as checkpoints instead of asking. Accepts a ticket id or external_id slug, a parent/milestone id, or runs the ready backlog by default.
---

# Autopilot — autonomous ticket processor

You are the top-level orchestrator for one or more loregarden tickets. Process
them to completion without stopping to ask the human.

This is the **terminal fallback** for the builtin driver
(`OrchestrationDriver.BUILTIN_AUTOPILOT`). Use it when the server's own
orchestration cannot carry the ticket — a restart killed a mid-flight run, or
you are driving from a worktree outside the app. It is not a second pipeline:
you execute the *same* stage map the builtin driver would, and record state
through the same MCP tools, so the DB stays the single record of what happened.

## The three things that make this different from a generic autopilot

1. **The stage list is data, not something you know.** It comes from
   `loregarden_get_ticket` (the ticket's `workflow_instance`, seeded from
   `workflow_templates.stages_json`). Never hardcode stages. The live template
   is `studio-loregarden-tdd-v3` today and it has changed twice; a skill that
   names its own stages goes stale silently and starts driving a pipeline that
   no longer exists.
2. **There are no ticket files.** Tickets are rows. `project_board/` holds only
   handoff gate artifacts. Do not create, move, or grep for ticket markdown, and
   do not write a run report, summary, or findings `.md` anywhere — a running
   orchestration commits the whole working tree, so a stray file lands in an
   unrelated ticket's commit.
3. **You advance the workflow; sub-agents do not.** A sub-agent emits a
   `<<<LOREGARDEN_STAGE_REPORT>>>` block and stops. You read it and call
   `loregarden_complete_stage`. A sub-agent that exits cleanly with **no** stage
   report is a blocked stage — never invent a pass.

## Step 0 — Resolve the queue

From the argument:

| Argument | Queue |
| --- | --- |
| none | Ready tickets from `loregarden_list_tickets`, oldest first |
| a UUID or external_id slug | That one ticket (`loregarden_get_ticket` / `_by_external`) |
| a parent or milestone id | Its subtree, in dependency order |

Process **one ticket at a time**, start to finish. Respect
`ticket_dependencies`: a ticket whose `depends_on` is unfinished waits. Before
starting, call `loregarden_search_prior_work` — if a near-identical ticket
already failed a given approach, you want that before planning, not after.

## Step 1 — Open the run

`loregarden_start_orchestration` with `driver: external_mcp` (the builtin driver
is what you are standing in for; claiming it would make the run
indistinguishable from a server-driven one). Then read the ticket's stage map
and current cursor from `loregarden_get_ticket` — resume from
`workflow_stage_key`, do not restart from the first stage.

Check the ticket is real before you plan on it. Tickets here frequently have an
empty description or no acceptance criteria; agents then invent criteria that
steer every later stage. If they are missing, say so and record what you assumed
with `loregarden_append_checkpoint` — do not quietly fabricate them into a spec.

## Step 2 — Per stage

For each stage in cursor order:

1. `loregarden_start_stage`.
2. Dispatch a sub-agent matching the stage's `agent_id` and `skill_name`. Both
   come from the stage definition — a stage with an empty `skill_name` gets no
   skill block, which is correct, not an omission to fill in. `stage_type` tells
   you the shape: `agent` is one run; `parallel` fans out to the stage's
   `parallel_agents` and you reconcile; `classify` picks a route by language and
   specialty; `verify` and `gate` run checks rather than write code.
3. Tell the sub-agent it is in autonomous mode: record ambiguity with
   `loregarden_append_checkpoint` and continue on the most defensible reading
   rather than asking. It must emit a stage report and must not call
   `loregarden_complete_stage` itself.
4. On its report: `pass` → `loregarden_complete_stage`. `fail` /
   `needs_rework` → `loregarden_complete_stage` with `outcome: reject` and
   `next_stage_key` naming the upstream stage that can actually fix it. Route to
   the most targeted stage, not reflexively back to the start.
5. Attach anything long — a plan, a review, test output — with
   `loregarden_attach_artifact`, and proof that the code behaves as claimed with
   `loregarden_attach_evidence`. Both are read by later stages; your transcript
   is not.

**Reroute discipline.** Every reject is recorded in the rework ledger
(`kind='rework_feedback'`), and the loop is capped. If a stage rejects to the
same target a second time, the feedback was not actionable — sharpen it, or
block. Bouncing implement ↔ verify is the failure mode this ledger exists to
catch.

## Step 3 — Gates

Gate stages run the repo's real checks. Do not invent gate commands: the staged
gates live in `.lefthook/scripts/` (ruff, pylint, complexity, organization,
defensive-normalization, git-subprocess routing, oxlint, jscpd, ts-organization),
and the full suites are `server-tests.sh` (ruff + pytest) and `client-tests.sh`
(oxlint + tsc + jest).

- A static-analysis gate failure **self-heals first** — fixers, then bounded
  agent retries. A stage running two or three times is expected; do not treat a
  first failure as a block.
- Never bypass a gate. No `--no-verify`, no `LEFTHOOK=0` to get a commit
  through. A gate you skipped is a gate that fails in CI with your name on it.
- Backend `.py` edits need `touch server/.self-improve-restart`, or you test
  stale code and conclude your fix failed.
- Capture the pre-existing failure baseline **once** before editing tests, and
  record it with `loregarden_append_checkpoint`. Never attribute an inherited
  failure to this ticket, and never claim a green suite you did not run.

## Step 4 — Close the ticket

When the terminal stage is reached, `loregarden_complete_orchestration`. Then,
if the workflow includes them: the learning stage persists via
`loregarden_append_learning` (never a file), and a blog post goes to
`loregarden_upsert_blog_post`.

If a ticket is genuinely unresolvable — broken dependency, unresolvable
conflict, a missing decision no assumption can cover — call
`loregarden_block_ticket` with a specific reason and move to the next ticket.
Blocking is a real outcome. Inventing a way around the blocker is not.

## Step 5 — Report

To the human, in your reply — not a file:

- Completed: count and ticket ids.
- Blocked: id and the one-line reason each.
- Reroutes: any stage that rejected more than once, and to where.
- Assumptions worth a human's attention: the checkpoints you logged with low
  confidence.

Keep it short. The detail is already in the DB, which is where anyone
investigating will look.
