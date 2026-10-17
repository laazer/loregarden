"""Resolve a stage's target branch once, lazily, and never cache a failure.

Three invariants, written down here once because their absence is what made
this a defect twice (lg-milestone-that-918, lg-milestone-that-1200). A second
copy of them would be two places to keep true, which is the shape of the bug.

**Lazy.** Building the memo resolves nothing. Resolution writes shared refs and
can block on a merge turn, and every caller has paths that return before they
need an answer — a stage with no members, a run whose worktree already exists, a
checkout too dirty to fall back onto. A factory that resolved eagerly would make
those paths write refs where today they write nothing, which is a behaviour
change wearing a refactor's clothes.

**At most once.** That is the saving: one `for-each-ref` and one refresh per
stage instead of one per place that wants the name.

**A raise is not an answer.** A failure is not cached; the next call retries.
Resolution fails transiently — the merge turn was not granted, the target could
not take its base — and a remembered failure is a stale answer with no way to
clear it for the rest of the stage.

And the invariant that is about the memo's *lifetime* rather than its body, so
it belongs to each caller: build it inside the unit of work, never on a module
global or a service object. The cached *name* would not go stale, but resolving
is the write that carries the base branch onto the integration branch, so a memo
outliving its stage makes everything that landed on the base since invisible to
the next one.
"""

from __future__ import annotations

from collections.abc import Callable


def target_branch_once(resolve: Callable[[], str]) -> Callable[[], str]:
    """`resolve`, wrapped so it runs at most once and only when first asked."""
    resolved: list[str] = []

    def once() -> str:
        if not resolved:
            # Appended only on return, so a raise leaves the memo empty and the
            # next call tries again.
            resolved.append(resolve())
        return resolved[0]

    return once
