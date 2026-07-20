---
name: plan
description: How planning work is recorded here — the plan is attached as a retrievable artifact, not left in the run transcript, so every later stage can read the decisions instead of re-deriving them.
---

# Plan — write it down where the pipeline can find it

## Attach the plan as an artifact

When the plan is settled, attach it:

```
loregarden_attach_artifact(kind="plan", title="Plan — <ticket title>", content={...})
```

This is the part that is easy to skip and expensive to skip. A plan left in your
reply lives only inside a run transcript that nothing downstream reads — the
spec, test-design, and implement stages then rebuild your reasoning from the
ticket text and reach different conclusions. An attached plan is injected
directly into those later prompts.

Attach one artifact, once, when the plan is final. A stage report is a verdict
(`pass` / `needs_rework`), not a place to put the plan.

## What the plan has to contain

Downstream agents act on this without asking you follow-up questions, so it
must stand alone:

- **The approach** — what is being built, in enough detail that someone who has
  not read your reasoning would make the same structural choices.
- **The steps**, ordered, each small enough for one agent run, each naming what
  it needs as input and what it produces.
- **The seams** — which existing modules are touched, and which are deliberately
  left alone. Say where new code belongs.
- **Risks and assumptions**, explicitly. Anything you had to guess is the thing
  most likely to be wrong; naming it lets a later stage check it cheaply.
- **What is out of scope**, when the ticket's wording invites more than it asks
  for.

## Verify the ground before planning on it

Read the code you intend to change before describing how it will change. A plan
built on an assumed interface produces a spec built on the same assumption, and
the error is not caught until implementation — several stages later, with the
cost of every stage in between already paid.

Where the ticket's acceptance criteria are missing or vague, say so in the plan
rather than inventing criteria. Invented criteria are indistinguishable from
real ones by the time they reach test design, and they steer everything after.

## Do not implement

No code, no tests, no fixes — not even a small one you notice in passing. Note
it in the plan and leave it. The pipeline's later stages exist to do that work
under gates this stage does not run.
