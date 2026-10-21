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

Your run is one headless turn: when the turn ends, the process ends. A background command
never notifies you afterwards, so never end your turn "waiting for" one. Run tests in the
foreground and only the ones your change reaches (`common_assets/test_selection_v1.md`).

```
<<<LOREGARDEN_STAGE_REPORT>>>
{"status": "pass|fail|needs_rework|blocked", "confidence": 0.0-1.0, "reroute_to_stage": "<stage_key>|null", "reroute_to_agent": "<agent_id>|null", "reroute_context": "<what the target stage needs to know it missed>", "unmet_criteria": ["<acceptance criterion this finding makes false>"], "blocked_kind": "harness|work|decision|human_action|tickets|null", "options": ["<choice a person can pick, when blocked_kind is decision>"], "waiting_on": ["<external id of a ticket this one waits for, when blocked_kind is tickets>"]}
<<<END_STAGE_REPORT>>>
```

Field rules:
- `status`: `pass` if this stage's work is complete and correct; `fail` or `needs_rework` if it is not (e.g. static QA found real violations, a reviewer rejected the diff, tests could not be made to pass); `blocked` if you cannot proceed at all (e.g. missing credentials, an ambiguous requirement only a human can resolve) — unlike `fail`/`needs_rework`, this halts the ticket for human review rather than rerouting for automatic rework. Work that a **different agent** must do is **not** `blocked` — it is `needs_rework` with a reroute. In particular, if you are a scope-limited implementer (e.g. the frontend implementer, restricted to `client/**`) and the ticket needs changes in another agent's area (e.g. `server/**`), do the part inside your own scope, then report `needs_rework` and hand the rest off — do not report `blocked`. Loregarden already routes a cross-scope write denial to the agent that owns that path; reserve `blocked` for something no agent can advance without a human.
- **Reject only for an unmet acceptance criterion.** `fail` and `needs_rework` mean *this
  ticket's stated criteria are not satisfied yet*. They do not mean "I found something real".
  Before rejecting, name the acceptance criterion your finding makes false, and put it in
  `unmet_criteria`. If you cannot name one, the work in front of you is done and your finding
  is a **new ticket** — file it with `loregarden_create_ticket` and report `pass`, saying in
  your response what you filed and why it did not block. If the new ticket cannot start until
  other work lands, pass those tickets in `depends_on` when you create it: a dependency written
  only in the description holds nothing, and the ticket is dispatched straight into the wall.
  A run may file three tickets; findings that are instances of one defect are one ticket.

  This is the difference between a review that converges and one that does not. A defect
  *family* — "the gate can be made to examine nothing", "the parser mishandles some inputs" —
  has as many instances as the input space has corners, and a reviewer asked "is there another
  instance?" will always find one. That question has no terminating answer, so it must not
  gate a ticket. Ship the invariant the criteria asked for, file the enumeration, and let the
  next ticket carry it. A real case: one ticket met all four of its criteria at its third
  round and was then rerouted three more times on genuine new findings, none of which any
  criterion covered.

- `unmet_criteria`: when `status` is `fail` or `needs_rework`, the acceptance criteria your
  finding makes false — the grounds for the rejection, not a restatement of it. Delivered to the
  target stage alongside `reroute_context`, so the agent redoing the work knows what "done" would
  mean. Omit or leave empty on `pass`. If you cannot fill this in, re-read the rule above: the
  work in front of you is finished and your finding is a new ticket.
