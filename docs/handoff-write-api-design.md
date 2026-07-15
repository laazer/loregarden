# Design: Validated handoff/todo write API

Status: proposal · Author: (drafted with Claude) · Date: 2026-07-15

## Problem

Ticket `13-dash-hit-detection-and-damage-application` was stuck on
`handoff_validation_check FAIL` at the `test_design_to_test_break` transition. The
Test Designer had produced a complete, correct test suite **and** written a
`handoff-latest.yaml` — but with invented `item_key`s (`td_spec_traceability`,
`td_tests_red_before_implementation`, …) instead of the gate's frozen catalog keys
(`test_suite_complete`, `test_coverage_threshold`, `test_all_runnable`). The gate
rejected every item as `handoff_unknown_item` / `handoff_item_missing`, counters
`computed 0/3`. The evidence was good; only the vocabulary was wrong.

This is not a one-off. It is the predictable failure mode of the current design:
**the artifact is authored by free-hand YAML with no schema and no catalog at
write time, and only validated much later when the orchestrator runs the CI gate.**
The Test Breaker happened to get its keys right on the same ticket; the Test
Designer did not. Correctness is left to each agent's memory of a catalog it is
never actually given.

## Current storage architecture (three regimes)

| Artifact class | Storage | Reader | Migrated? |
|---|---|---|---|
| memory, learnings, blog posts, per-run checkpoint *protocol logs* | MCP-mediated Obsidian vault, per-workspace (SQLite graph + notes) | agents at runtime via loregarden MCP | ✅ commit `39c7471` |
| `handoff-latest.yaml`, `todos-latest.json` | plain files, **committed to the target repo's git** (`blobert/project_board/checkpoints/<ticket>/`) | hermetic CI gate `ci/scripts/gates/*.py` (stdlib + pyyaml only, no loregarden, no DB, no network) | ❌ still files |
| `agent_context/` prompt & protocol docs | **symlink to iCloud Drive** (`~/Library/Mobile Documents/.../blobert_agent_context`) | agents at runtime | ❌ neither repo's git |

The checkpoint-log migration deliberately did **not** move the gate artifacts:
their reader runs *inside the target repo's CI* with loregarden dead, so a
git-committed file is the correct, portable substrate (cf. commit-tracked build
provenance). Moving them into a DB would couple the target repo's CI to
loregarden's runtime — a regression.

**So "files" is not the disease.** The disease is: no validated write path, a
catalog that lives only in the gate + spec (never handed to the author), and a
third, fragile iCloud symlink for prompts.

## Proposal: a validated-write MCP tool

Add `loregarden_write_handoff` (and sibling `loregarden_write_todos`), following the
established memory-tool pattern (`server/loregarden/mcp/tools.py`
`_execute_memory_tool`, schema list, `workspace_slug` scoping). The tool takes
**structured** input, not raw YAML, and **validates before it commits the file**.

### Why this closes the gap

- The agent submits `{from_agent, to_agent, checklist:[{item_key, status, evidence, …}]}`.
- The tool renders canonical YAML, writes it to the workspace repo path, then
  **runs the workspace's own gate as the validator** (see below).
- On PASS: keep the file, return success.
- On FAIL: do **not** leave a broken file (write to a temp path / restore prior
  `handoff-latest.yaml`), and return the gate's `violations[]` to the agent so it
  self-corrects **in the same turn** — instead of discovering the failure later
  when the orchestrator gate blocks the pipeline.

### Single source of truth for the catalog (the key decision)

The frozen catalog lives in each workspace's gate
(`ci/scripts/gates/handoff_validation_check.py::PAIR_CATALOGS`). The write tool
must not duplicate it (drift). Options:

- **A. Duplicate catalog in loregarden** — rejected: two catalogs drift; the gate
  is still the real judge, so loregarden's copy would give false confidence.
- **B. Shell out to the workspace gate to validate (recommended).** loregarden
  resolves the workspace repo via `resolve_workspace_root(workspace)` (already
  used by `gate_runner.py`), renders the YAML, writes it, and invokes
  `python ci/scripts/gate_runner.py handoff_validation_check --input {…}` (or
  `run_workflow_transition_gates.py`) in that repo. The gate stays the sole
  authority; loregarden adds only the *validated-write + self-correct loop*.
  Zero duplication, CI hermeticity preserved.
- **C. Gate emits its catalog as JSON** that loregarden reads and validates
  against natively — more plumbing (new gate subcommand `--dump-catalog`), but
  gives loregarden fast local validation without a subprocess per write. Viable as
  a v2 optimization on top of B.

Recommend **B now, C later** if subprocess latency matters.

### Tool contract (sketch)

```
loregarden_write_handoff:
  inputs:
    ticket_id:      str   (required)
    workspace_slug: str   (required — scopes repo + gate)
    from_agent:     str   (required)
    to_agent:       str   (required)
    checklist:      [ {item_key, status, evidence, evidence_type?, item?, required?} ]  (required)
  behavior:
    - normalize agent names; look up pair; auto-fill `item`/`required` from catalog
      so the agent cannot mislabel them
    - compute required_items_met / total_required_items (agent never hand-counts)
    - render canonical handoff-latest.yaml, write atomically to
      {repo}/project_board/checkpoints/{ticket}/handoff-latest.yaml
    - run the workspace gate; on FAIL restore prior file and return violations[]
  returns: { status: PASS|FAIL, path, violations[], remediation_hints[] }
```

Auto-filling `item`/`required` and the counters from the catalog eliminates three
of the observed failure rules (`label mismatch`, `required flag mismatch`,
`counter mismatch`) structurally — the agent supplies only key + status +
evidence.

## Rollout

1. Land the doc band-aid (frozen catalog table in
   `mandatory_workflow_gates_v1.md`) — **done** — so free-hand authors stop
   inventing keys immediately.
2. Implement `loregarden_write_handoff` (option B). Update the workflow protocol
   docs to tell finishing agents to call the tool instead of writing YAML by hand.
3. Add `loregarden_write_todos` symmetrically for `todos-latest.json`.
4. (Optional v2) `--dump-catalog` gate subcommand for native validation.

## Non-goals

- Moving gate artifacts into a DB / object store — breaks CI hermeticity; the
  files-in-target-git substrate is correct.
- Rewriting the gate — it stays the single authority; we wrap authoring only.

## Open questions

- The `agent_context/` iCloud symlink is out of scope here but is the most
  fragile of the three regimes (unversioned prompts). Worth a separate decision:
  vendor it into the target repo's git, or serve it through MCP like memory.
- Should the write tool also stage/commit the file (the protocol requires
  explicit-path commits on handoff), or leave commit to the agent? Leaning:
  return the path and let the existing commit-on-handoff step handle it, to
  respect the shared-worktree git-hygiene rules.
```
