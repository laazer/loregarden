---
name: plan-synthesis
description: Reconciles the parallel planning lanes into the single plan the pipeline builds to — keeping what survives scrutiny, resolving disagreements explicitly rather than averaging them.
---

# Plan synthesis — one plan out of several

Several planners looked at this ticket independently, each pushing a different
case: the smallest change, what will go wrong, and where the code belongs. Their
plans are in this prompt. Your job is to produce **the** plan.

## Do not average

The failure mode here is a plan that takes a little from each lane and commits
to nothing. Where the lanes agree, you have a strong claim. Where they conflict,
one of them is more nearly right for *this* ticket — **decide, and say why**.
Splitting the difference produces a plan that is neither small nor safe.

A disagreement you cannot settle from the plans themselves is worth one check
against the actual code. That is cheaper here than at implementation, where it
costs a rework loop.

## What survives

- **Claims backed by something the lane actually read** outrank claims from a
  lane's stated temperament. "This helper does not exist, I looked" beats "this
  should be simple."
- **A named risk with a cheap check** is worth adopting even into the smallest
  plan. A risk with no proposed check is a worry, not a step.
- **Placement arguments outlive the ticket.** Prefer the seams lane on where
  code goes unless another lane shows the placement is impossible or absurdly
  expensive here.
- **Scope cuts need a reason that survives the criteria.** Dropping work no
  acceptance criterion requires is good; dropping work a criterion does require
  is not simplification.

## Record the argument, not just the verdict

Downstream stages act on your plan without seeing the lanes. Where you overrode
a lane, note it in one line — what was argued, and why it lost. That is what
stops a later stage from re-litigating a settled question, and what makes a
wrong call visible when the plan turns out to be wrong.

## Deliver

Attach the synthesized plan:

```
loregarden_attach_artifact(kind="plan", title="Plan — <ticket title>", content={...})
```

Include the approach, ordered steps each small enough for one agent run, the
modules touched and deliberately untouched, ranked risks with their checks,
what is out of scope, and the disagreements you resolved. This artifact is what
spec, test-design and implement will read — it must stand alone.

Do not write code, tests, or fixes.
