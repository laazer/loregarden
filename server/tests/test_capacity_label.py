"""Reading a capacity lease's label back as what / where / pid.

Old labels repeated the worktree inside the branch; new ones name the branch
once; some callers name no place at all. All three must read the same way, and
a label that cannot be split must stay whole rather than lose words.
"""

from __future__ import annotations

import pytest
from loregarden.services.capacity_label import HolderLabel, parse_holder_label


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        (
            "pre-push client-tests · lg-x-e33a13@claude/lg-x-e33a13 · pid 22339",
            HolderLabel("pre-push client-tests", "claude/lg-x-e33a13", None, 22339),
        ),
        (
            "pre-push client-tests · claude/lg-x-e33a13 · pid 22339",
            HolderLabel("pre-push client-tests", "claude/lg-x-e33a13", None, 22339),
        ),
        (
            "pre-push server-tests · hungry-solomon-46dbeb@claude/adapter-21cc50 · pid 7",
            HolderLabel(
                "pre-push server-tests", "claude/adapter-21cc50", "hungry-solomon-46dbeb", 7
            ),
        ),
        (
            "loregarden: suite profile round 4 · pid 85965",
            HolderLabel("loregarden: suite profile round 4", None, None, 85965),
        ),
        ("e2e suite", HolderLabel("e2e suite", None, None, None)),
        ("", HolderLabel("", None, None, None)),
        # A place-shaped segment is only the place when something precedes it.
        ("pid 4", HolderLabel("pid 4", None, None, None)),
    ],
)
def test_every_label_shape_reads_back_the_same_way(label: str, expected: HolderLabel) -> None:
    assert parse_holder_label(label) == expected


def test_a_free_text_tail_stays_in_what() -> None:
    parsed = parse_holder_label("build · the docs site · pid 3")
    assert parsed.what == "build · the docs site"
    assert parsed.branch is None and parsed.worktree is None


def test_the_recorded_pid_wins_over_the_label() -> None:
    assert parse_holder_label("x · pid 1", pid=2).pid == 2
