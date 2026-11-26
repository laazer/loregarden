"""The phrases only a tree failure may use.

Any of them on a non-tree failure is the defect: the toast clamps the message, so
the tree sentence does not pad a non-tree diagnosis, it replaces it. Matched
case-insensitively by `_assert_no_tree_sentence` (test_handoff_failure_classification.py)
and `_assert_not_reported_as_a_tree_failure` (test_terminal_handoff_command.py).
"""

from __future__ import annotations

FORBIDDEN_TREE_PHRASES = (
    "resolve the conflict",
    "its target branch",
    "the worktree is",
    "no target branch",
    "nothing was merged",
)
