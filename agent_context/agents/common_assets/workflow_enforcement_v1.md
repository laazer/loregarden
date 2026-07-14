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
- Use Loregarden MCP tools for workflow state, stage transitions, approvals, and artifacts
- Do **not** edit project_board ticket `WORKFLOW STATE` for stage cursor changes Loregarden owns
- MCP endpoint: `POST http://127.0.0.1:8000/mcp` (or `LOREGARDEN_MCP_URL` / `./scripts/mcp-server.sh`)

------------------------------------------------------------
TICKET AUTHORITY
------------------------------------------------------------

- The ticket file is the single source of truth.
- Located at:
  agent_context/projects/<project_name>/<folder>/<ticket>.md

Agents MUST:
- Read the ticket file before acting.
- Update ONLY:
    - WORKFLOW STATE
    - NEXT ACTION
- Increment Revision by +1.
- Update Last Updated By.
- Preserve all other content.

If the ticket file is malformed or missing:
- Escalate to Planner.

------------------------------------------------------------
STAGE ENUM (STRICT)
------------------------------------------------------------

Allowed values:

PLANNING
SPECIFICATION
TEST_DESIGN
TEST_BREAK
IMPLEMENTATION_BACKEND
IMPLEMENTATION_FRONTEND
IMPLEMENTATION_GENERALIST
STATIC_QA
INTEGRATION
DEPLOYMENT
BLOCKED
COMPLETE

No other values allowed.

------------------------------------------------------------
STAGE TRANSITION ENFORCEMENT
------------------------------------------------------------

Agents may only transition stages according to the approved transition matrix.

Invalid transitions must escalate to Planner.

No skipping stages.
No lateral jumps.
No silent corrections.

------------------------------------------------------------
FOLDER RULE
------------------------------------------------------------

00_backlog/ → Stage must be PLANNING  
01_active/ → Any stage except COMPLETE  
02_complete/ → Stage must be COMPLETE  

If Stage becomes COMPLETE:
- Ticket must move to 02_complete/.

------------------------------------------------------------
SCOPE ENFORCEMENT
------------------------------------------------------------

Agents may modify ONLY files within their ownership domain.

Cross-domain work requires explicit Planner assignment to Generalist.

Prefer module-level changes at all times.

------------------------------------------------------------
NO SILENT TERMINATION
------------------------------------------------------------

Every execution must end with a fully updated:

# WORKFLOW STATE
# NEXT ACTION

If blocked:
- Set Stage to BLOCKED
- Clearly explain Blocking Issues
- Route appropriately

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
- `status`: `pass` if this stage's work is complete and correct; `fail` or `needs_rework` if it is not (e.g. static QA found real violations, a reviewer rejected the diff, tests could not be made to pass); `blocked` if you cannot proceed at all (e.g. missing credentials, an ambiguous requirement only a human can resolve) — unlike `fail`/`needs_rework`, this halts the ticket for human review rather than rerouting for automatic rework.
- `confidence`: your honest confidence (0.0–1.0) that `status` is correct. Do not default to 1.0 — if you are uncertain, say so.
- `reroute_to_stage`: when `status` is `fail` or `needs_rework` and you know which upstream stage should redo the work (e.g. `implementation_backend`, `spec`), name its stage key. Use `null` if you don't know — the orchestrator will fall back to the workflow template's rework route, or the immediately preceding stage. Ignored when `status` is `blocked`.
- `reroute_context`: when rerouting, a specific, actionable description of what the target stage missed or must fix. This is delivered to that stage's agent as prior-stage feedback — write it for that reader, not for a human audit log. When `status` is `blocked`, use this field instead to explain the blocker for the human who picks this up.

If you emit no report, or a malformed one, the orchestrator falls back to today's exit-code-only behavior — but that means a stage that only *looks* successful (clean exit, buggy work) silently advances. Always emit the report.

------------------------------------------------------------
REVISION RULE
------------------------------------------------------------

Every state mutation increments Revision by +1.

No mutation → no revision change.

------------------------------------------------------------
GIT / VCS
------------------------------------------------------------

**Feature branch (Planner mandate):** The **Planner** names one **feature branch** per ticket in the plan (see `agent_context/agents/readme.md`). Implementation work is committed on that branch until the **merge into `master`** milestone runs, unless the ticket defers merge.

**Sync before starting a new ticket:** Before the Planner names or the orchestrator checks out a feature branch for a new ticket, fetch and pull the latest default branch (`git fetch origin && git checkout master && git pull`) so the new branch starts from current remote state — this matters most right after a prior ticket's PR has merged. If the pull surfaces conflicts, resolve them on the default branch **before** branching; do not carry conflicts onto the new feature branch.

**Orchestrator discipline:** After the Planner’s branch name is known, the **orchestrator** checks out that branch from up-to-date **`master`** (or the repo default branch) **before** Spec, test, or implementation edits. Ticket-driven code and tests must not accumulate **only** on **`master`** in lieu of that branch. If branching is impossible without losing work, log a checkpoint (scoped log + index pointer per `AGENTS.md` / autopilot **Hard rules — checkpoint audit trail**) and **stop** rather than bypassing this rule.

**Handoff commit:** When you finish your work and update the ticket (WORKFLOW STATE + NEXT ACTION) to pass the ticket to the next agent, you MUST commit all changes (code, tests, ticket file, and any other modified files) before completing your turn. Use a commit message that references the ticket (e.g. `<ticket_id>: <short description>`).

**Merge into the default integration branch (e.g. `master`):** After **Gatekeeper** approval, integration happens **per project policy**. Default for this repo: **open or update a pull request** and let a **human or CI** merge into **`master`** (or the documented default branch). The orchestrator **does not** `git merge` the feature branch into `master` locally and **does not** `git push` the integration branch **unless the human explicitly asks** for that workflow.

**Push to remote (orchestrator — explicit):** When publishing work, push **only the feature branch**, e.g. `git push -u origin <feature-branch-name>`. This makes the branch available for PR review. **Do not** run `git push origin master` (or `git push origin <default>`) as part of autonomous ticket completion unless the human has instructed otherwise.

**Completion commit and push:** When the ticket reaches COMPLETE and is moved to 02_complete/, the agent performing that transition MUST:
1. Commit all remaining changes on the **feature branch** with a message that references the ticket and indicates completion (the branch may still be ahead of `master` until the PR merges).
2. Push **that feature branch** to the remote (`git push -u origin <feature-branch>`), or log a checkpoint (scoped log + index pointer per `AGENTS.md`) if push is impossible (network, credentials, hooks).

No handoff or completion without the required Git steps (commits on the feature branch; push the feature branch, not the integration branch, unless overridden by human instruction).

------------------------------------------------------------
TESTING DISCIPLINE
------------------------------------------------------------

**Baseline before edits:** Any agent that runs tests must execute the relevant test command **once before making any code or test changes** to record which tests already fail. This first run is the pre-existing failure baseline. Document it in the scoped checkpoint log.

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

**Handoff requirement:** Before updating WORKFLOW STATE to hand off to the next agent, all in_progress todos for the current ticket must be moved to `completed` or `cancelled`. The `todo_validation_check` gate enforces this and will block the handoff if any `in_progress` todos referencing the ticket ID remain.

------------------------------------------------------------
END OF MODULE