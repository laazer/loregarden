"""A capacity lease's free-text label, read back as what / where / pid.

`capacity-run.sh` labels a lease `<what> · <worktree>@<branch>` and
`capacity_run` appends ` · pid N`. Worktrees here are named after their branch,
so the old form said the same thing twice:

    pre-push client-tests · lg-x-e33a13@claude/lg-x-e33a13 · pid 22339

The wrapper now writes the branch alone when the worktree is its suffix, but
leases taken by an older checkout keep the long form until they end, and other
callers write labels with no place at all. So this reads every shape back the
same way, and a label it cannot split stays whole in `what` — never dropped.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

_SEPARATOR = " · "
_PID = re.compile(r"^pid (\d+)$")
_WORD = re.compile(r"[a-z_-]+")


@dataclass(frozen=True)
class HolderLabel:
    #: What is running: "pre-push client-tests".
    what: str
    #: The branch it runs for, when the label names one.
    branch: str | None
    #: The worktree, only when it says something the branch does not.
    worktree: str | None
    pid: int | None

    def as_dict(self) -> dict:
        return asdict(self)


def _is_place(segment: str) -> bool:
    """A `repo@branch` or a bare branch/repo name: one token, no spaces."""
    return bool(segment) and " " not in segment


def _split_place(place: str) -> tuple[str | None, str | None]:
    """(branch, worktree) from `worktree@branch`, `branch`, or `worktree`."""
    if "@" in place:
        worktree, branch = place.split("@", 1)
        if branch.rsplit("/", 1)[-1] == worktree:
            return branch, None
        return branch, worktree
    if "/" in place:
        return place, None
    return None, place


def parse_holder_label(label: str, *, pid: int | None = None) -> HolderLabel:
    segments = label.split(_SEPARATOR)
    found_pid = pid
    if len(segments) > 1 and (match := _PID.match(segments[-1])):
        segments = segments[:-1]
        found_pid = pid if pid is not None else int(match.group(1))
    branch: str | None = None
    worktree: str | None = None
    if len(segments) > 1 and _is_place(segments[-1]):
        branch, worktree = _split_place(segments[-1])
        segments = segments[:-1]
    return HolderLabel(
        what=_SEPARATOR.join(segments),
        branch=branch,
        worktree=worktree,
        pid=found_pid,
    )


def label_words(label: str) -> tuple[str, ...]:
    """What a label says is running, as words to match it to other labels by.

    Numbers are dropped, so "dawnbringer pre-push 1376" and "… 1378" are the same
    work. Where it runs and which process are not part of what it is.
    """
    return tuple(_WORD.findall(parse_holder_label(label).what.lower()))
