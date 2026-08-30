# CLAUDE.md

Repository-level operating manual for Claude Code and other coding agents working in
**loregarden**. For *where things are* — structure, code map, commands — read `AGENTS.md`.

## General Guidelines

1. **Ask, don't assume.** If something is unclear, ask before writing a line. When running unattended, pick the most reasonable interpretation, proceed, and record the assumption with `loregarden_append_checkpoint` rather than blocking.
2. **Simplest solution for simple problems**, better solutions for hard ones. Do not add flexibility nothing needs yet.
3. **Don't touch unrelated code** — but do surface smells you find, as a separate issue.
4. **Flag uncertainty explicitly.** Confidence without certainty causes more damage than admitting a gap. A small, low-risk experiment beats a confident guess.
5. **Verify before reporting.** This repo will happily let you conclude something false from a plausible-looking query. See *Verify, don't infer* below.

## Output economy

Tokens are the budget here — this control plane pays for every agent turn. Keep output lean
without dropping rigor:

1. **Be terse.** No preamble, no filler, no restating the request. Answer, show the evidence,
   stop. Match reply length to the task — a one-line change gets a one-line report.
2. **Prefer structure over prose.** Return findings, status, and data as tables, lists, or
   JSON the caller can parse, not paragraphs. Route long reports to
   `loregarden_attach_artifact`, never the response body (see *The database is the source of
   truth*).
3. **Read narrowly.** Fetch the lines and files you need, not whole trees — over-reading
   inflates input cost more than any verbose reply does. Prefer targeted search and
   `loregarden_get_ticket` over broad greps.

Never trade correctness, tests, or required evidence for brevity. Cut filler, not substance.

## Project Overview

An Agent SDLC IDE — a local control plane orchestrating multi-agent development. Tickets in
SQLite, run through configurable TDD pipelines by CLI agents, gated by an approval inbox,
exposed over MCP.

- **Backend:** FastAPI 0.115 + SQLModel 0.0.22 + Pydantic **v2** on SQLite, Python 3.11
- **Frontend:** React 19 + TypeScript 6 + Vite 8, Zustand 5, **Jest 30**, **oxlint**
- **Desktop:** Tauri 2
- **Agents:** Claude Code / Cursor CLI subprocesses, driven over MCP

There is no game engine in this repo — no `.gd`, `.tscn`, `.blend`, or shader sources. Hive
(`client/src/lib/hive/`, `client/src/components/dashboard/hive/`) is a React/canvas office
simulation that visualizes agent activity. Despite the tile coordinates, NPCs, and sprites, it
is ordinary frontend code, and its tickets belong here.

## The database is the source of truth

Tickets, workflow templates, learnings, checkpoints, and artifacts all live in the database or
the workspace vault — **never in repo files**. Reach them through the `loregarden_*` MCP tools.

Concretely, and these are the mistakes agents actually make here:

- There is **no ticket markdown**. Do not grep for one; use `loregarden_get_ticket`.
- `agent_context/workflows/*.yaml` is **v1-era**. Live stage definitions are in
  `workflow_templates.stages_json`. Editing the YAML changes nothing.
- **Never write a report, summary, findings, spec, or stage-completion `.md`.** Loregarden reads
  none of them; the orchestrator sweeps them into an unrelated ticket's commit. Route reports to
  `loregarden_attach_artifact`, decisions to `loregarden_complete_stage`, assumptions to
  `loregarden_append_checkpoint`, learnings to `loregarden_append_learning`.
- **No MCP tools attached, or the server is down?** Every tool is also a CLI command that runs
  against the database in-process: `./scripts/loregarden-cli.sh mcp call <tool> key=value…`
  (`mcp list` / `mcp describe <tool>` to find the arguments). Use it instead of curling `/mcp`
  or abandoning the write — see `agent_context/agents/common_assets/loregarden_mcp_v1.md`.

Writing real source code and real test files is, of course, still the job. The rule is about
*reports about* the work.

## Verify, don't infer

This control plane observes itself, and several of its tables record only part of the story.
Three traps that have produced confidently wrong conclusions:

- **Auto-approved tool calls write no `approvals` row.** Querying `approvals` for a tool and
  finding zero does **not** mean it was never called — only that it was never called on an
  `auto_approve=0` run.
- **`alwaysApply: true`** in prompt frontmatter does nothing. It is a Cursor convention. A common
  asset reaches an agent only if its `role_file` says to read it, or `executors/cli.py` embeds it.
