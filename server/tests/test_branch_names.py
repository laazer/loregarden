"""The branch-name vocabulary, in the leaf module that now owns it.

lg-external-harness-1452, placement 1. `git_branch` could not import the
receipt gate at module level: `git_branch -> worktree_lifecycle` closed a
cycle through `worktree_receipt`, `worktree_service` and `target_branch`, and
every one of those three imported `git_branch` for a name helper and nothing
else. Moving the vocabulary into a leaf that imports only `re` and
`models.domain` cuts the edge permanently.

The move is tested here rather than in `test_git_branch.py` because the
compatibility posture is `internal`: NO re-export is left behind, so these
names are not reachable from `git_branch` any more and a test that imported
them from there would be testing a shim the ticket forbids.
"""

from __future__ import annotations

import pytest
from loregarden.models.domain import BaxterChatSession, Ticket, WorkItemType


def _helpers():
    """The module under test, resolved per call rather than at import.

    A module-level `from loregarden.services import branch_names` would make a
    tree that has not grown the module yet abort the whole run's collection
    instead of failing these tests, which is a worse signal for every other
    suite that runs beside them.
    """
    from loregarden.services import branch_names

    return branch_names


def test_default_ticket_branch():
    ticket = Ticket(
        external_id="42-my-feature", work_item_type=WorkItemType.TASK, title="x", workspace_id="w"
    )
    assert _helpers().default_ticket_branch(ticket) == "loregarden/42-my-feature"


def test_default_ticket_branch_uses_milestone_prefix():
    ticket = Ticket(
        external_id="42-my-feature",
        milestone="Q3 Launch",
        work_item_type=WorkItemType.TASK,
        title="x",
        workspace_id="w",
    )
    assert _helpers().default_ticket_branch(ticket) == "q3-launch/42-my-feature"


def test_resolve_ticket_branch_prefers_explicit():
    ticket = Ticket(
        external_id="42-my-feature",
        branch="custom/branch",
        work_item_type=WorkItemType.TASK,
        title="x",
        workspace_id="w",
    )
    assert _helpers().resolve_ticket_branch(ticket) == "custom/branch"


def test_validate_branch_name_rejects_invalid():
    with pytest.raises(ValueError):
        _helpers().validate_branch_name("bad branch name")


def test_a_chat_session_branch_is_derived_from_its_id_not_its_title():
    """A thread can be renamed; the id half is what keeps its commits findable.

    The oracle here was full equality of the whole branch name, which the
    shipped helper has never satisfied and which
    `test_chat_worktree_sandbox.py::test_branch_is_derived_from_the_thread_id_not_its_title`
    already contradicts: the title decorates the branch so a thread is
    recognisable in `git branch`, and a rename redecorates it. This ticket is
    a MOVE of the vocabulary, not a redesign of it, so the test is the thing
    that was wrong (lg-external-harness-1452, implement).
    """
    session = BaxterChatSession(title="Planning the thing")
    first = _helpers().chat_session_branch(session)
    session.title = "Renamed by the operator"

    assert first.startswith("chat/")
    assert session.id[:8] in first
    assert session.id[:8] in _helpers().chat_session_branch(session)


def test_git_branch_leaves_no_re_export_behind():
    """AC1. `internal` posture: every caller is migrated in this change.

    A re-export would keep the cycle alive at import time, which is the whole
    reason the move exists — and a shim that lets a caller stay put is exactly
    what the posture forbids.
    """
    from loregarden.services import git_branch

    for name in (
        "_BRANCH_RE",
        "_SLUG_RE",
        "_slugify",
        "default_ticket_branch",
        "chat_session_branch",
        "resolve_ticket_branch",
        "validate_branch_name",
    ):
        assert not hasattr(git_branch, name), f"git_branch still re-exports {name}"
        assert hasattr(_helpers(), name), f"branch_names is missing {name}"


def test_branch_names_imports_nothing_that_could_close_the_cycle():
    """A leaf, and provably one: it may reach `re` and the domain models and
    nothing else in `services`."""
    import ast
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "loregarden" / "services" / "branch_names.py"
    ).read_text(encoding="utf-8")
    # Both statement forms. `ast.ImportFrom` alone misses
    # `import loregarden.services.worktree_service`, which closes the cycle
    # exactly as well as the `from` spelling does and is the spelling a later
    # edit reaches for when the `from` one is the only one under test.
    # lg-external-harness-1452, test-break.
    reached: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            reached.add(node.module)
        elif isinstance(node, ast.Import):
            reached.update(alias.name for alias in node.names)
    assert not [m for m in reached if m.startswith("loregarden.services")], reached
    assert reached <= {"re", "loregarden.models.domain"} | {
        m for m in reached if m.startswith("loregarden.models")
    }, f"a leaf reaches only `re` and the domain models; it also reaches {reached}"