- `confidence`: your honest confidence (0.0–1.0) that `status` is correct. Do not default to 1.0 — if you are uncertain, say so.
- `reroute_to_stage`: when `status` is `fail` or `needs_rework` and you know which upstream stage should redo the work, name its stage key **exactly as it appears in the "Valid `reroute_to_stage` values for this workflow" list in your run context** — do not guess or invent a plausible-sounding key, and do not use a stage's display name. A key that isn't in that list is discarded, and the rework falls back to the immediately preceding stage — which is rarely where you wanted it. Use `null` if you don't know or none applies: the orchestrator will then fall back to the workflow template's rework route, or the immediately preceding stage. Ignored when `status` is `blocked`.
- `reroute_to_agent`: when rejecting, and only when you know **which specialist** should redo
  the work (most often: you are a scope-limited implementer and this ticket is another
  implementer's work), name that agent exactly as listed under "Valid `reroute_to_agent`
  values" in your run context. Loregarden dispatches that agent the next time a stage that
  can run it comes up, once, ahead of its own keyword routing. Do not ask for an agent in
  `reroute_context` prose instead — nothing reads it there, and the same wrong agent is
  dispatched again. A name no stage in the rework can run is discarded with a note on the
  ticket. Use `null` when routing should choose as usual.
- `reroute_context`: when rerouting, a specific, actionable description of what the target stage missed or must fix. This is delivered to that stage's agent as prior-stage feedback — write it for that reader, not for a human audit log. When `status` is `blocked`, use this field instead to explain the blocker for the human who picks this up. Use `null` or `""` when you are not rerouting.
- `blocked_kind`: **required when `status` is `blocked`** — who can unblock it. `harness`: the
  environment or the control plane failed (a tool crashed, a dependency is missing), not the work.
  `work`: you stopped on something an agent can still do (a test you could not crack, a gap you
  could have assumed past) — say so honestly; a repair run will pick it up. `decision`: a choice
  only a person should make (a spec bar tripped, two valid approaches) — you MUST also give
  `options`, 2–4 short choices a person can pick in one click; the answer comes back to the
  ticket and the stage reruns without anyone asking. `human_action`: a person's hands are needed
  (credentials, hardware, an external account). `tickets`: this ticket cannot proceed until
  other tickets land (the code it builds on is not merged yet) — you MUST also give `waiting_on`,
  their external ids. The orchestrator links them as prerequisites and holds the ticket, with no
  repair turn and no person paged; the stage reruns by itself once they are done and landed.
  Do not use `work` for this: a repair agent cannot make another ticket land. A `blocked` report with no kind is treated as
  `work`, and the history says the agent did not say — earn the person's attention by naming it.
  **A `decision` is also the only way to get a person's go-ahead for an irreversible or outward
  action** (force-pushing or publishing a ref, opening or closing a pull request, re-cutting
  another ticket's branch): make each option the exact command. The pick comes back as the
  `Decision — <stage>` artifact, and **that pick is the authorization**: on the rerun, carry it
  out; do not ask again. `loregarden_request_approval` cannot grant it (it only opens stage
  gates), so a spec must not name it as the channel for one.
  **A spec does not put such an operation in a code ticket's acceptance criteria.** Criteria
  that require changing shared state rather than code (force-pushing or republishing a ref,
  opening or closing a pull request by hand, re-cutting another ticket's branch, rewriting other
  tickets' rows) go in a separate ticket: create it under the same parent with
  `loregarden_create_ticket`, set it `parked` with `loregarden_update_ticket` (a person owes it),
  link the two with `loregarden_link_relation`, and leave only code criteria here. Code that
  *implements* such an operation (a feature that opens PRs) is ordinary code and stays. An
  implementer cannot finish an operation criterion without stopping for a person every round;
  lg-milestone-that-782 ran four implement rounds on one before it was split out. This is
  checked: a passing spec whose criteria name such an operation (a force-push or
  force-update, an admin bypass, re-signing history, re-cutting worktrees onto a rebuilt
  branch, editing `landed_sha` through the tools) does not advance until a person chooses to
  split them out or keep them.
- `options`: only with `blocked_kind: decision`. Each is the answer itself ("Relax the bar to
  0.5", "Try approach B", "Accept and continue"), not a question.
- `waiting_on`: only with `blocked_kind: tickets`. External ids (`lg-milestone-that-1314`), one
  per ticket this one waits for. An id that does not resolve, or a ticket already done and
  landed, turns the report into an ordinary block that says so.
- **Nulls are fine.** `reroute_to_stage`, `reroute_context` and `unmet_criteria` may each be `null`, empty, or omitted entirely when they do not apply — all three spellings mean the same thing to the parser. `status` and `confidence` are the two fields that must carry a real value, since they are the verdict itself.

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