- **The YAML workflows are a decoy.** Read `workflow_templates` from the DB.

When a query result would change your recommendation, exercise the real code path against the
real data before you rely on it. Prefer a runnable check over a plausible reading.

## Diagnose intermittent failures; do not re-roll them

A test that fails under the hook and passes on its own is a *diagnosis task*, not a dice roll.
Retrying a push, or rerunning a suite hoping for green, is the two most expensive minutes an
agent can spend — and giving up after the retries fail is worse, because the cause was
discoverable the whole time.

Work it in this order:

1. **Find the first failure, not the loudest one.** A cascade dominates the output. One run here
   showed 54 "unable to find an element" errors and 4 timeouts; the 54 were downstream of the
   first timeout corrupting that file's harness state. Counting symptoms picked the wrong
   suspect. Read the first `●` in each failing file.
2. **Read the failure's own words.** `Exceeded timeout of 5000 ms` names its cause. Do not
   theorise past a message that is already specific.
3. **Look for a quantity that separates pass from fail.** Wall-clock, worker count, ordering,
   `--changedSince` base. Here `client-tests` passed at 310s and failed at 323s / 369s / 442s —
   that correlation *is* the evidence, and it arrived faster than any of the retries.
4. **Rule out your own change with an import path, not a feeling.** "Unrelated" means the failing
   file cannot reach what you edited. Check it: `grep -rl <module> client/src`.
5. **Distinguish slow from hung.** A file that completes in 45s under load and 9s idle has a
   budget problem. One that never returns has a deadlock. Only the first is fixed by a timeout.

State what you could not determine. "It passed on retry" is not a diagnosis, and reporting it as
one hides a defect that will resurface on someone else's branch.

## Agent Checkpoints (Autopilot / Autonomous Agents)

When running unattended and you hit a decision a human would normally make — an ambiguous
requirement, a missing asset, an assumption you had to pick — record it with
`loregarden_append_checkpoint` and continue. Never write a checkpoint file. If the work is
genuinely blocked (broken dependency, unresolvable conflict), call `loregarden_block_ticket`
and stop rather than inventing a way around it.

Before editing tests, run the relevant test command **once** to capture the pre-existing failure
baseline, and record it with `loregarden_append_checkpoint`. Do not attribute inherited failures
to your change — or claim a green suite you did not verify.

## Code review agents

Reviewers run in a fixed order: **organization first** (boundaries, cohesion, DRY, does this
belong here at all), **then best practices** (correctness, readability, naming, error handling,
testability). Report **Critical → High → Medium**; omit Low. Flag and require removal of tests
asserting prose or logging text that no spec requires.

Return the review in your response, or via `loregarden_attach_artifact` if long. Never as a
markdown file.

### Python (Python Reviewer Agent)

Applies to `server/**/*.py`. Enforce, beyond the automated gates:

- Module-level imports; no function-local imports to dodge cycles — fix the cycle.
- Pydantic **v2** idioms (`model_validate`, `model_dump`); v1 patterns are a bug.
- No defensive normalization (`str(x).strip().lower()` on a value already normalized at its
  source) — the `detect-defensive-normalization` gate enforces this.
- No stringly-typed vocabularies. A closed set of values is an enum
  (`models/domain/enums.py`, `mcp/tool_ids.py`), not a string: no `run.status == "failed"`
  next to `RunStatus`, no `status: str` / `kind: str` parameters, no inline `x in {"a", …}`
  sets, no literal compared all over a module with no type behind it. The `py-organization`
  gate enforces all four on staged lines; `# py-org: allow-string` waives a line, and is for
  vocabularies we do not own (a GitHub conclusion of `"skipped"` is not `CIStatus.SKIPPED`).
- No `isinstance`. `isinstance(payload, dict)` is a schema check written by hand — model the
  payload with Pydantic at the boundary and pass the model. `isinstance(x, SomeClass)` is a
  type switch — dispatch polymorphically or through a `typing.Protocol`. Same gate, same
  scoping; `# py-org: allow-isinstance` waives a line for genuinely foreign objects
  (`__eq__`, a `TypeDecorator`, a third-party payload not yet modelled).
- Migrations append-only, each guarding its own changes; never rewrite an applied id.
- Test isolation via `unittest.mock` over `monkeypatch`, unless mocking handles the case poorly.
- No ticket IDs in filenames.
- Watch the hotspots in `AGENTS.md` → *Notes*: new code in a 1000-line service usually belongs
  in a new module.

