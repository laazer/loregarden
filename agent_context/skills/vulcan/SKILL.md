---
name: vulcan
description: Method for finding logic that is being prompted when it should be executed — mechanical steps in loregarden's skills, stages, and gates that belong in a script, a lefthook gate, a service function, or an MCP tool instead of an agent turn.
---

# Vulcan — stop paying an agent to do arithmetic

A deterministic step left in a prompt is bought twice: in tokens on every run,
and in the nondeterminism of a model doing what a function would do identically.
You identify and specify these. Write the script only when asked.

Two rules this rests on: **read the DB bodies, not the `agent_context/` files —
the DB is what runs** (§1); and **each accepted extraction becomes a
`loregarden_create_ticket`, never a findings `.md`** (§5).

## 1. Where the mechanical work hides here

Read what agents are actually instructed to do, not the workflow diagram:

- **`skills.body` and `studio_agents.role_body`** in the DB — these are
  authoritative; `agent_context/` files are seed material. Any step phrased "run
  X, then parse the output for Y" is a candidate.
- **`workflow_templates.stages_json`**, plus `gate_checks_json` /
  `handoff_checks_json` — checks described in prose rather than named commands.
- **`.lefthook/scripts/`** — the pattern to copy already exists there
  (`py_organization_check.py`, `detect-defensive-normalization.sh`,
  `py_git_subprocess_check.py`). A rule an agent is *told* to follow and a rule a
  gate *enforces* are not the same thing, and the gate is cheaper.
- **`scripts/`** — if agents run a multi-command incantation, it belongs here as
  one command.
- **`auto_fix_attempts`** — a gate whose failures are repeatedly repaired by the
  same mechanical edit is a fixer that does not exist yet.

## 2. The test for "should be code"

All five, not a majority:

1. **Deterministic** — same input, same output: formatting, parsing, validation,
   counting, file placement.
2. **Repeated** — most runs, or every ticket of a kind. Query before asserting
   frequency (see `absorb-adapt` for which tables carry signal).
3. **Reasoning-free** — "check every changed file has a test" qualifies; "check
   the tests are meaningful" does not.
4. **Stable** — the rule is settled. Scripting a rule still being argued about
   freezes a draft into something that blocks people.
5. **Checkable** — its output can be asserted in a test.

Failing 3 or 4 means leave it in the prompt and say why. Judgment encoded as a
regex is confidently wrong and expensive to argue with.

## 3. Say where it lands, in this repo's terms

| The step is… | Belongs as… | Note |
| --- | --- | --- |
| A rule violated in written code | A **lefthook gate** in `.lefthook/scripts/` | Staged-file scoped; must also run in CI |
| A repeated operator incantation | A **script** in `scripts/` | One command, documented usage |
| Logic the server needs at runtime | A **service function** in `server/loregarden/services/` | Testable directly, not a subprocess |
| A structured artifact an agent hand-writes | An **MCP tool** | Follow `loregarden_write_handoff` |

That last row is the highest-value pattern here. An agent asked to hand-write
structured YAML invents keys; a tool that renders the canonical shape,
validates, and rolls back with violations on failure makes that impossible
rather than merely discouraged.

Two non-negotiables: anything shelling out to `git` or `gh` goes through
`loregarden.services.git_subprocess.run_git` (`GIT_DIR` beats `cwd`; the
`py-git-subprocess` gate enforces it), and a new gate must run in CI as well as
lefthook — `LEFTHOOK=0` makes a lefthook-only gate advisory.

## 4. What a proposed extraction specifies

- **Where it lives today** — the skill body, role body, stage, or gate, named.
- **Why it qualifies** — against the five tests, with the frequency evidence.
- **Interface** — invocation, inputs, outputs, exit codes. A gate must exit
  non-zero with detail an agent can act on; a bare "failed" sends the run into a
  retry loop and burns the auto-fix budget.
- **Edge cases** — empty input, unparseable file, partially staged tree, running
  from a worktree with `GIT_DIR` already set.
- **What is deleted from the prompt** when it lands. An extraction that adds a
  script and leaves the prompt text makes the system larger, not faster.
- **Impact / frequency / confidence**, each low-medium-high, so the list orders.

## 5. Output goes into the system, not into a file

**Never write a findings `.md`** — nothing reads it, and a running orchestrator
sweeps the working tree into an unrelated ticket's commit. Each accepted
extraction becomes a ticket via `loregarden_create_ticket` with the evidence in
the description; a long list goes to `loregarden_attach_artifact`
(`kind="analysis"`). A short list is just your reply.

## 6. Restraint

Do not script reasoning-heavy or ambiguous logic. Prefer one small composable
tool over a framework — every script is a thing that will later be wrong and
need maintaining. Do not propose an extraction whose payoff you cannot state:
"runs on every ticket, saves a stage retry" is a payoff, "cleaner" is not. If
the workflow you are reading is unclear, ask — a script built on a
misunderstood step is enforced nonsense every future run must satisfy.
