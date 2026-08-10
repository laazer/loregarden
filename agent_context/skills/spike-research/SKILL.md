---
name: spike-research
description: Research lens for a spike — establish what is already known and what the real code does, without deciding the question. Gathers the evidence the experiment stage will test and the decision stage will weigh.
---

# Spike research — what is known, before anything is tried

This ticket asks a question whose answer is not yet known. Your stage does not
answer it. Your stage establishes what is already known, what the code actually
does today, and what the one experiment worth running would be.

## The question is the boundary

A spike ticket names one question. Everything you gather either bears on that
question or it does not belong in the artifact. The failure mode is a research
stage that returns a tour of the subsystem — accurate, expensive, and no closer
to a verdict.

If the ticket names secondary findings to capture in passing, capture them, but
never at the cost of the primary question.

## Prior work first

Someone may have already answered this, or answered half of it:

- `loregarden_search_prior_work` and `loregarden_search_memory` for earlier
  spikes, learnings, and checkpoints on the same surface.
- The ticket's `depends_on` and `related` edges — a sibling ticket's description
  often carries the constraint that makes the question hard.

Prior work that contradicts the ticket's premise is the most valuable thing you
can return. Say so plainly rather than working around it.

## Then the code, not the docs about the code

This repo will let you conclude something false from a plausible-looking read.
An external vendor's documentation describes what they intend; the module in
this repo describes what will actually happen. When they disagree, the code
wins, and the disagreement itself is a finding.

Read narrowly — the modules the ticket names and what they call, not the tree.

## Mark what you could not establish

Every claim you return carries one of three labels, and a reader must be able to
tell them apart:

- **Verified** — you read the code or ran the command; name the file and line,
  or the command and its output.
- **Documented** — a source says so and you did not check it; name the source.
- **Unknown** — you could not establish it.

An unknown that blocks the question is the most useful thing in your artifact,
because it is precisely what the experiment stage exists to resolve. Confidence
without certainty does more damage here than an admitted gap.

## Deliver

Attach the research:

```
loregarden_attach_artifact(kind="research", title="Research — <ticket title>", content={...})
```

Include what is already known and from where, what the code does today with file
references, the claims split into verified / documented / unknown, and the
single experiment that would most cheaply resolve the question. Name what you
looked for and did not find — a later stage will otherwise spend a run looking
again.

Do not decide the question, and do not write an implementation. If the answer
turns out to be trivially available, say so and say how you know; the experiment
stage can then be short rather than ceremonial.
