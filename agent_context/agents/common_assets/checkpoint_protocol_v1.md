# Checkpoint protocol (autonomous mode)

Replaces asking the human: log, assume conservatively, proceed.

## When to log

**Failures vs ambiguity:** Failed shell commands, non-zero test runs, missing tools, or I/O errors are **failures**, not checkpoint ambiguity. Surface **verbatim command output** (stderr + failing stdout tail) in your reply and in the scoped log; do not route failures through "Assumption made" as if they were optional judgment calls. For failures, log output and next steps (e.g. Stage `BLOCKED`); do not silently continue a pipeline step without that evidence.

When you have a **question, ambiguity, risk, or design decision** (not a hard failure above) that would normally warrant human input:

1. Call the `loregarden_append_checkpoint` MCP tool — **never write a checkpoint file directly** (checkpoints live in the same Obsidian vault as memory/learnings, not the workspace repo):

   ```json
   {
     "ticket_id": "<ticket-id>",
     "workspace_slug": "<workspace_slug from the run prompt>",
     "run_id": "<run-id>",
     "entry": "### [TICKET_ID] <Stage> — <short label>\n**Would have asked:** <exact question>\n**Assumption made:** <what you decided>\n**Confidence:** Low | Medium | High"
   }
   ```

   Entries accumulate in one file per ticket+run — each call appends, it does not overwrite.

2. Resolve with the most conservative, reasonable assumption.
3. Continue without waiting.

Steps 2–3 apply to **judgment ambiguity** only. If the underlying issue is a failed command or test, stop treating the step as succeeded; surface output per `workflow_enforcement_v1.md` (**Tool, script, and test failures**).

## Scoped log (subagents) and index (orchestrator)

**Subagents** (Spec, Test Designer, Test Breaker, Implementation, Python Reviewer, AC Gatekeeper, Learning, and all consultative agents):
- **Write via:** `loregarden_append_checkpoint` with your own `ticket_id` + `run_id` — your ticket-scoped log only.
- **Read via:** `loregarden_search_memory` scoped to your `workspace_slug`, or `loregarden_memory_status` to discover the resolved Checkpoints dir if you need to browse it directly.
- Do **not** call `loregarden_append_checkpoint` for another ticket's `ticket_id`, and do not read other tickets' checkpoint entries.

**Orchestrator** (autopilot, ap-continue, c-continue, feature):
- Maintains a thin run-level index (run start/end + one-line pointer per ticket — never full body entries) as its own checkpoint entry, distinct from subagents' per-ticket logs.
- Updates the index after each ticket completes — not inside subagent prompts.

`<ticket-id>`: short slug (e.g. `feat-add-org-filter`). `<run-id>`: timestamp + stage (e.g. `2026-06-16T10-00-00Z-spec`).

**Hard rule:** Subagents must never fabricate or reuse another ticket's `run_id`. Full checkpoint bodies (`### [...]`, `**Would have asked:**`, etc.) belong only in the ticket-scoped log entry, not the orchestrator's index.

## Per-stage resolution hints

- **Test Designer:** Resolve by writing the strictest defensible test allowed by the spec.
- **Test Breaker:** Resolve by writing a test that encodes the conservative assumption; mark with `# CHECKPOINT` in the test file.
- **Implementation:** Resolve by choosing the simplest implementation consistent with spec and tests.
- **AC Gatekeeper:** Resolve by holding Stage at `INTEGRATION` and escalating to the appropriate agent rather than marking `COMPLETE` with unverified ACs.
