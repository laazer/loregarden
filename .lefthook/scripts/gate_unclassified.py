#!/usr/bin/env python3
"""The words a gate uses for a construct it recognized and cannot grade.

A classifier with a closed set of arms has two honest answers for a node it was
handed — this is mine and here is its arm, or this is not mine at all — and one
dishonest one: read it as whichever neighbouring arm the fall-through reaches.
That third answer is how `_paths_through` reads a `match` as a plain statement
and reports a quiet path as observed, and how `isinstance_errors` prints
``isinstance(..., ?)`` with advice it never established. The construct is
flattened, and nothing says so.

So the answer is named instead, in one wording both Python gates import. This is
the CONSTRUCT-level sibling of `precommit_git_diff.UnexaminableFileError`, which
is the FILE-level one: there the run could not parse the file, here it parsed the
file and has no arm for something inside it. Two levels of the same refusal, each
findable from the other, so the next agent does not write a third wording for
"I could not grade this".

`registered_at` is the one field a producer cannot answer about itself — where
the classifier that reached this node is wired — so a blank one is refused at
construction. A finding that reads as provenance and carries none is worse than
one that carries nothing.

Nothing here reads the filesystem, runs git, or imports `loregarden`:
`.lefthook/scripts/` runs in every workspace the control plane drives, before
any dependency of ours exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = [
    "UNCLASSIFIED_LABEL",
    "Outcome",
    "Unclassified",
    "unclassified_message",
]

#: The criterion's own words, so a reader greps for the same string the gate printed.
UNCLASSIFIED_LABEL = "unclassified construct at"

#: The one sentence between the parts. It does not vary with the construct: a
#: wording that moved with its subject would make "both surfaces say the same
#: thing" unmeasurable, which is what the shared module exists to make checkable.
_WORDING = (
    "this classifier recognized the construct and has no arm for it, so it is named "
    "here rather than graded as a neighbouring arm"
)


class Outcome(Enum):
    """The three answers a classifier in scope may return, named as such.

    Collapsing NOT_APPLICABLE into UNCLASSIFIED makes a gate fire on ordinary
    code (a nested `def` inside a handler is not the subject of the handler-body
    walk, and never a missing arm). Collapsing UNCLASSIFIED into NOT_APPLICABLE
    reproduces the defect this module answers, under a new name. Both collapses
    are defects, which is why the two are distinct values rather than one
    "nothing to say" sentinel.
    """

    CLASSIFIED = "classified"  # a named arm fired
    NOT_APPLICABLE = "not_applicable"  # the node is not this classifier's subject
    UNCLASSIFIED = "unclassified"  # it IS the subject, and no arm names it


@dataclass(frozen=True)
class Unclassified:
    """One construct a classifier recognized as its subject and could not grade.

    `path`/`line` locate the construct itself, which is not necessarily where
    the finding is anchored: `py_silent_except_check` anchors at the enclosing
    handler, the line its diff-scope filter already admits, and names the
    construct's own site in the message.
    """

    construct: str
    path: str
    line: int
    inferred_by: str
    registered_at: str

    def __post_init__(self) -> None:
        if not self.registered_at.strip():
            raise ValueError(
                "registered_at must name where the classifier is wired; a finding that "
                "cannot say reads as provenance and carries none"
            )


def unclassified_message(item: Unclassified) -> str:
    """The one line every caller prints, in the one shape both gates are compared on.

    `<label> <path>:<line>: <construct>; <wording> (inferred_by=…, registered_at=…)`
    """
    return (
        f"{UNCLASSIFIED_LABEL} {item.path}:{item.line}: {item.construct}; {_WORDING} "
        f"(inferred_by={item.inferred_by}, registered_at={item.registered_at})"
    )
