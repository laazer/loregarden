"""The frozen fragments of `describe_handoff_failure`'s four lead sentences.

This module **composes nothing**, and that is the whole of its design. It is the
oracle the other copies of the prose are checked against, and a file that both
*states* a sentence and *builds* it can be reworded in one keystroke — which is
the defect this module exists for (lg-milestone-that-1315). So there are no
composers here, no f-strings assembling a lead, no import of
`loregarden.services.handoff_tree_status` and no import of
`tests.handoff_lead_helpers`. Importing either would make the oracle agree with
whichever copy it imported, by construction.

The prose has four server-side homes:

1. **Production** — `handoff_tree_status.describe_handoff_failure` composes the
   canonical leads (class A inline at the `ProfileUnreadableError` arm, B and C
   in `_no_target_lead`, D inline in the `else` arm).
2. **`tests/handoff_lead_helpers.py`** — composes them a second time, on
   purpose (lg-milestone-that-1314 AC9): three test modules assert production's
   *output* against the helper's *composers*, so a single shared composer would
   make every one of those assertions compare the implementation against
   itself.
3. **`tests/test_handoff_failure_classification.py`** and
4. **`tests/test_terminal_handoff_command.py`** — which both retyped the
   forbidden-phrase tuple, bound to nothing.

Homes 1 and 2 are already two-way bound by those output-vs-composer
assertions, so a one-sided reword of either is red today. Homes 3 and 4 were
bound to nothing at all, and that is what this table fixes: they import from
here, and `test_the_composer_keeps_the_parts_no_criterion_changed` drives
`LEAD_FRAGMENTS` against production's **docstring-stripped** source while
`test_the_helper_composers_still_say_what_the_table_says` drives it against the
helper's composed output.

What this cannot catch, stated so no one reads it as a gap: a fully coordinated
reword of production *and* the helper *and* this table in one commit. That is a
visible diff in the review gate, not a detectable error, and no test can make
it one.

`scope` is the name of the production function the fragment must live in, and
the counts are per-function rather than module-wide on purpose. Module-wide,
`its target branch` counts 2 — `_unreadable`'s unrelated warning sentence
retypes it, and that sentence is explicitly not owned by this table — so a
module-wide table could not pin that row at an exact count without binding an
out-of-scope sentence. Per-function, every row below lives in exactly one scope
at exactly count 1, and the partition itself asserts lg-milestone-that-1237's
"class-A lead inlined at its except site with classes B/C in `_no_target_lead`"
(AC2) rather than assuming it. Measured at commit `e2a2dafe`.
"""

from __future__ import annotations

#: (key, fragment, scope, expected_count, why).
#:
#: `why` names the input class and the criterion the fragment serves, and the
#: assertion message quotes it, so a failure reads as "this sentence moved"
#: rather than as an opaque count mismatch.
LEAD_FRAGMENTS = (
    (
        "A",
        "no target branch — the workspace's orchestration profile will not parse. Fix ",
        "describe_handoff_failure",
        1,
        "Input class A, `ProfileUnreadableError` from `target_branch_name`: the lead names the "
        "profile that will not parse and nothing else (lg-milestone-that-1237 AC2).",
    ),
    (
        "B",
        "has no target branch to be brought up to, so nothing was merged",
        "_no_target_lead",
        1,
        "Input class B, an unnameable target branch: `so nothing was merged` is the fact the old "
        "sentence got wrong by implying a merge had been attempted and conflicted "
        "(lg-milestone-that-1237 AC3).",
    ),
    (
        "B2",
        "— run the stage supervised.",
        "_no_target_lead",
        1,
        "Input classes B and C: the actionable half of the no-target lead, which every extractor "
        "that split on the first `. ` used to cut off (lg-milestone-that-1237 AC3, AC13).",
    ),
    (
        "C",
        "The worktree is ",
        "_no_target_lead",
        1,
        "Input class C versus B: the worktree clause is the one thing class C omits, keyed on "
        "`repo_root is None` so it is absent exactly when there is no tree to name "
        "(lg-milestone-that-1237 AC4).",
    ),
    (
        "D",
        "resolve the conflict with ",
        "describe_handoff_failure",
        1,
        "Input class D, the read succeeded: the conflicting-refresh lead this composer was "
        "written for, byte for byte (lg-milestone-that-917 AC12, -1237 AC5).",
    ),
    (
        "D2",
        ", or run the stage supervised — ",
        "describe_handoff_failure",
        1,
        "Input class D: the second action, ahead of the long path so the toast clamp eats the "
        "path and not the action (lg-milestone-that-1237 AC5, AC10).",
    ),
    (
        "D-fallback",
        "its target branch",
        "describe_handoff_failure",
        1,
        "Input class D with an empty `target_branch`: the only lead that may name no branch, and "
        "it names none rather than substituting the built-in default "
        "(lg-milestone-that-1237 AC7).",
    ),
)

#: The phrases only a tree failure may use. Any of them on a non-tree failure is
#: the defect, because the lead sentence is the whole diagnosis: the toast
#: clamps at `max-height: 5.8em`, so the tree sentence does not pad a non-tree
#: diagnosis, it **replaces** it. Asserted as a set on every non-tree message by
#: `_assert_no_tree_sentence` (test_handoff_failure_classification.py) and
#: `_assert_not_reported_as_a_tree_failure` (test_terminal_handoff_command.py),
#: both case-insensitively — lg-milestone-that-1237's no-target lead opens its
#: second sentence with `The worktree is`, so a leak differing from the listed
#: phrase only in one capital would otherwise read as clean.
#:
#: Extended by lg-milestone-that-1237, not rewritten: that ticket replaced the
#: one tree lead with four, and the tuple — 975's guard against a tree sentence
#: leaking onto a failure that is not about the tree — was blind to the two new
#: ones, so `no target branch` would have leaked undetected.
#:
#: Moved here verbatim from the two tuples that held it (lg-milestone-that-1315
#: AC8). A phrase here that production no longer uses is a leak detector that
#: has quietly become a no-op, which is why
#: `test_the_composer_keeps_the_parts_no_criterion_changed` also asserts every
#: one of them is still present in production's docstring-stripped source.
FORBIDDEN_TREE_PHRASES = (
    "resolve the conflict",
    "its target branch",
    "the worktree is",
    "no target branch",
    "nothing was merged",
)
