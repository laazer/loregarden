"""Which name on the remote holds which history.

`publish_tree` pushes one squash commit cut from the base (`squash_onto`), not
the branch's history. It used to push that commit *as* the branch, so
`origin/integration/<root>` and the local `integration/<root>` were two objects
with one name: the same files, no shared history. Anything that read the
remote name as the branch merged the squash into a ticket branch, and from
then on every merge of the real branch into it conflicted, because git could
no longer see that the two sides held the same work. On 2026-10-20 a
hand-resolved merge took `origin/integration/lg-external-harness-463` into
lg-external-harness-1026's branch, and its next two checkout merges conflicted
in seven test files, each spending a 900s repair turn on a tree that already
held every change.

So a squash is pushed under its own name, ``publish/<branch>``, and that name
is the PR head. Nothing else pushes there and nothing merges from it. The
branch's own name means one thing everywhere: the branch, with its history.

An integration branch lives only here: nothing pushes its history, so its
local ref is the only one that is the branch (`comparison_ref`).

A leaf module, so the git helpers below the target-branch layer can use it.
"""

from __future__ import annotations

#: A tree's integration branch: ``integration/<root external id>``.
INTEGRATION_PREFIX = "integration/"
#: Where the commit publishing a branch is pushed, and where its PR lives.
PUBLISH_PREFIX = "publish/"


def publish_ref_for(branch: str) -> str:
    """The remote branch that carries ``branch``'s publish commit."""
    return f"{PUBLISH_PREFIX}{branch}"


def branch_for_pr_head(head: str) -> str:
    """The local branch a PR whose head is ``head`` publishes.

    A head outside ``publish/`` is its own branch: a chat branch pushed with
    its history, or a tree PR opened before publishing moved to its own name.
    """
    return head.removeprefix(PUBLISH_PREFIX)


def comparison_ref(branch: str) -> str:
    """The ref that is ``branch`` when comparing a checkout against it.

    The remote-tracking ref for a branch the remote holds the history of; the
    local ref for an integration branch, which the remote never holds. A stale
    ``origin/integration/…`` left from before the rename is a squash, and
    merging it is the failure this module exists to prevent.
    """
    if branch.startswith(INTEGRATION_PREFIX):
        return branch
    return f"origin/{branch}"
