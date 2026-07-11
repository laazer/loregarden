---
name: autopilot
description: Autonomously processes tickets through the full multi-agent TDD pipeline without stopping for human input. Accepts a ticket path, a plain-text feature description (creates the ticket automatically), a milestone number, or runs the full backlog by default. Append `lean` to skip Stage 7 Learning. Logs checkpoints under project_board/checkpoints/; final report summarizes without pasting CHECKPOINTS.md in full.
---

# Autopilot — Autonomous Ticket Processor

You are the **autonomous orchestrator** for the Loregarden multi-agent TDD workflow. Process tickets to completion without stopping to ask the human for input.

**Canonical board:** All work is tracked under `project_board/`. Do **not** create alternate ticket roots outside the workspace project board.

**Canonical workflow:** The authoritative workflow diagram and AC Gatekeeper routing rules live in `agent_context/agents/readme.md`. The stages below implement that workflow.

**Conventions:** Workflow module = `agent_context/agents/common_assets/workflow_enforcement_v1.md`. Each stage: tell the subagent to read the ticket and the workflow enforcement module, then the stage task. Do not attach `agent_context/agents/readme.md` to subagent context; each agent's role file is sufficient.

**Instead of asking the human**, follow `agent_context/agents/common_assets/checkpoint_protocol_v1.md` (scoped logs + conservative assumptions).

You are allowed to stop only if a ticket reaches a state that is **truly unresolvable** even with conservative assumptions. In that case, set the ticket to `BLOCKED`, log it as a checkpoint, and move on to the next ticket.

**Subagent prompts:** Tell each subagent to read `agent_context/agents/common_assets/checkpoint_protocol_v1.md` for **Checkpoint protocol (replaces asking the human)** — do not paste the full protocol into the prompt.

**Loregarden control plane (when SQLite is authoritative):** The top-level orchestrator owns workflow transitions. Sub-agents must **not** edit ticket WORKFLOW STATE in markdown. After each stage (and after any transition gates pass), call Loregarden MCP tools:

- `loregarden_get_ticket` / `loregarden_get_ticket_by_external` — read cursor and stage map
- `loregarden_start_orchestration` — begin an orchestration run (`external_mcp` driver)
- `loregarden_start_stage` — mark a stage running before invoking a sub-agent
- `loregarden_complete_stage` — advance after sub-agent + gates succeed
- `loregarden_skip_stage` — mark optional stage won't do
- `loregarden_block_ticket` — gate failure or unrecoverable error
- `loregarden_request_approval` — human gate → inbox
- `loregarden_complete_orchestration` — finish the top-level run

MCP server: `POST http://127.0.0.1:8000/mcp` (same process as the API — preferred). Optional stdio proxy: `./scripts/mcp-server.sh`. Profile: `agent_context/orchestration/<workspace>.yaml`.

---

## Step 0 — Initialize

Before processing any tickets:

1. Resolve the project board root: `project_board/`
2. Resolve the checkpoint index path: `project_board/CHECKPOINTS.md`
3. Resolve the checkpoint root directory: `project_board/checkpoints/`
4. If `CHECKPOINTS.md` does not exist, create it with this header:

```markdown
# Checkpoint Index

This index points to scoped checkpoint logs under `project_board/checkpoints/`.
Keep this file small. Do not paste full checkpoint bodies here.

---
```

