# Pipeline Stage Definitions v1

Authoritative stage definitions for the Loregarden multi-agent TDD workflow. All orchestrator skills (autopilot, ap-continue, c-continue, feature) reference this file instead of duplicating stage handlers.

**How to use:** Orchestrators read this file during initialization. Execute each stage in order, substituting `<TICKET_PATH>`, `<TICKET_ID>`, and `<AGENT_NAME>` at runtime.

**Transition gates:** After each stage, run:
```bash
python ci/scripts/run_workflow_transition_gates.py --ticket-id <TICKET_ID> --transition <TRANSITION_NAME>
```
Exit 1 → set ticket `BLOCKED`, paste verbatim stderr/stdout into scoped checkpoint; do not advance.

**Background vs. blocking (Stages 7–8):** In autopilot, Stages 7 and 8 fire in the background after the per-ticket completion report; the orchestrator proceeds to the next ticket immediately. In ap-continue, c-continue, and feature, Stages 7 and 8 block before the final report.

**Stage reports (routing signal):** Every stage/reviewer sub-agent ends its response with the `<<<LOREGARDEN_STAGE_REPORT>>>` JSON block required by `agent_context/agents/common_assets/workflow_enforcement_v1.md`. When a stage's own instructions below leave the reject/reroute call to your judgment (e.g. "route back to X", "re-invoke the implementation agent"), read that block first and prefer it over your own reading of prose: pass `outcome: "reject"`, `next_stage_key: <reroute_to_stage>`, and `blocking_issues: <reroute_context>` to `loregarden_complete_stage` when `status` is `fail` or `needs_rework`. Only fall back to your own judgment if the block is missing or malformed.

---

## Stage 1 — Planner

Invoke a `planner` subagent. Pass this prompt verbatim:

> **AUTONOMOUS MODE — NO HUMAN INTERACTION**
>
> Read the ticket at `<TICKET_PATH>` and the workflow enforcement module at `agent_context/agents/common_assets/workflow_enforcement_v1.md`.
>
> Decompose the feature into a structured execution plan.
>
> **Checkpoint protocol (replaces asking the human):** Read and follow `agent_context/agents/common_assets/checkpoint_protocol_v1.md`.
>
> When done, update the ticket: advance Stage to `SPECIFICATION`, increment Revision, set Last Updated By to `Planner Agent`, set Next Responsible Agent to `Spec Agent`, set Status to `Proceed`.

**Transition gate:** `planner_to_spec`

---

## Stage 1.5 — Domain Consultation (conditional)

After Stage 1 completes and before the Human Design Gate, check whether the ticket warrants consultative agents.

**Domain routing — invoke when condition matches:**

| Condition | Agent | Role file |
|---|---|---|
| Unfamiliar subsystem or novel approach needing evidence before spec | Research Librarian | `agent_context/agents/misc_agents/research_librarian_v1.md` |

