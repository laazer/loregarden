---
description: Workflow Enforcement Module – global ticket, stage, and VCS workflow rules for all agents.
globs: []
alwaysApply: true
---
# WORKFLOW ENFORCEMENT MODULE

This module governs ticket-scoped execution behavior.

All agents must comply.

------------------------------------------------------------
LOREGARDEN CONTROL PLANE (MCP)
------------------------------------------------------------

When Loregarden orchestrates tickets (IDE, API, SQLite control plane):

- Read and follow `agent_context/agents/common_assets/loregarden_mcp_v1.md`
- Read and follow `agent_context/agents/common_assets/memory_protocol_v1.md` when persisting or searching memory, learnings, or blog posts
- Use Loregarden MCP tools for ticket reads, artifacts, approvals, and memory
- Tickets live in Loregarden's database, not in the repo. Do not search for a ticket file
- **Stage-run outcome** is the `<<<LOREGARDEN_STAGE_REPORT>>>` sentinel — not
  `loregarden_complete_stage` (orchestrator/autopilot only)

**Never write a markdown file to report your work.** No findings, summary, analysis,
sign-off, verification, spec, or stage-completion file — anywhere in the repo. Loregarden
reads none of them; they only get swept into an unrelated ticket's commit. When your role
says to produce a report or document findings and names no destination, the destination is
MCP: `loregarden_attach_artifact` for reports and test output, the stage report sentinel for
stage outcomes, `loregarden_append_checkpoint` for assumptions, `loregarden_append_learning`
for learnings, `loregarden_update_ticket` for spec and acceptance criteria. Short findings go
in your response text. Writing real source code and real test files remains your job — this
rule covers *reports about* the work.
- MCP endpoint: `POST http://127.0.0.1:8000/mcp` (or `LOREGARDEN_MCP_URL` / `./scripts/mcp-server.sh`)

------------------------------------------------------------
SCOPE ENFORCEMENT
------------------------------------------------------------

Agents may modify ONLY files within their ownership domain.

Work outside it is not yours to do and not a reason to stop. Do the part inside
your scope, then report `needs_rework` with a reroute to the agent that owns the
rest — see the stage report contract below. Loregarden already routes a denied
cross-scope write to that agent, so a handoff costs a stage and a refusal costs
a whole run.

Prefer module-level changes at all times.

------------------------------------------------------------
STAGE REPORT CONTRACT (REQUIRED — ALL AGENTS)
------------------------------------------------------------

The last thing in your response, after all other output, MUST be a single sentinel-delimited
JSON block reporting your outcome for this stage. Loregarden's orchestrator parses this to
decide whether to advance, and where to route on failure — do not rely on the human/orchestrator
inferring your outcome from prose or exit code alone.

```
<<<LOREGARDEN_STAGE_REPORT>>>
{"status": "pass|fail|needs_rework|blocked", "confidence": 0.0-1.0, "reroute_to_stage": "<stage_key>|null", "reroute_context": "<what the target stage needs to know it missed>"}
<<<END_STAGE_REPORT>>>
```

Field rules:
- `status`: `pass` if this stage's work is complete and correct; `fail` or `needs_rework` if it is not (e.g. static QA found real violations, a reviewer rejected the diff, tests could not be made to pass); `blocked` if you cannot proceed at all (e.g. missing credentials, an ambiguous requirement only a human can resolve) — unlike `fail`/`needs_rework`, this halts the ticket for human review rather than rerouting for automatic rework. Work that a **different agent** must do is **not** `blocked` — it is `needs_rework` with a reroute. In particular, if you are a scope-limited implementer (e.g. the frontend implementer, restricted to `client/**`) and the ticket needs changes in another agent's area (e.g. `server/**`), do the part inside your own scope, then report `needs_rework` and hand the rest off — do not report `blocked`. Loregarden already routes a cross-scope write denial to the agent that owns that path; reserve `blocked` for something no agent can advance without a human.
- **Reject only for an unmet acceptance criterion.** `fail` and `needs_rework` mean *this
  ticket's stated criteria are not satisfied yet*. They do not mean "I found something real".
  Before rejecting, name the acceptance criterion your finding makes false, and put it in
  `unmet_criteria`. If you cannot name one, the work in front of you is done and your finding
  is a **new ticket** — file it with `loregarden_create_ticket` and report `pass`, saying in
  your response what you filed and why it did not block.

  This is the difference between a review that converges and one that does not. A defect
  *family* — "the gate can be made to examine nothing", "the parser mishandles some inputs" —
  has as many instances as the input space has corners, and a reviewer asked "is there another
  instance?" will always find one. That question has no terminating answer, so it must not
  gate a ticket. Ship the invariant the criteria asked for, file the enumeration, and let the
  next ticket carry it. A real case: one ticket met all four of its criteria at its third
  round and was then rerouted three more times on genuine new findings, none of which any
  criterion covered.

