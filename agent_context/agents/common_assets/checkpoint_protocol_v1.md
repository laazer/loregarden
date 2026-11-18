# Checkpoint protocol (autonomous mode)

Ask without stopping: put the question in front of the operator with the default you are taking,
then proceed on it.

## When to log

**Failures vs ambiguity:** Failed shell commands, non-zero test runs, missing tools, or I/O errors are **failures**, not checkpoint ambiguity. Surface **verbatim command output** (stderr + failing stdout tail) in your reply and in the scoped log; do not route failures through "Assumption made" as if they were optional judgment calls. For failures, log output and next steps (e.g. Stage `BLOCKED`); do not silently continue a pipeline step without that evidence.

When you have a **question, ambiguity, risk, or design decision** (not a hard failure above) that would normally warrant human input — anything you "would have asked":

1. Call `loregarden_ask_operator` — **never** bury the question in a checkpoint entry, where no
   inbox, ticket panel or gate will ever show it to anyone:

   ```json
   {
     "ticket_id": "<ticket-id>",
     "question": "<the exact question, answerable without opening the code>",
     "options": ["<the conservative choice you will take>", "<the alternative>"],
     "default": "<the conservative choice you will take>",
     "confidence": "low | medium | high",
     "reversibility": "easy | costly | irreversible",
     "affects": "<the stage whose work changes if the operator picks otherwise, at or before yours>",
     "context": "<what you saw: the file, the criterion, the conflict>"
   }
   ```

2. Read the reply's `blocking`.
   - **false:** carry on with your default now. The operator confirms or overrides it later; an
     override sends `affects` back with their answer, and the gate waits for an answer when your
     confidence was below high.
   - **true:** the question must stop the stage (you asked, confidence is low, the default is
     irreversible, or the answer would change criteria, scope or tests). Stop and report the stage
     `blocked` with `blocked_kind: "decision"`; it reruns with the answer.

3. Ask each question once. A retried call with the same words returns the same question.

`loregarden_append_checkpoint` is now only for an **assumption that needs nobody's answer** — a
local naming or style choice you would never put to a person. Entries accumulate in one file per
ticket+run in the Obsidian vault; never write a checkpoint file directly.

Steps 1–2 apply to **judgment ambiguity** only. If the underlying issue is a failed command or test, stop treating the step as succeeded; surface output per `workflow_enforcement_v1.md` (**Tool, script, and test failures**).

## Scoped log (subagents) and index (orchestrator)

**Subagents** (Spec, Test Designer, Test Breaker, Implementation, Python Reviewer, AC Gatekeeper, Learning, and all consultative agents):
- **Write via:** `loregarden_append_checkpoint` with your own `ticket_id` + `run_id` — your ticket-scoped log only.
- **Read via:** `loregarden_search_memory` scoped to your `workspace_slug`, or `loregarden_memory_status` to discover the resolved Checkpoints dir if you need to browse it directly.
- Do **not** call `loregarden_append_checkpoint` for another ticket's `ticket_id`, and do not read other tickets' checkpoint entries.

**Orchestrator** (autopilot, ap-continue, c-continue, feature):
- Maintains a thin run-level index (run start/end + one-line pointer per ticket — never full body entries) as its own checkpoint entry, distinct from subagents' per-ticket logs.
- Updates the index after each ticket completes — not inside subagent prompts.

`<ticket-id>`: short slug (e.g. `feat-add-org-filter`). `<run-id>`: timestamp + stage (e.g. `2026-06-16T10-00-00Z-spec`).

**Hard rule:** Subagents must never fabricate or reuse another ticket's `run_id`. Full checkpoint bodies (`### [...]`, `**Assumption made:**`, etc.) belong only in the ticket-scoped log entry, not the orchestrator's index.

## Per-stage resolution hints

- **Test Designer:** Resolve by writing the strictest defensible test allowed by the spec.
- **Test Breaker:** Resolve by writing a test that encodes the conservative assumption, and ask the question with `affects` set to your own stage.
- **Implementation:** Resolve by choosing the simplest implementation consistent with spec and tests. Editing a test an earlier stage wrote changes the requirement: ask, with `affects` naming that stage.
- **AC Gatekeeper:** Resolve by holding Stage at `INTEGRATION` and escalating to the appropriate agent rather than marking `COMPLETE` with unverified ACs.