- No silently caught exceptions. A broad `except Exception` (or bare `except:`, or
  `contextlib.suppress(Exception)`) whose body is inert — `pass`, `return None`, `return False`,
  `value = None` — reports success the code never had. Log it, re-raise it, record it on the
  result, or narrow the catch to the failure you actually expect. Handlers that surface the
  error are fine. The `py-silent-except` gate enforces this — on staged lines pre-commit, and
  on the worktree at every stage transition — and, like the organization gates, it is
  diff-scoped: only handlers your change added or edited can fail it. `# py-silent: allow` on
  the `except` line waives one, for a swallow that is genuinely right (best-effort cleanup on
  an already-failing path).

- Shell out to git through `loregarden.services.git_subprocess.run_git`, never
  `subprocess.run(["git", ...])` — `GIT_DIR` overrides `cwd`, so an unscrubbed call can operate
  on the wrong repository. Same for `gh`, which resolves its repo through git. The
  `py-git-subprocess` gate enforces this.

The automated gates (Ruff, Pylint diff-scoped, organization, defensive-normalization,
silent-exception, git-subprocess routing) run on staged files via lefthook.

**The organization and silent-exception gates are workspace-agnostic and run twice.** One copy
lives in `.lefthook/scripts/`; both surfaces invoke it from there rather than copying it
around:

- **Pre-commit**, on staged files — for loregarden via `lefthook.yml`, for other workspaces via
  `scripts/install-workspace-hooks.sh <workspace-root>`, which writes a marker-delimited block
  into that repo's `lefthook.yml` pointing back at this checkout (`--check` reports drift
  without writing).
- **Orchestration gates**, on every stage transition in every workspace — the `gates.commands`
  in `agent_context/orchestration/*.yaml`, including `default.yaml`, so a workspace with no
  profile of its own still gets them. They run `--scope worktree`, because an agent's edits are
  uncommitted when the gate fires, and that scope includes untracked files — a module the agent
  just wrote is the least-reviewed code in the run and `git diff` never lists it.

- **On demand**, via `loregarden_check_organization` — ask what the gate will say before
  spending a stage finding out:

      loregarden_check_organization workspace_slug=blobert action=check scope=worktree

  `action` is `check` (default, read-only), `hooks_status`, or `install_hooks`. Approval
  policy follows the *action*, not the tool name: the reads auto-approve, `install_hooks`
  goes to the inbox because it rewrites another repo's git hooks.

Layout is detected per repo (Python root, TypeScript root, enum home, error helper), so the
messages name the target workspace's own modules. Nothing here assumes loregarden's tree. A reviewer adds judgment the gates cannot: is this the right shape,
in the right place, with the right seams.

### GDScript (`gdscript-reviewer`)

**Not applicable in this repo** — loregarden contains no `.gd` files. The `gdscript_reviewer`
agent exists in the registry to serve workspaces that supply their own role file and their own
GDScript rules. If a loregarden ticket routes here, that is a routing bug, not a review task.

### Frontend

Applies to `client/**/*.{ts,tsx}`. oxlint runs on staged files. Beyond it: no `as any` or
`@ts-ignore` suppression, no empty catch blocks, and no assertions on copy that no spec pins.
No inline `err instanceof Error ? err.message : "…"` ternary either — that narrowing is
`describeError(error, fallback)` in `state/toastStore`, which also recovers the `ApiError`
status line the ternary discards. The `ts-organization` gate enforces it on staged lines;
`// ts-org: allow-instanceof` waives one. Type guards inside a helper stay legal.

## Knowing when a ticket is done

A ticket is done when its **acceptance criteria** are met — not when reviewers stop finding
things. Those are different conditions, and conflating them is how a ticket runs six implement
rounds.

- **A reject needs a named unmet criterion.** `fail`/`needs_rework` asserts that a stated
  criterion is false. A reviewer who finds a genuine defect that no criterion covers should
  **file a ticket and pass**, saying what it filed. If `unmet_criteria` comes back empty on a
  reject, that is the signal the ticket is finished and the finding is new scope.
- **Honour the loop cap.** `MAX_REWORK_REROUTES = 3` in `services/rework_feedback.py` blocks a
  ticket that has bounced to the same stage three times. It is a durable, deliberate safeguard.
  `loregarden_requeue_ticket` can clear it, and the reason string should name the unmet
  criterion that justifies another round. "The reviewers found more real defects" is not that
  reason — it is the thing the cap exists to stop.
