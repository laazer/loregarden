---
name: plan-lenses
description: One planner argues the ticket three ways (the smallest change, what will go wrong, where the code belongs) and then commits to a single plan. Replaces three parallel planning lanes plus a synthesis stage.
---

# Plan — three cases, one decision

This stage used to run three planners in parallel and a fourth to reconcile
them. Each started cold, read the same code, and the reconciler only saw their
summaries. You do all four jobs in one session, so the code you read for one
lens is already in front of you for the next.

The risk of doing it alone is averaging: a plan that takes a little from each
case and commits to nothing. Avoid it by arguing each case properly before
deciding.

## 1. Read the ground

Read the code you would change before arguing anything. Every lens below is
worthless on an imagined interface.

## 2. Argue each case, for real

Write each one as its advocate would. Keep them short, but do not pre-compromise.

- **Smallest.** The smallest change that satisfies the acceptance criteria as
  written. Which parts of the obvious approach does no criterion require? What
  existing seam could be used instead of building one? Can it touch one module
  instead of three? State what this version gives up.
- **Risk.** Start from the failure. Which assumption, if false, wastes the most
  work, and how is it checked *before* work depends on it? What does this touch
  that nobody mentioned (callers, string-keyed lookups, migrations, prompts,
  tests pinning current behavior)? Where would it silently half-work? What is
  hard to undo? Rank the risks by expected cost; name the ones not worth
  guarding against. Verify what you can instead of listing hypotheticals.
- **Seams.** Where does the code go? Name the module, or say in one sentence
  what a new one is *for*. What does it couple to? Is this a second answer to a
  question answered elsewhere? What is the smallest interface between new and
  old code, and can it be tested alone? Follow a pattern that already exists
  over a better one that would make two conventions.

## 3. Decide

- Where the cases agree, that is a strong claim.
- Where they conflict, one is more nearly right for *this* ticket: **decide, and
  say why** in one line. One check against the code beats a guess.
- Claims backed by code you read outrank temperament. A named risk with a cheap
  check goes into even the smallest plan. Prefer the seams case on placement
  unless it is impossible or absurdly expensive here. A scope cut needs a reason
  that survives the criteria.

## Deliver

Attach one plan, once, when it is final:

```
loregarden_attach_artifact(kind="plan", title="Plan — <ticket title>", content={...})
```

It is what ui-design, spec, test-design and implement read, and they act on it
without asking you anything, so it must stand alone: the approach, ordered
steps each small enough for one agent run, modules touched and deliberately
untouched, ranked risks with their checks, what is out of scope, and the
disagreements between the cases you resolved and why. Where the acceptance
criteria are missing or vague, say so rather than inventing criteria.

Do not write code, tests, or fixes.