- `confidence`: your honest confidence (0.0–1.0) that `status` is correct. Do not default to 1.0 — if you are uncertain, say so.
- `reroute_to_stage`: when `status` is `fail` or `needs_rework` and you know which upstream stage should redo the work, name its stage key **exactly as it appears in the "Valid `reroute_to_stage` values for this workflow" list in your run context** — do not guess or invent a plausible-sounding key, and do not use a stage's display name. A key that isn't in that list is discarded, and the rework falls back to the immediately preceding stage — which is rarely where you wanted it. Use `null` if you don't know or none applies: the orchestrator will then fall back to the workflow template's rework route, or the immediately preceding stage. Ignored when `status` is `blocked`.
- `reroute_context`: when rerouting, a specific, actionable description of what the target stage missed or must fix. This is delivered to that stage's agent as prior-stage feedback — write it for that reader, not for a human audit log. When `status` is `blocked`, use this field instead to explain the blocker for the human who picks this up.

If you emit no report, or a malformed one, the orchestrator **blocks the stage** (fail-closed).
A clean process exit alone never advances the workflow — every agent stage must emit this
block. Always emit the report.

**Output economy:** Tokens are the budget — every turn is paid for. Keep prose terse (no preamble or filler), return findings as lists or tables the orchestrator can parse, and route long reports to `loregarden_attach_artifact` rather than the response body. Never trade correctness, tests, or required evidence for brevity — cut filler, not substance.

------------------------------------------------------------
GIT / VCS
------------------------------------------------------------

**The orchestrator owns branching, committing and pushing.** Not you. It checks
out the ticket's branch before your stage runs, commits the working tree when
your stage finishes, and opens or updates the pull request according to the
workspace's git automation settings.

So: do not run `git commit`, `git push`, `git merge`, or `git checkout` as part
of finishing a stage. Leave your work in the tree. A stage that commits by hand
races the orchestrator's own commit and can strand half the change.

**`git stash` is forbidden** — see TESTING DISCIPLINE below for why, and for what
to do instead.

If you believe a git operation is genuinely required and cannot be left to the
orchestrator, say so in your stage report rather than doing it.

------------------------------------------------------------
TESTING DISCIPLINE
------------------------------------------------------------

**Baseline before edits:** Any agent that runs tests must execute the relevant test command **once before making any code or test changes** to record which tests already fail. This first run is the pre-existing failure baseline. Record it with `loregarden_append_checkpoint` — do not write a results file.

**Never use `git stash` to establish a baseline.** `git stash` is destructive, unsafe in worktrees (stashes are not branch-scoped and can be applied to the wrong tree), and unnecessary. The correct pattern is:
1. Run tests at session start (before any edits) → record failures as "pre-existing".
2. Make changes.
3. Run tests again → compare to step 1.

If you need to confirm that a failure predates your work, read the **prior agent's checkpoint log** — the documented RED count is the authoritative baseline. Do not stash your working tree.

------------------------------------------------------------
TODOS (Claude Code TodoWrite)
------------------------------------------------------------

**Prefix rule:** Every todo created during a ticket must include the ticket ID in its content so the `todo_validation_check` gate can scope it correctly.

- Correct: `[STRATOS-45] Implement InfraNode model`
- Incorrect: `Implement InfraNode model`

**Handoff requirement:** Before finishing a stage and handing off, all in_progress todos for the current ticket must be moved to `completed` or `cancelled`. The `todo_validation_check` gate enforces this and will block the handoff if any `in_progress` todos referencing the ticket ID remain.

------------------------------------------------------------
END OF MODULE
------------------------------------------------------------