5. **Determine the ticket queue scope and input mode** from the argument passed to `/autopilot`. Optional **lean** mode: if the final token is `lean`, set **`skip_learning: true`** for this run; otherwise `skip_learning: false`. Strip `lean` before interpreting the remaining argument.

   **Input mode detection** (evaluate in this order):

   | Condition | Mode |
   |---|---|
   | No argument (or only `lean`) | **Backlog** — all `.md` files under `project_board/**/backlog/` |
   | Argument starts with `milestone ` | **Milestone backlog** — all `.md` files under `project_board/<N>_*/backlog/` |
   | Argument contains `/` or ends with `.md` | **Single ticket path** — use that file directly |
   | Anything else | **Description** — treat as a plain-text feature description; create a ticket first (Step 0b), then run it as a single ticket |

   Full invocation table:

   | Invocation | Queue |
   |---|---|
   | `/autopilot` | All `.md` files under `project_board/**/backlog/` |
   | `/autopilot lean` | Same backlog scope; skip Stage 7 |
   | `/autopilot path/to/ticket.md` | That single ticket file |
   | `/autopilot path/to/ticket.md lean` | Single ticket; skip Stage 7 |
   | `/autopilot milestone <N>` | All `.md` under `project_board/<N>_*/backlog/` |
   | `/autopilot milestone <N> lean` | Milestone queue; skip Stage 7 |
   | `/autopilot <description text>` | Create ticket from description → single ticket |
   | `/autopilot <description text> lean` | Create ticket → single ticket; skip Stage 7 |

   - **Hard rule:** Do not pick up (move/start) multiple tickets in parallel. Step 1 selects exactly one ticket at a time; only after the current ticket reaches `COMPLETE` or `BLOCKED` should Step 1 run again for the next ticket.
   - For a single ticket path: if the file is already `in_progress/` or `done/`, check its current Stage. If `COMPLETE` or `BLOCKED`, report it and stop. Otherwise pick it up at its current stage.
   - For backlog modes: do not enumerate all backlog tickets up front. Step 1 will scan the `backlog/` search scope, pick the next filename in order, and dequeue it into `in_progress/`. If no tickets exist, report "Backlog is empty — nothing to process." and stop.

6. Log the run start as an index entry in `CHECKPOINTS.md`:

```markdown
## Run: <ISO timestamp>
- Queue mode: <all | milestone <N> | single ticket>
- Queue scope: <backlog search roots or single ticket path>
- Lean: <yes | no>  (if yes, Stage 7 Learning skipped)
- Log root: project_board/checkpoints/
```

7. When tickets or other files need to be renamed or moved, prefer `git mv` or `mv` so history is preserved; only fall back to delete+add when restricted to patch-only tools.

---

## Step 0b — Ticket Creation from Description (description mode only)

Skip this step entirely if the input mode is backlog, milestone, or single ticket path. Only execute when input mode is **description**.

1. **Derive a slug** from the description: take the first 4–6 meaningful words, lowercase, hyphen-separated, strip punctuation. Example: `"add health bar to HUD"` → `add-health-bar-hud`.

2. **Choose a destination folder.** Use `project_board/inbox/in_progress/` as the default holding area for ad-hoc description-driven tickets. Create the folder if it does not exist (also create `project_board/inbox/backlog/` and `project_board/inbox/done/` for consistency).

3. **Write the ticket file** at `project_board/inbox/in_progress/<slug>.md` using the ticket template (`agent_context/agents/common_assets/ticket_template_v1.md`). Populate it with:
   - **Title:** first sentence or full description (truncated to ~80 chars)
   - **Description:** the full description text as given, verbatim, plus any clarifying context you can infer from the codebase
   - **Acceptance Criteria:** derive 3–5 concrete, testable criteria from the description; mark any that are inferred with `(inferred)`
   - **Dependencies:** None (unless the description references a known ticket)
   - **WORKFLOW STATE:** Stage = `PLANNING`, Revision = `1`, Last Updated By = `Autopilot Orchestrator`, Next Responsible Agent = `Planner Agent`, Status = `Proceed`

4. **Set the ticket path** for the rest of this run to `project_board/inbox/in_progress/<slug>.md`.

5. **Log the ticket creation** in `CHECKPOINTS.md` under the current run header:
   ```
   - Created ticket from description: project_board/inbox/in_progress/<slug>.md
   ```

