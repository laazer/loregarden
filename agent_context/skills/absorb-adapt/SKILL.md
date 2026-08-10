---
name: absorb-adapt
description: Method for evolving loregarden's own pipeline — read the run telemetry the control plane already records, find friction that repeats across tickets, and land the fix as a ticket, a skill body, or a workflow template change rather than a proposal document. Use after several tickets or runs, when the same friction shows up more than once, or during a retrospective. Not for one ticket's postmortem, and not for a change you cannot point at a repeated failure for.
---

# Absorb-Adapt — change the system, not the ticket

You are not fixing a ticket; you are changing what the pipeline does to *every*
ticket after this. That leverage is why the bar is evidence, not intuition.

Three rules this whole skill rests on: **evidence from the tables, never a
guess** (§1); **the DB is what runs — files are seed material** (§3); and
**output goes to `loregarden_create_ticket` / `attach_artifact` /
`append_learning`, never to a findings `.md`** (§4).

## 1. Read the telemetry before forming an opinion

Loregarden observes its own runs, so most friction you would guess at is already
recorded — and a guess that contradicts the tables is worse than no proposal.
The DB is `data/loregarden.db` (`settings.database_url`, resolved against the
repo root); query it with `sqlite3`.

- **`agent_runs`** — `status`, `agent_id`, `skill_name`, `stage_key`. Failures
  concentrated on one `(agent_id, stage_key)` pair is the strongest signal here.
- **`orchestration_runs.error_message`**, grouped — a recurring message is a
  missing capability, not bad luck.
- **`artifacts`** — `kind='error'`; `kind='rework_feedback'` (the reroute ledger
  in `rework_feedback.py`: one row per reroute, so the count *is* the loop
  metric); `kind='blocked_report'`. Gate evaluations land here too, titled by
  outcome (`gate_observability.py`).
- **`auto_fix_attempts`** — `attempt_number` by gate. A gate needing several
  self-heal attempts is a gate whose failure text agents cannot act on.
- **`approvals`** — where humans intervene, and on what. **Trap:** auto-approved
  tool calls write no row; zero rows means "never called on an `auto_approve=0`
  run", not "unused".
- **`loregarden_search_memory` / `search_prior_work`** — learnings, and the
  checkpoints where agents recorded assumptions they had to invent. The same
  assumption invented three times is a missing spec field, not three errors.

Take a count before taking a position. "Three tickets, ids named" is what makes
a proposal reviewable.

## 2. Classify what you found — the fixes differ

| Observation | Usually means | Fix lands in |
| --- | --- | --- |
| One agent fails the same way | Its role/skill omits a rule | `studio_agents.role_body` / skill body |
| Every agent invents one assumption | Upstream stage under-specifies | Upstream skill, ticket template |
| A stage reroutes in a loop | Routing or gate feedback unreadable | `stages_json`, gate detail text |
| A mechanical step is still prompted | It should be code | Hand to the `vulcan` skill |

Prefer extending what exists. A role body that has grown a second job justifies
a new agent; one that is merely long does not.

## 3. Know where a change actually takes effect

System-evolution work here most often fails by landing somewhere inert.

- **Agents and workflows are DB-authoritative**: `studio_agents.role_body`,
  `workflow_templates.stages_json`. The registry and `agent_context/workflows/*.yaml`
  are v1-era seed material — editing them changes nothing live.
- **Skills too, and the seed is insert-only.** `seed_builtin_skills` skips slugs
  already in `skills`, so a *new* `agent_context/skills/<slug>/SKILL.md` seeds on
  first lookup while an edit to an existing one is inert. Changing a live skill
  goes through `SkillService.update_skill` (Ticket Studio), which writes the
  `skill_versions` row.
- **A skill reaches an agent only if named** — a stage's `run.skill_name` or
  `studio_agents.default_skill`. A misspelled one raises `SkillNotFoundError`;
  an unreferenced one is dead weight. Bodies are truncated at
  `SKILL_PROMPT_CAP` (3000 chars) when rendered into a prompt, so a long skill
  silently loses its tail — keep the load-bearing part first.
- **`alwaysApply: true` does nothing here** (Cursor convention). A common asset
  reaches an agent only if a role body says to read it or `executors/cli.py`
  embeds it.
- **A workflow template needs a terminal stage**, or a passing gate re-loops.

Where your recommendation depends on one of these, exercise the real path
against the real data first.

## 4. Land the output as work, not as a document

**Never write a findings, retro, or evolution `.md`.** Nothing reads it, and a
running orchestrator sweeps the working tree into an unrelated ticket's commit.

- Each accepted proposal → **`loregarden_create_ticket`**, evidence in the
  description (ticket ids, counts, the query), real acceptance criteria.
  Features need a parent; order with `loregarden_link_dependency`.
- Long analysis → **`loregarden_attach_artifact`** (`kind="analysis"`).
- Durable lesson → **`loregarden_append_learning`**. Assumption you picked →
  **`loregarden_append_checkpoint`**.

A proposal with no ticket gets re-derived next month.

## 5. What each proposal carries

Observation with its evidence; the concrete change (which agent, skill,
template, gate); where it lands, chosen against section 3; what it costs — every
added instruction is prompt budget spent on every future run, so say what it
displaces; and the query that would later show it worked. Rank by frequency ×
cost, not by how interesting the finding is.

## 6. Do not overfit

One ticket is an anecdote. If a pattern appears once, say so and ask for more
data — a rule added on one example is paid for on every run forever and is
nearly impossible to remove, because nobody can prove it is not helping. And do
not propose a change you cannot point at a failure for.
