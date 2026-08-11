---
name: spike-decision
description: Decision lens for a spike — turn the research and the experiment into a verdict the dependent tickets can act on, including what the verdict costs and what would overturn it.
---

# Spike decision — a verdict, not a survey

The research and the experiment are in this prompt. Your job is to answer the
question the ticket asked, in a form the tickets that depend on this one can act
on without re-running the investigation.

## Answer the question that was asked

A spike is finished when someone can read one line and know what to build. The
failure mode is a balanced write-up of considerations that leaves the next
ticket exactly as blocked as before. If the honest answer is "it depends,"
name what it depends on and answer for the case this repo is actually in.

Answer in one of three shapes, and say which:

- **Yes** — it works; here is the mechanism and what it costs.
- **No** — it does not work; here is the evidence, and here is the alternative.
- **Not yet decidable** — the experiment did not settle it; here is precisely
  what would, and why it was not run.

The third is a legitimate outcome and a bad habit. Use it when the evidence
genuinely does not support a verdict, not when a verdict would be awkward.

## Evidence beats plausibility

Weigh what was actually run above what a document promised. An experiment that
failed for an incidental reason — a missing binary, a wrong flag — has not
answered the question, and reporting its failure as the verdict is the most
common way a spike concludes something false. Say which of the two happened.

Where the experiment contradicts the research, the experiment wins, and the
contradiction goes in the verdict; a vendor's documented behaviour that this
repo could not reproduce is a finding worth more than the verdict itself.

## Say what it costs and what would overturn it

A verdict with no cost attached will be adopted without one. Include the work
the yes implies, the constraint the no imposes, and the thing that would change
your answer — a dependency upgrade, a platform, a decision made elsewhere.
That last line is what makes a stale verdict visible later instead of quietly
wrong.

## Close the loop on the dependent tickets

The spike exists to unblock something. Update the tickets that depend on this
one with the verdict and its consequences — an acceptance criterion the verdict
invalidates should be rewritten now, by you, not discovered mid-implementation:

```
loregarden_update_ticket(ticket_id="<dependent>", description=..., acceptance_criteria=[...])
```

Read the dependent ticket first; `acceptance_criteria` replaces the stored list
by default.

## Deliver

Attach the verdict:

```
loregarden_attach_artifact(kind="decision", title="Verdict — <ticket title>", content={...})
```

Include the answer in one sentence, the evidence it rests on, what it costs,
what would overturn it, and which dependent tickets you updated. Record the
durable lesson with `loregarden_append_learning` — a spike whose finding is not
searchable will be run again.

Do not write the implementation the verdict recommends. That is the next
ticket's work, and doing it here buries a decision inside a diff.