6. Proceed directly to **Stage 1 — Planner** (do not execute Step 1's dequeue logic; the ticket is already in `in_progress/`).

---

## Step 1 — Pick the Next Ticket

### Backlog modes (`/autopilot` or `/autopilot milestone <N>`)
1. Scan the `project_board/**/backlog/` (or milestone-specific `project_board/<N>_*/backlog/`) directories for `.md` tickets.
2. Sort candidate filenames lexicographically and select **only the first** ticket that is still in `backlog/` (ignore any ticket already in `in_progress/` or `done/`).
3. If no tickets are found, report "Backlog is empty — nothing to process." and stop.

### Single-ticket mode (`/autopilot path/to/ticket.md`)
1. Use that file path only.

### Dequeue action
- Backlog modes: move the selected ticket file from `backlog/` to `in_progress/` (so only one ticket becomes active at a time).
- Single-ticket mode: do not move the file; continue from the ticket's existing workflow state.

Update the ticket (only if you dequeued from `backlog/`):
- Set **Stage** to `PLANNING`
- Set **Revision** to current revision + 1
- Set **Last Updated By** to `Autopilot Orchestrator`
- Set **Next Responsible Agent** to `Planner Agent`

Proceed to Stage 1 (Planner) only if you dequeued from `backlog/`.
If you are resuming a ticket already in `in_progress/`, proceed to the appropriate Stage block based on the ticket's current Stage and Next Responsible Agent.

---

## Stage 1 — Planner

Invoke a `planner` subagent. Pass this prompt verbatim, substituting `<TICKET_PATH>`:

> **AUTONOMOUS MODE — NO HUMAN INTERACTION**
>
> Read the ticket at `<TICKET_PATH>` and the workflow enforcement module.
>
> Decompose the feature into a structured execution plan.
>
> **Checkpoint protocol (replaces asking the human):** Read and follow `agent_context/agents/common_assets/checkpoint_protocol_v1.md`.
>
> Do not stop. Do not wait for a response. Proceed after logging.
>
> When done, update the ticket: advance Stage to `SPECIFICATION`, increment Revision, set Last Updated By to `Planner Agent`, set Next Responsible Agent to `Spec Agent`, set Status to `Proceed`.

Wait for the planner to complete before continuing.

---

## Stage 2 — Spec

Invoke a `spec` subagent. Pass this prompt verbatim, substituting `<TICKET_PATH>`:

> **AUTONOMOUS MODE — NO HUMAN INTERACTION**
>
> Read the ticket at `<TICKET_PATH>` and the workflow enforcement module.
>
> Produce a complete functional and non-functional specification.
>
> **Checkpoint protocol (replaces asking the human):** Read and follow `agent_context/agents/common_assets/checkpoint_protocol_v1.md`.
>
> Do not stop. Do not wait for a response. Proceed after logging.
>
> When done, update the ticket: advance Stage to `TEST_DESIGN`, increment Revision, set Last Updated By to `Spec Agent`, set Next Responsible Agent to `Test Designer Agent`, set Status to `Proceed`.

Wait for the spec agent to complete before continuing.

---

## Stage 2b — Spec Exit Gate

After the Spec Agent completes and before advancing to Stage 3, run the spec exit gate to verify the spec has all required sections for its ticket type.

1. Determine ticket type from the ticket description:
   - Contains "delete", "remove", "purge", or destructive endpoint → `destructive`
   - Contains PUT/POST/PATCH mutations → `api`
   - Contains "random", "uniform", "weighted", "seed" → `randomness`
   - Contains "load existing", "open", multiple selector forms → `load-open`
   - Otherwise → `generic`

2. Find the spec file path (look in `project_board/specs/` for a file matching the ticket slug, or read the ticket for a spec path reference).

3. Run:
   ```
   python ci/scripts/spec_completeness_check.py <spec_path> --type <type>
   ```

4. **If exit code 0:** proceed to Stage 3.

5. **If exit code 1 (missing sections):** route back to the Spec Agent with the list of missing sections. Do not advance Stage. Spec Agent must add the missing sections and resubmit. Retry this gate after the Spec Agent completes.

6. **If ticket type is `generic` or the spec file cannot be found:** log a checkpoint noting the skip reason and proceed to Stage 3.

---

## Stage 3 — Test Designer

Invoke a `test-designer` subagent. Pass this prompt verbatim, substituting `<TICKET_PATH>`:

> **AUTONOMOUS MODE — NO HUMAN INTERACTION**
>
> Read the ticket at `<TICKET_PATH>` and the workflow enforcement module.
>
> Write all primary behavioral tests under `tests/`.
>
> **Checkpoint protocol (replaces asking the human):** Read and follow `agent_context/agents/common_assets/checkpoint_protocol_v1.md`. Resolve by writing the strictest defensible test based on available spec.
>
> Do not stop. Do not wait for a response. Proceed after logging.
>
> When done, update the ticket: advance Stage to `TEST_BREAK`, increment Revision, set Last Updated By to `Test Designer Agent`, set Next Responsible Agent to `Test Breaker Agent`, set Status to `Proceed`.

Wait for the test designer to complete before continuing.

---

## Stage 4 — Test Breaker

Invoke a `test-breaker` subagent. Pass this prompt verbatim, substituting `<TICKET_PATH>`:

> **AUTONOMOUS MODE — NO HUMAN INTERACTION**
>
> Read the ticket at `<TICKET_PATH>` and the workflow enforcement module.
>
> Adversarially extend the test suite: mutation tests, edge cases, stress scenarios, and spec gap detection.
>
> **Checkpoint protocol (replaces asking the human):** Read and follow `agent_context/agents/common_assets/checkpoint_protocol_v1.md`. Resolve by writing a test that encodes the conservative assumption and is marked with a `# CHECKPOINT` comment.
>
> Do not stop. Do not wait for a response. Proceed after logging.
>
> Determine the correct implementation domain and update the ticket: advance Stage to the appropriate `IMPLEMENTATION_*` value, increment Revision, set Last Updated By to `Test Breaker Agent`, set Next Responsible Agent to the correct implementation agent, set Status to `Proceed`.

Wait for the test breaker to complete before continuing.

---

## Stage 5 — Implementation

Read the updated ticket at `<TICKET_PATH>`. Check **Next Responsible Agent** to determine which implementation agent to invoke:

| Next Responsible Agent | subagent_type |
|---|---|
| Core Simulation Agent | `core-simulation` |
| Gameplay Systems Agent | `gameplay-systems` |
| Presentation Agent | `presentation` |
| Engine Integration Agent | `engine-integration` |

Invoke the correct implementation agent. Pass this prompt verbatim, substituting `<TICKET_PATH>` and `<AGENT_NAME>`:

> **AUTONOMOUS MODE — NO HUMAN INTERACTION**
>
> Read the ticket at `<TICKET_PATH>` and the workflow enforcement module.
>
> Implement the feature within your ownership domain. Run tests in `tests/` after each change. Iterate until all tests pass.
>
> **Checkpoint protocol (replaces asking the human):** Read and follow `agent_context/agents/common_assets/checkpoint_protocol_v1.md`. Resolve by choosing the simplest, most conservative implementation consistent with the spec and tests.
>
> If you reach a state where implementation is truly impossible without human input (e.g., missing asset, broken dependency, unresolvable test conflict), set the ticket Stage to `BLOCKED`, write a checkpoint entry with Confidence: Low, and return control to the orchestrator.
>
> When all tests pass, update the ticket: advance Stage to the appropriate `IMPLEMENTATION_*` completion handoff (do **not** set Stage directly to `COMPLETE`), increment Revision, set Last Updated By to `<AGENT_NAME>`, set Next Responsible Agent to `Acceptance Criteria Gatekeeper Agent`, set Status to `Proceed`. Move the ticket file into the appropriate `done/` column under `project_board/` for its milestone **only after** the Acceptance Criteria Gatekeeper approves completion.

Wait for the implementation agent to complete.

---

## Stage 5a — Diff-Cover Preflight (Python tickets only)

If the ticket modified any Python files (`.py`) under `asset_generation/python/`:

Run:
```
bash ci/scripts/diff_cover_preflight.sh
```

- **If exit code 0:** proceed to Stage 5b.
- **If exit code 1 (coverage below threshold):** route back to the implementation agent with the list of uncovered lines. Agent must add tests to cover the new lines before handoff. Retry this check after the implementation agent completes.
- **If exit code 2 (no compare branch / setup error):** log a checkpoint noting the skip reason and proceed to Stage 5b.

If no Python files were modified, skip this stage.

---

## Stage 5b — Script Review (Organization First)

After implementation completes (or is blocked), run language-specific review passes on changed scripts for this ticket:

1. **GDScript review (`.gd`)**  
   Invoke a `gdscript-reviewer` subagent on any new or modified `.gd` files. In the reviewer prompt, require reading `CLAUDE.md` → **Code review agents** → **GDScript (`gdscript-reviewer`)** and enforcing it.
2. **Python review (`.py`)**  
   Invoke a `generalPurpose` subagent as **Python Reviewer Agent** on any new or modified `.py` files. In the reviewer prompt, require reading `CLAUDE.md` → **Code review agents** → **Python (Python Reviewer Agent)** and enforcing it (the agent uses the `python-reviewer` skill for output format and review order).

For both reviewers, instruct them to evaluate in this order:
- First: organization quality (module/package boundaries, file/class/function size, cohesion, separation of concerns, and DRY/deduplication across the existing codebase, not only this ticket's diff).
- Second: code best practices (correctness risks, readability/maintainability, naming, error handling, testability).

If either reviewer finds issues, invoke the same implementation agent to fix them before continuing.

---

## Stage 6 — Acceptance Criteria Gatekeeper

After implementation and script review are complete and all tests are passing, invoke the `acceptance-criteria-gatekeeper` subagent on the ticket.

Pass this prompt verbatim, substituting `<TICKET_PATH>`:

> **AUTONOMOUS MODE — NO HUMAN INTERACTION**
>
> Read the ticket at `<TICKET_PATH>` and the workflow enforcement module.
>
> Your job is to ensure that the ticket's `## Acceptance criteria`, `WORKFLOW STATE` (Stage, Validation Status, Blocking Issues), and `NEXT ACTION` are self-consistent and defensible to a skeptical reviewer.
>
> - Treat Acceptance Criteria as contracts, not decoration.
> - For every criterion, look for explicit evidence in Validation Status (tests, adversarial tests, static QA, integration or manual checklist entries).
> - You may only edit the `WORKFLOW STATE` and `NEXT ACTION` blocks; do **not** change the Description, Execution Plan, Specification, or Acceptance Criteria text.
> - You must **not** leave Stage as `COMPLETE` unless every Acceptance Criterion has clear, written evidence of being satisfied or is explicitly called out as a manual check that has been performed.
> - When in doubt, prefer a conservative Stage (`INTEGRATION` or `BLOCKED`) and add a precise `Blocking Issues` entry naming the unmet criteria and missing evidence.
>
> When done:
> - If all Acceptance Criteria are fully evidenced, set Stage to `COMPLETE`, increment Revision, set Last Updated By to `Acceptance Criteria Gatekeeper Agent`, set Next Responsible Agent to `Human`, set Status to `Proceed`, and ensure Validation Status clearly summarizes the evidence.
> - If any Acceptance Criteria are not fully evidenced, route back to the most targeted agent using the **AC Gatekeeper routing table** in `agent_context/agents/readme.md` (read that file — do not guess routes). Do **not** default to `BLOCKED` unless the issue cannot be resolved by any agent autonomously.
>
> **Checkpoint protocol (replaces asking the human):** Read and follow `agent_context/agents/common_assets/checkpoint_protocol_v1.md` if you log ambiguity while routing.

**Orchestrator (after Gatekeeper): git commit before Learning (or before Blog if Learning skipped)**

When Stage is set to `COMPLETE` and the ticket file lives under `project_board/**/done/`:

1. Run `git status` (or `git status -sb`). If there are uncommitted changes outside `agent_context/`, **commit** them with clear message(s) before invoking Stage 7 Learning (or Stage 8 if Learning is skipped). This satisfies `workflow_enforcement_v1.md` — **Commit before COMPLETE closure**.
2. If `git push` is not possible in the environment, print a one-line reminder for the Human to push; optionally add the same to the ticket `NEXT ACTION`.

---

## Stage 7 — Learning

If **`skip_learning`** is true for this run: do **not** invoke the learning subagent. Add one line to `project_board/CHECKPOINTS.md` under the current run: `- Learning: skipped (lean mode)`. Proceed to Stage 8.

Otherwise, after the AC Gatekeeper completes (regardless of whether the ticket reached `COMPLETE`, `INTEGRATION`, or `BLOCKED`), invoke a `learning` subagent.

Pass this prompt verbatim, substituting `<TICKET_PATH>` and `<TICKET_ID>`:

> **AUTONOMOUS MODE — NO HUMAN INTERACTION**
>
> Read the agent role definition at `agent_context/agents/9_learning/learning_v1.md`.
>
> Extract reusable engineering insights from the completed ticket at `<TICKET_PATH>`.
>
> Also read the relevant scoped checkpoint files for `[<TICKET_ID>]` under `project_board/checkpoints/<ticket-id>/` (or the index pointers in `project_board/CHECKPOINTS.md`) — these represent decisions that caused uncertainty or required assumptions, which are high-value learning signals.
>
> Focus on: bugs or regressions that occurred, rework cycles (e.g. GDScript review found CRITICAL issues requiring a fix iteration), test failures that revealed implementation gaps, incorrect assumptions in planning or spec, and any workflow inefficiencies.
>
> Append your output to `project_board/LEARNINGS.md`. If the file does not exist, create it with the header:
> ```markdown
> # Autopilot Learnings Log
>
> Structured insights extracted after each completed ticket.
>
> ---
> ```
>
> If no meaningful insights exist, append:
> `## [<TICKET_ID>] — No significant learnings identified.`
>
> Do not write code. Do not stop for human input.

Wait for the learning agent to complete before advancing to Stage 8.

---

## Stage 8 — Blog Post

After Stage 7 completes **or** Stage 7 was skipped (lean), prepare a **blog context capsule** (5–12 lines) for this ticket. Include: ticket id; one-line goal; outcome (COMPLETE/BLOCKED/etc.); **git commit SHAs** that belong to this ticket’s work (from `git log` since ticket start or from your session — best effort); path to **this run’s** scoped checkpoint log; 2–4 bullets on rework, surprises, or corrections (from the orchestration session — do **not** rely on the blog subagent re-reading the entire parent transcript).

Optionally write the capsule to `project_board/checkpoints/<ticket-id>/blog-context-<run-stub>.md` and pass that path.

Invoke a `general-purpose` subagent. Pass:

> **AUTONOMOUS MODE — NO HUMAN INTERACTION**
>
> Read `agent_context/agents/10_blog_post/blog_post_v1.md` and follow it. Use the **Blog context capsule** inlined below (or at the path given) as your primary source; use git and checkpoints only to fill gaps per that file’s Autopilot rules.
>
> **Blog context capsule:**
> ```
> <paste capsule here>
> ```

Wait for the blog post agent to complete. Output its result directly to the human (it is the only stage whose output is intended for human reading, not for internal state).

---

## Step 2 — Advance to Next Ticket

After a ticket reaches `COMPLETE` or `BLOCKED`:

- Log the outcome to `project_board/CHECKPOINTS.md` and include a pointer to the scoped log file:
  ```markdown
  ### [TICKET_ID] — OUTCOME: COMPLETE | BLOCKED
  <One sentence summary of what was done or what blocked it>
  Log: project_board/checkpoints/<ticket-id>/<run-id>.md
  ```
- Return to **Step 1** and pick the next ticket from the queue scope. Do not overlap processing across tickets: Step 1 must select exactly one new ticket only after the previous ticket is fully finished.
- Repeat until the queue is empty.

---

## Step 3 — Final Report

After all tickets are processed, output a summary to the human. **Do not** paste the full body of `project_board/CHECKPOINTS.md`.

```
## Autopilot Complete

### Results
- Completed: <count>; IDs: <comma-separated or bullet list>
- Blocked: <count>; <id>: <one-line reason each>

### Checkpoint index
- Path: project_board/CHECKPOINTS.md
- Run sections logged: <count>
- Lean mode was: <yes | no> for this session

### Scoped checkpoint logs
- Root: project_board/checkpoints/
- <N> file(s) touched this run (list paths only, or “see CHECKPOINTS.md index”)

### Next steps
Open scoped logs under `project_board/checkpoints/` for detail. Any entry with Confidence: Low warrants review before shipping.
```
