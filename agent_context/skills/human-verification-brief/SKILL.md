---
name: human-verification-brief
description: Turn an acceptance criterion only a human can close into a brief they can act on — the expected observation derived from the source data, numbered checks that can each fail, and the failure signature for each. Use before asking a person to look at anything.
---

# Human verification brief — hand over an expectation, not a question

Some acceptance criteria can only be closed by a person: a rendered scene, a
feel, a device you cannot drive. Asking that person **"does this look right?"**
moves the whole problem onto them. They have to reconstruct what right *is* —
from a fixture file, a mapping module and a camera default they have not read —
before they can answer. That reconstruction is work you already have the context
to do, and every round of "what am I looking at?" is a stage that produced no
evidence.

Do the derivation first. Hand over a claim the human can falsify in one look.

## Derive the expectation from the source of truth

Compute what the surface **should** show, from the data and the code that maps
it, and cite where each number came from (`file:line`). Not "a creature made of
parts" — the parts, their sizes, their world positions, their colours, composed
through whatever transforms apply.

Present it as a table of observables, one row per thing the human can see:

| Part | Kind | Size | World centre | Extent | Colour |
|------|------|------|--------------|--------|--------|

When the thing is spatial, add a rough sketch to scale — ASCII is enough. A
sketch converts the table into something a person can match against a screen at
a glance, and it is the cheapest way to expose a disagreement between what you
computed and what they see.

If you cannot derive the expectation, say so and stop. An underivable
expectation means the fixture is underspecified, which is itself the finding.

## Every check must be able to fail

Write the checks as a numbered list, and make each one **discriminating**: there
must be an observation that fails it. A check both a correct and a broken build
would pass costs the human a look and buys nothing.

- Weak: "the creature renders." A single misplaced part renders too.
- Strong: "the green appears exactly once, only at the cone tip."
- Strong: "the collar between capsule and cone is visibly thinner than the
  capsule" — it pins a radius the human can judge without a measurement.

Prefer checks on relationships (this is above that, this is thinner, this is
behind) over absolute values nobody can eyeball.

## Name the failure signature

For each check, say what a failure would mean — the diagnosis, not just the
symptom. That is what turns a "no" into a bug report you can act on without a
second round:

- Green anywhere but the tip → material slots misresolved.
- A part at its local offset instead of its composed one → the parent hierarchy
  is not composing.
- Cone point-down → geometry orientation wrong.

## Say which checks a static view cannot settle

Call out the checks that need an interaction — orbit, hover, scroll, resize, a
second session — and say exactly which one. In the fixture that prompted this
skill, one part sat directly behind another from the default camera: from the
shipped view it was invisible, and "I don't see it" was the correct observation
for both a working build and a broken one. Naming the orbit was the difference
between an answer and a wasted round.

## Explain the surprises before they are filed as bugs

Anything correct-but-alarming gets its own line, ahead of the checks: a sphere
half below the grid because its centre is the origin, two parts intersecting by
design, a deliberately empty state. Otherwise the human reports it, and you
spend a round explaining that the thing they found is intended.

## Close the loop

End with what a "yes" actually confirms — the AC ids, by number — and what you
will do with it. When the answer comes back, record it as evidence
(`loregarden_attach_evidence`, `evidence_kind: "real_surface"`) with the checks
as they were confirmed, so the next reader sees what was observed rather than
that someone approved. A "yes" that leaves no trace is an unverified AC with a
tick next to it.
