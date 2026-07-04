# Checkpoint protocol (autonomous mode)

Replaces asking the human: log, assume conservatively, proceed.

## When to log

**Failures vs ambiguity:** Failed shell commands, non-zero test runs, missing tools, or I/O errors are **failures**, not checkpoint ambiguity. Surface **verbatim command output** (stderr + failing stdout tail) in your reply and in the scoped log; do not route failures through "Assumption made" as if they were optional judgment calls. For failures, log output and next steps (e.g. Stage `BLOCKED`); do not silently continue a pipeline step without that evidence.

When you have a **question, ambiguity, risk, or design decision** (not a hard failure above) that would normally warrant human input:

1. Append an entry to the active run log at `agent_context/projects/<PROJECT>/project_board/checkpoints/<ticket-id>/<run-id>.md`:

   ```
   ### [TICKET_ID] <Stage> — <short label>
   **Would have asked:** <exact question>
   **Assumption made:** <what you decided>
   **Confidence:** Low | Medium | High
   ```

2. Resolve with the most conservative, reasonable assumption.
3. Continue without waiting.

Steps 2–3 apply to **judgment ambiguity** only. If the underlying issue is a failed command or test, stop treating the step as succeeded; surface output per `workflow_enforcement_v1.md` (**Tool, script, and test failures**).

## Scoped log (subagents) and index (orchestrator)

**Subagents** (Spec, Test Designer, Test Breaker, Implementation, Python Reviewer, AC Gatekeeper, Learning, and all consultative agents):
- **Write to:** `agent_context/projects/<PROJECT>/project_board/checkpoints/<ticket-id>/<run-id>.md` — your ticket-scoped log only.
- **Read from:** `agent_context/projects/<PROJECT>/project_board/checkpoints/<ticket-id>/` — your ticket's directory only.
- Do **not** read or write `agent_context/projects/<PROJECT>/project_board/CHECKPOINTS.md`. Do **not** read other tickets' checkpoint directories.

**Orchestrator** (autopilot, ap-continue, c-continue, feature):
- Maintains `agent_context/projects/<PROJECT>/project_board/CHECKPOINTS.md` as a thin run-level index (run start/end + one-line pointer per ticket — never full body entries).
- Updates the index after each ticket completes — not inside subagent prompts.

`<ticket-id>`: short slug (e.g. `feat-add-org-filter`). `<run-id>`: timestamp + stage (e.g. `2026-06-16T10-00-00Z-spec.md`).

**Hard rule:** Subagents must never read or write `CHECKPOINTS.md`. Full checkpoint bodies (`### [...]`, `**Would have asked:**`, etc.) belong only in the ticket-scoped log.

## Per-stage resolution hints

- **Test Designer:** Resolve by writing the strictest defensible test allowed by the spec.
- **Test Breaker:** Resolve by writing a test that encodes the conservative assumption; mark with `# CHECKPOINT` in the test file.
- **Implementation:** Resolve by choosing the simplest implementation consistent with spec and tests.
- **AC Gatekeeper:** Resolve by holding Stage at `INTEGRATION` and escalating to the appropriate agent rather than marking `COMPLETE` with unverified ACs.
