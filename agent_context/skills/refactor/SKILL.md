---
name: refactor
description: Method for changing structure without changing behavior — settle intent before editing, find every reference before moving anything, re-run the tests after each step.
---

# Refactor — change the shape, keep the behavior

A refactor that changes behavior is a bug with a tidy diff. Everything below
exists to make that outcome hard to reach by accident.

## 1. Establish intent before editing

A refactor ticket often names a direction but not a destination ("clean up the
orchestration service"). Do **not** pick one and start moving code.

State the concrete end state in one sentence — what moves where, and what stays.
If the ticket does not pin it down, record the interpretation you chose with
`loregarden_append_checkpoint` before you edit, so the next stage can see the
decision rather than reverse-engineering it from the diff. If two readings would
produce materially different code, ask rather than guess.

## 2. Capture the behavior you must preserve

Run the relevant test command **first** and keep the output. That baseline is
what "unchanged behavior" is measured against; without it, a failure you
inherited is indistinguishable from one you caused.

If the code you are about to move has no test covering it, write one that passes
against the *current* implementation first. A refactor with no test underneath it
is not verifiable.

## 3. Find every reference before moving anything

Read the repository map in this prompt for where the subsystem lives, then search
for callers. Grep is the tool available here — it is textual, so it reliably
misses:

- **string-keyed lookups** — registry dicts, `getattr`, config values naming a
  function or class
- **re-exports** — a symbol pulled through an `__init__.py` or an index module
  under a different name
- **tests and fixtures** — often the largest group of callers, easy to skip when
  searching only source directories
- **non-code references** — migrations, YAML, prompts, docs naming the symbol

Search for the bare name across the whole repo, not just the module you are in.

## 4. One step at a time, tests after each

Move one thing, run the tests, then the next. A batch of five moves that ends red
tells you nothing about which one broke it — that is the whole reason the loop is
small.

Never mix a refactor with a behavior change in the same step. If you find a bug
while restructuring, note it and leave it; fixing it inside a refactor hides it
from review and destroys the "tests unchanged" signal.

## 5. Done means

- The same tests pass as before, and you can name the baseline they are being
  compared against.
- No test was edited to accommodate the new structure. Changing a test to make a
  refactor pass converts it from evidence into decoration — if a test genuinely
  must move, say so explicitly and explain why the assertion is unchanged.
- The change is structural end to end: nothing new is done, and nothing that was
  done before has stopped.