> Consultants are per-project. This is Loregarden's table; each workspace resolves
> `agent_context/` from its own repo root, so a workspace with domain specialists (e.g.
> Blobert's Godot and Blender consultants) declares them in its own copy of this file. Only
> list a role file that exists in *this* repo — a row pointing at a missing file sends the
> agent hunting for it.

If multiple conditions match, **invoke all matching consultants in parallel** — they each read the same ticket independently and produce advisory output.

**Prompt for each consultant** (substitute `<AGENT_ROLE_FILE>`):

> **CONSULTATION MODE — NO IMPLEMENTATION**
>
> Read your role definition at `<AGENT_ROLE_FILE>`. Read the ticket with `loregarden_get_ticket`
> — there is no ticket file.
>
> Provide validated, source-backed guidance relevant to this ticket. Cite sources. Apply your Analysis Framework to every recommendation.
>
> Do NOT write implementation code or tests. Do NOT modify the ticket.
>
> Return a structured consultation report the Spec Agent can use as input. Return it in your
> response or via `loregarden_attach_artifact` — never as a markdown file.

After all consultants complete, record one-line summaries with `loregarden_append_checkpoint` — never by writing a checkpoint file: `Consultation [Agent Name]: <key finding or "no blocking concerns">`.

If no condition matches, skip this stage entirely.

---

## Human Design Gate (conditional)

After Stage 1.5 (or Stage 1 if no consultation ran) and before Stage 2.

**When to trigger:** The ticket introduces any of:
- A new gameplay mechanic or player-facing interaction system
- A new elemental combination, fusion attack, or status effect
- A change to how the player experiences combat or movement

**When to skip:** Bug fixes, refactors, asset pipeline changes, backend/API changes, or test-only changes.

**Action when triggered:** Pause and surface to the human:
1. Ticket ID and one-line goal
2. The planner's Execution Plan section (from the ticket)
3. Consultation report summaries (if Stage 1.5 ran)
4. Prompt: "Confirm design direction or redirect before Spec runs."

**Wait for explicit human confirmation before invoking Stage 2.**

---

## Stage 2 — Spec

Invoke a `spec` subagent. Pass this prompt verbatim:

> **AUTONOMOUS MODE — NO HUMAN INTERACTION**
>
> Read the ticket at `<TICKET_PATH>` and the workflow enforcement module.
>
> Produce a complete functional and non-functional specification.
>
> **Checkpoint protocol:** Read and follow `agent_context/agents/common_assets/checkpoint_protocol_v1.md`.
>
> When done, update the ticket: advance Stage to `TEST_DESIGN`, increment Revision, set Last Updated By to `Spec Agent`, set Next Responsible Agent to `Test Designer Agent`, set Status to `Proceed`.

---

## Stage 2b — Spec Exit Gate

After the Spec Agent completes, verify the spec has all required sections.

1. Determine ticket type:
   - Contains "delete", "remove", "purge", or destructive endpoint → `destructive`
   - Contains PUT/POST/PATCH mutations → `api`
   - Contains "random", "uniform", "weighted", "seed" → `randomness`
   - Contains "load existing", "open", multiple selector forms → `load-open`
   - Otherwise → `generic`

2. Find the spec file path (check `project_board/specs/` for a file matching the ticket slug, or read the ticket for a spec path reference).

3. Run:
   ```bash
   python ci/scripts/spec_completeness_check.py <spec_path> --type <type>
   ```

4. **Exit 0:** proceed to Stage 3.
5. **Exit 1 (missing sections):** route back to Spec Agent with the list of missing sections. Retry after Spec Agent completes.
6. **Generic type or spec file not found:** log a checkpoint with the exact skip reason (path tried, verbatim script output if run). Proceed to Stage 3 only after that evidence exists.

**Transition gate:** `spec_to_test_design`

---

## Stage 3 — Test Designer

Invoke a `test-designer` subagent. Pass this prompt verbatim:

> **AUTONOMOUS MODE — NO HUMAN INTERACTION**
>
> Read the ticket at `<TICKET_PATH>` and the workflow enforcement module.
>
> Write all primary behavioral tests under `tests/`.
>
> Hard test-quality rules:
> - Tests must validate executable runtime behavior, not prose.
> - Do NOT create tests that assert ticket/spec/checkpoint markdown text.
> - Do NOT assert logging text unless logging behavior is explicitly required by the spec as an observable contract.
> - Name new test files by subsystem + behavior (e.g. `test_acid_weak_point.gd`); do NOT put milestone or ticket numbers in filenames. Put traceability in the module docstring.
> - Prefer `unittest.mock` for doubles; use `monkeypatch` only for env/`sys.modules`/settings singletons.
>
> **Checkpoint protocol:** Read and follow `agent_context/agents/common_assets/checkpoint_protocol_v1.md`. Resolve by writing the strictest defensible test allowed by the spec.
>
> When done, update the ticket: advance Stage to `TEST_BREAK`, increment Revision, set Last Updated By to `Test Designer Agent`, set Next Responsible Agent to `Test Breaker Agent`, set Status to `Proceed`.

**Transition gate:** `test_design_to_test_break`

---

## Stage 4 — Test Breaker

Invoke a `test-breaker` subagent. Pass this prompt verbatim:

> **AUTONOMOUS MODE — NO HUMAN INTERACTION**
>
> Read the ticket at `<TICKET_PATH>` and the workflow enforcement module.
>
> Adversarially extend the test suite: mutation tests, edge cases, stress scenarios, and spec gap detection.
>
> Hard test-quality rules:
> - Extend only executable behavior tests; do not add markdown/prose assertion tests.
> - Every adversarial test must target a real runtime seam capable of catching a code regression.
> - Do not add log-message assertions unless the spec defines logging semantics as required behavior.
> - New/changed test files: behavior-oriented names only (no ticket/milestone id in filename). Prefer mocks over `monkeypatch` per AGENTS.md.
>
> **Checkpoint protocol:** Read and follow `agent_context/agents/common_assets/checkpoint_protocol_v1.md`. Resolve by writing a test that encodes the conservative assumption, marked with a `# CHECKPOINT` comment.
>
> Determine the correct implementation domain and update the ticket: advance Stage to the appropriate `IMPLEMENTATION_*` value, increment Revision, set Last Updated By to `Test Breaker Agent`, set Next Responsible Agent to the correct implementation agent, set Status to `Proceed`.

**Transition gate:** `test_break_to_implementation`

---

## Stage 5 — Implementation

Read the ticket's **Next Responsible Agent** field to select the correct agent:

| Next Responsible Agent | subagent_type |
|---|---|
| Core Simulation Agent | `core-simulation` |
| Gameplay Systems Agent | `gameplay-systems` |
| Presentation Agent | `presentation` |
| Engine Integration Agent | `engine-integration` |
| Implementation Frontend Agent | `generalPurpose` (read `agent_context/agents/misc_agents/implementation_frontend_v1.md` + `asset_generation/web/frontend/AGENTS.md`) |

Invoke the correct agent. Pass this prompt verbatim:

> **AUTONOMOUS MODE — NO HUMAN INTERACTION**
>
> Read the ticket at `<TICKET_PATH>` and the workflow enforcement module.
>
> Implement the feature within your ownership domain.
>
> **Test runs during implementation — run only this ticket's test files, not the full suite:**
> - Godot: `timeout 300 godot --headless -s tests/run_tests.gd -- <test_file_1> <test_file_2> ...`
>   Pass the specific test files created for this ticket by the Test Designer and Test Breaker.
> - Python: `timeout 120 bash .lefthook/scripts/py-tests.sh`
> - Frontend: `timeout 60 cd asset_generation/web/frontend && npm test`
>
> Do not run `ci/scripts/run_tests.sh` — the orchestrator runs the full Regression Gate after you complete.
>
> **Checkpoint protocol:** Read and follow `agent_context/agents/common_assets/checkpoint_protocol_v1.md`. Resolve by choosing the simplest, most conservative implementation consistent with spec and tests.
>
> If implementation is truly impossible without human input (missing asset, broken dependency, unresolvable conflict), set Stage to `BLOCKED`, write a checkpoint entry with Confidence: Low, and return.
>
> When all ticket tests pass, update the ticket: advance Stage to the appropriate `IMPLEMENTATION_*` completion handoff, increment Revision, set Last Updated By to `<AGENT_NAME>`, set Next Responsible Agent to `Acceptance Criteria Gatekeeper Agent`, set Status to `Proceed`.

**Transition gate:** `implementation_to_static_qa`

---

## Stage 5a — Diff-Cover Preflight (Python tickets only)

If the ticket modified any Python files (`.py`) under `asset_generation/python/`:

```bash
bash ci/scripts/diff_cover_preflight.sh
```

- **Exit 0:** proceed to Stage 5b.
- **Exit 1 (coverage below threshold):** route back to implementation agent with uncovered lines. Retry after agent completes.
- **Exit 2 (setup error):** log a checkpoint with full script output (stdout + stderr), exit code, and working directory. Proceed to Stage 5b only after that log entry exists.

If no Python files were modified, skip.

---

## Stage 5b — Script Review (parallel)

After implementation, invoke all three reviewers **simultaneously in parallel** — all are read-only and analyze orthogonal concerns. Wait for all three before acting on results.

**1. GDScript reviewer** — any new or modified `.gd` files:

Invoke a `gdscript-reviewer` subagent. Require reading `AGENTS.md` → **Code review agents** → **GDScript (`gdscript-reviewer`)`. Enforce review order: organization first (boundaries, cohesion, DRY), then best practices (correctness, readability, naming, error handling, testability). Flag and require removal of tests asserting prose/logging text not in spec.

**2. Python Reviewer Agent** — any new or modified `.py` files:

Invoke a `generalPurpose` subagent as **Python Reviewer Agent**. Require reading `AGENTS.md` → **Code review agents** → **Python (Python Reviewer Agent)** and the `python-reviewer` skill. Same review order: organization first, then best practices.

**3. Architecture Reviewer** — full implementation diff:

Invoke a `generalPurpose` subagent as **Architecture Reviewer**:

> **AUTONOMOUS MODE — NO IMPLEMENTATION**
>
> Read your role definition at `agent_context/agents/misc_agents/architecture_reviewer_v1.md`.
>
> Review the implementation changes for the ticket at `<TICKET_PATH>`. Focus on: agent boundary compliance, coupling, pattern correctness (citing gameprogrammingpatterns.com or refactoring.guru), extensibility for future elemental combinations, and duplication.
>
> Report findings: Critical → High → Medium only. Omit Low. If no Critical or High findings: "Architecture review: no significant findings."
>
> Do NOT write code. Do NOT modify files.

**After all three complete:**
- GDScript or Python reviewer found issues → re-invoke the implementation agent to fix them, then re-run reviewers
- Architecture Reviewer found Critical or High → re-invoke the implementation agent to address them, then re-run Architecture Reviewer
- Medium findings → log to scoped checkpoint only, do not block
- All pass (or no applicable files) → proceed to Regression Gate

**Transition gate (before Stage 7 only):** Run `static_qa_to_learning` immediately before invoking Stage 7. Script Review agents must have written the `static_qa→learning` handoff at end of Stage 5b.

---

## Regression Gate

After Stage 5b passes and before the Human Interaction Verification Gate.

1. Run the full test suite:
   ```bash
   timeout 300 ci/scripts/run_tests.sh
   ```
2. **All pass:** proceed to Human Interaction Verification Gate.
3. **Any failures:** re-invoke the same implementation agent (same type as Stage 5) with:
   - Verbatim failing output (stderr + failing assertions)
   - Mandate: "Fix all regressions introduced by this ticket's changes, including tests in other files. Run only failing test files during iteration — the orchestrator re-runs `run_tests.sh` after you complete."
   Repeat until the full suite passes.
4. **Agent cannot fix without human input:** set ticket to `BLOCKED`, log verbatim output, stop.

---

## Human Interaction Verification Gate (conditional)

After the Regression Gate passes and before Stage 6.

**When to trigger:** The ticket modified Godot gameplay code (`.gd` or `.tscn` files) that produces player-visible behavior — movement, combat, visual effects, audio, or UI.

**When to skip:** Python-only tickets, web frontend/backend tickets, pure engine wiring with no player-visible change, or test-only changes.

**Action when triggered:** Pause and surface to the human:
1. What was implemented (one-paragraph summary)
2. The verification scene path (from Stage 6.5 if already created, or the main sandbox scene `scenes/levels/sandbox/test_movement_3d.tscn`)
3. A checklist of player-facing behaviors to verify manually (derive from the ticket's Acceptance Criteria)

**Wait for explicit human confirmation before proceeding to Stage 6.**

---

## Stage 6 — Acceptance Criteria Gatekeeper

Invoke the `acceptance-criteria-gatekeeper` subagent. Pass this prompt verbatim:

> **AUTONOMOUS MODE — NO HUMAN INTERACTION**
>
> Read the ticket at `<TICKET_PATH>` and the workflow enforcement module.
>
> Your job is to ensure the ticket's `## Acceptance criteria`, `WORKFLOW STATE`, and `NEXT ACTION` are self-consistent and defensible to a skeptical reviewer.
>
> - Treat Acceptance Criteria as contracts, not decoration.
> - For every criterion, look for explicit evidence in Validation Status (tests, adversarial tests, static QA, integration or manual checklist entries).
> - You may only edit the `WORKFLOW STATE` and `NEXT ACTION` blocks.
> - Do not leave Stage as `COMPLETE` unless every Acceptance Criterion has clear, written evidence or is explicitly called out as a manual check that has been performed.
> - When in doubt, prefer a conservative Stage and add a precise `Blocking Issues` entry.
>
> When done:
> - All ACs evidenced → set Stage to `COMPLETE`, increment Revision, set Last Updated By to `Acceptance Criteria Gatekeeper Agent`, set Next Responsible Agent to `Human`, set Status to `Proceed`, move ticket to `done/`.
> - Any AC unmet → route back using the AC Gatekeeper routing table in `agent_context/agents/readme.md`. Do NOT default to `BLOCKED` unless no agent can resolve it autonomously.
>
> **Checkpoint protocol:** Read and follow `agent_context/agents/common_assets/checkpoint_protocol_v1.md`.

**After Gatekeeper (orchestrator action):** When Stage is `COMPLETE` and ticket is under `project_board/**/done/`, run `git status` and commit any uncommitted changes outside `agent_context/` before invoking Stage 7.

---

## Stage 6.5 — Sandbox Verification Scene (Godot gameplay tickets only)

**When to run:** After Stage 6 sets the ticket to `COMPLETE` with `Next Responsible Agent: Human`, and the ticket modified Godot gameplay code (`.gd` or `.tscn` under `scripts/`, `scenes/`, or `tests/`).

**When to skip:** Python-only tickets, web frontend/backend tickets, no runtime Godot changes.

Invoke an `engine-integration` subagent:

> **AUTONOMOUS MODE — NO HUMAN INTERACTION**
>
> Read the ticket at `<TICKET_PATH>` to understand what Godot gameplay mechanic was implemented.
>
> Create a **sandbox verification scene** under `scenes/levels/sandbox/` so the Human can manually verify the feature in-game:
>
> - **Scene file:** `scenes/levels/sandbox/<milestone_id>_verification_<short_slug>_3d.tscn`
>   - Derive `<milestone_id>` from the ticket ID (e.g. `M12-02` → `m12`).
>   - Base on `res://scenes/levels/sandbox/m11_sandbox_base_3d.tscn`.
>   - Place 2–3 `enemy_infection_3d.tscn` instances. Use an existing animated GLB from `assets/enemies/generated_glb/`.
>   - Add a `Label3D` sign explaining what keys to press and what to observe.
>
> - **Controller script (only if needed):** `scripts/levels/<milestone_id>_verification_controller_3d.gd`
>   - Only if the feature requires special slot seeding, hotkeys, or per-frame setup not provided by existing controllers.
>   - Otherwise reuse `M11VerificationController3D`.
>
> Do not run tests or modify existing files. Only create the new scene (and optional controller). If blocked by a missing dependency, log a checkpoint and skip — do not set the ticket to BLOCKED.
>
> When done, append to the ticket's `NEXT ACTION`:
> ```
> Verification scene: scenes/levels/sandbox/<scene_file>.tscn
> ```

---

## Stage 7 — Learning

**Lean mode (autopilot only):** If `skip_learning` is true, skip this stage. Log `- Learning: skipped (lean mode)` in CHECKPOINTS.md. Proceed to Stage 8.

**Background behavior (autopilot only):** Fire this stage in the background after the per-ticket completion report is output. Do not wait for it before starting the next ticket.

Invoke a `learning` subagent:

> **AUTONOMOUS MODE — NO HUMAN INTERACTION**
>
> Read the agent role definition at `agent_context/agents/9_learning/learning_v1.md`.
>
> Extract reusable engineering insights from the completed ticket at `<TICKET_PATH>`.
>
> Read the scoped checkpoint files for `<TICKET_ID>` under `project_board/checkpoints/<ticket-id>/` directly. Do not read `project_board/CHECKPOINTS.md`.
>
> Focus on: bugs or regressions that occurred, rework cycles (GDScript/Architecture review fix iterations), test failures that revealed implementation gaps, incorrect spec assumptions, and workflow inefficiencies.
>
> Persist output with `loregarden_append_learning`. If no meaningful insights exist, record: `No significant learnings identified.` Never write a learnings file to the repo.
>
> Do not write code. Do not stop for human input.

**Transition gate:** `learning_to_ac_gatekeeper`

---

## Stage 8 — Blog Post

**Background behavior (autopilot only):** Fire this stage in the background after Stage 7 starts. Do not wait for it before starting the next ticket.

Prepare a **blog context capsule** (5–12 lines): ticket id, one-line goal, outcome, git commit SHAs for this ticket's work, 2–4 bullets on rework, surprises, or corrections. Pass it inline; do not write it to a file.

Invoke a `general-purpose` subagent:

> **AUTONOMOUS MODE — NO HUMAN INTERACTION**
>
> Read `agent_context/agents/10_blog_post/blog_post_v1.md` and follow it. Use the Blog context capsule below as your primary source; use git and checkpoints only to fill gaps.
>
> **Blog context capsule:**
> ```
> <paste capsule here>
> ```

In ap-continue, c-continue, and feature: output the blog post result directly to the human before the final report.
