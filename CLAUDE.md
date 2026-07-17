# CLAUDE.md

Repository-level operating manual for Claude Code and other coding agents working in
**loregarden**. For *where things are* — structure, code map, commands — read `AGENTS.md`.

## General Guidelines

1. **Ask, don't assume.** If something is unclear, ask before writing a line. When running unattended, pick the most reasonable interpretation, proceed, and record the assumption with `loregarden_append_checkpoint` rather than blocking.
2. **Simplest solution for simple problems**, better solutions for hard ones. Do not add flexibility nothing needs yet.
3. **Don't touch unrelated code** — but do surface smells you find, as a separate issue.
4. **Flag uncertainty explicitly.** Confidence without certainty causes more damage than admitting a gap. A small, low-risk experiment beats a confident guess.
5. **Verify before reporting.** This repo will happily let you conclude something false from a plausible-looking query. See *Verify, don't infer* below.

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
- Migrations append-only, each guarding its own changes; never rewrite an applied id.
- Test isolation via `unittest.mock` over `monkeypatch`, unless mocking handles the case poorly.
- No ticket IDs in filenames.
- Watch the hotspots in `AGENTS.md` → *Notes*: new code in a 1000-line service usually belongs
  in a new module.

The automated gates (Ruff, Pylint diff-scoped, organization, defensive-normalization) run on
staged files via lefthook. A reviewer adds judgment the gates cannot: is this the right shape,
in the right place, with the right seams.

### GDScript (`gdscript-reviewer`)

**Not applicable in this repo** — loregarden contains no `.gd` files. The `gdscript_reviewer`
agent exists in the registry to serve workspaces that supply their own role file and their own
GDScript rules. If a loregarden ticket routes here, that is a routing bug, not a review task.

### Frontend

Applies to `client/**/*.{ts,tsx}`. oxlint runs on staged files. Beyond it: no `as any` or
`@ts-ignore` suppression, no empty catch blocks, and no assertions on copy that no spec pins.

## Workflow discipline

- **The orchestrator commits the entire working tree.** Anything uncommitted when a stage
  finishes gets swept into that ticket's commit — including work unrelated to the ticket. Do not
  hand-edit files while an orchestration runs; if you must, expect the sweep and check
  `git log -p` afterwards.
- **Backend edits need a reload:** `touch server/.self-improve-restart`. The dev server ignores
  `.py` changes otherwise, and you will test stale code and believe your fix failed.
- **Run pytest with the git env unset** from a worktree: `env -u GIT_DIR -u GIT_WORK_TREE`.
  Otherwise the pre-push suite explodes with `git add .` exit-128 errors that have nothing to do
  with your change.
- Use `task dev` / `task server` / `task client`. Do not start servers ad-hoc.

## Anti-patterns

See `AGENTS.md` → *Anti-patterns* for the table with evidence. The short version: no report
markdown, no ticket-file hunting, no editing v1 YAML expecting an effect, no ticket IDs in
filenames, no defensive normalization, no hand-edits during an orchestration.
