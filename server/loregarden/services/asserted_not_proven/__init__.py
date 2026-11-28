"""Asserted-not-proven: a claim we believe without proof, as a recorded state.

A recorded state distinct from true and from unrecorded, with the artifact it
is OWED named as a resolvable pointer and the fallback NAMED and explicitly NOT
BUILT. Recording one is the normal path, not a failure to file.

Four modules, each with one job:

- `schemas` — the record and every refusal at write time.
- `record` — record / amend / measure / close, idempotent on `(ticket, claim)`,
  with the dependent-document obligation that RAISES.
- `listing` — the enumeration: the one operator-readable surface, merging
  stored rows with `ceiling:` markers scanned out of the tree.
- `tree_section` — the `## Asserted-not-proven` section of the tree's PR.

Nothing here registers a reconcile invariant. An invariant asserts a state
should not exist and repairs it; an open asserted-not-proven claim is a state
we WANT to exist and want to see, so registering one would file a finding
against every legitimate member.
"""
