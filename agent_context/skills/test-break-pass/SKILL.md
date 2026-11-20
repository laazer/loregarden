---
name: test-break-pass
description: After designing the tests, the same session turns adversary and attacks them, adding the edge-case, mutation and mock-honesty tests a separate test-break stage used to add. Replaces the test-break stage.
---

# Test design, then break your own tests

This stage used to be two: one agent wrote the tests, and a second started cold,
re-read the spec, the plan and those tests, and attacked them. You do both, so
the second half starts with everything the first half learned.

The risk of doing it alone is grading your own work charitably. The second pass
is not a review of the first; it is an attempt to prove it wrong.

## 1. Design

Write the behavioral tests your role describes: every acceptance criterion
verifiable, standard behavior, edges and failure states, deterministic.

## 2. Break

Switch sides. Assume the tests from step 1 pass against a wrong implementation,
and find that implementation.

- **Mutate.** For each important assertion, name the smallest code change
  (flipped boolean, off-by-one, skipped branch, swallowed error) that would
  still pass it. Where one exists, add the test that kills it.
- **Walk the matrix**, keeping only what applies to this change: null and empty
  inputs; boundaries (0, -1, max, oversized); type and structure swaps; invalid
  or corrupt input; concurrency and ordering; combinations of edge factors;
  error handling and failing dependencies; implicit assumptions in defaults and
  invariants; determinism (same input, same result).
- **Check mock honesty.** Find tests that assert interactions instead of
  behavior, or mock so much that the real path never runs. Add an
  integration-level or real-dependency test where isolation hides risk.

Every test from this pass states, in its name or a short comment, the weakness
it exposes. Do not change expected behavior, and do not write implementation
code.

## Deliver

The tests themselves go in the repo. Spec gaps, the weaknesses you found and
any you judged not worth a test go to `loregarden_attach_artifact`, never a
markdown file. A test that cannot fail is worse than none: run each new test
once and check it fails for the reason you expect (the behavior is not built
yet), not on its own setup.