- **Budget rounds when the ticket starts, not mid-flight.** Decide up front how many implement
  rounds a ticket gets. Sunk cost is loudest at round four, which is exactly when the decision
  is worst.
- **A defect family is not a ticket.** "The gate can be made to examine nothing" has as many
  instances as git has ways to produce odd output. Sequential review rounds are the wrong
  instrument for a search space: each round finds one more and none of them converge. Ship the
  invariant, add a property-based or conformance test that generates the space, file the
  enumeration, and stop. Three consecutive framings of a fix being falsified by the next review
  is evidence about the method, not a reason for a fourth framing.

Recorded because it happened: ticket 546 met all four of its acceptance criteria at its third
round, then ran three further implement rounds on real findings that no criterion covered. The
work was good and the defects were real; the rounds should have been tickets.

## Workflow discipline

- **The orchestrator commits the entire working tree.** Anything uncommitted when a stage
  finishes gets swept into that ticket's commit — including work unrelated to the ticket. Do not
  hand-edit files while an orchestration runs; if you must, expect the sweep and check
  `git log -p` afterwards.
- **Backend edits need a reload:** `touch server/.self-improve-restart`. The dev server ignores
  `.py` changes otherwise, and you will test stale code and believe your fix failed.
- **Pushing from a worktree no longer breaks the pre-push suite.** Git exports an absolute
  `GIT_DIR` into hooks when you push from a worktree; tests that build throwaway repos in
  `tmp_path` inherited it (`GIT_DIR` beats `cwd`) and died with `git add .` exit-128.
  `.lefthook/scripts/hook-noninteractive.sh` now unsets `GIT_DIR`/`GIT_WORK_TREE`, so the
  `env -u GIT_DIR -u GIT_WORK_TREE` workaround is no longer needed. Still scrub the env if you
  invoke pytest yourself from a context that already has `GIT_DIR` set (e.g. nested in a hook) —
  the server's own git helpers pass the ambient environment through.
- **Pre-push runs only the tests your commits reach.** `select_pytest_targets.py` walks the
  import graph (string constants included, so `import_module("loregarden.x")` counts) and pytest
  runs that subset; jest runs `--changedSince`. Anything unmappable — `conftest.py`,
  `pyproject.toml`, a non-Python file, anything outside `server/`/`client/` — falls back to the
  full suite, as does selecting zero tests. CI still runs everything, so a green push *predicts*
  CI rather than proving it. `LOREGARDEN_FULL_TESTS=1` forces the full run;
  `LOREGARDEN_TESTS_BASE=<ref>` asks what a given range would run.

- Use `task dev` / `task server` / `task client`. Do not start servers ad-hoc.

## A gate you tripped is yours to fix

The gates encode the standards this repo has decided on. Tripping one is not an obstacle between
you and your change — it *is* part of your change. Fix the cause and move on; do not ask which
gate to skip, and never reach for `--no-verify`, `LEFTHOOK=0`, or `LEFTHOOK_EXCLUDE` (a
`PreToolUse` hook denies the first of these outright).

This includes the case that feels unfair: **a file already over a limit before you touched it.**
The size, complexity and organization gates run on the whole staged file, not on your diff, so
editing one line of a 1673-line component makes its 1200-line cap yours to satisfy. That is
deliberate — it is how a file that has been growing unchecked finally gets split, and the person
who touched it next is the one holding the context to do it. Extract the cohesive piece, keep
state where it is, let `tsc`/`pytest` prove the seam, and say in the PR why an unrelated-looking
refactor is in the diff.

Two failure modes to avoid when fixing:

- **Do not trade one gate for another.** A `getattr`/`setattr` table that satisfies the
  complexity gate trips the organization gate. Reach for the structured API instead
  (`model_dump(exclude_none=True)` into `sqlmodel_update`).
- **Run the gate directly rather than through a commit.** `.lefthook/scripts/*` take a
  `--scope worktree` flag, and `loregarden_check_organization` answers before you spend a commit
  finding out.

## Anti-patterns

See `AGENTS.md` → *Anti-patterns* for the table with evidence. The short version: no report
markdown, no ticket-file hunting, no editing v1 YAML expecting an effect, no ticket IDs in
filenames, no defensive normalization, no hand-edits during an orchestration.

Two more, from the sections above: **no retrying a failing push in place of diagnosing it**, and
**no asking which gate to skip** — a gate your change trips is part of your change.
