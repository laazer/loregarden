"""`working_tree_paths` must return None when it cannot look, never raise.

`lg-workflow-integrity-713`. The function documents:

    "Returns None when git could not answer - NOT an empty set. The two are
     different facts ... a diagnostic that cannot tell 'the check passed' from
     'the check could not run' reports success it never had."

It honoured only half of that. It checked `returncode`, which covers a git that
RAN and failed, and missed the case where git never ran at all: `run_git` passes
`cwd` straight to `subprocess.run`, which raises FileNotFoundError when the
directory is gone.

The severity is not the unrecorded paths. `record_run_evidence` is called at
cli.py:301 with no try/except, and `complete_run` is the next statement — so a
removed worktree abandoned the run mid-completion and left it RUNNING with
nothing behind it, which is the orphan class lg-workflow-integrity-697 handles
after the fact.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from loregarden.services.git_commit_push_service import (
    paths_committed_since,
    working_tree_paths,
)


def test_a_missing_repo_root_reads_as_cannot_say(tmp_path: Path):
    """The reported defect: this raised FileNotFoundError."""
    gone = tmp_path / "worktree-that-was-removed"
    assert working_tree_paths(gone) is None
    assert paths_committed_since(gone, "HEAD") is None


def test_a_path_that_is_not_a_directory_reads_as_cannot_say(tmp_path: Path):
    """NotADirectoryError is another way of not being able to look, and the
    caller cannot tell it from an empty tree either."""
    not_a_dir = tmp_path / "a-file"
    not_a_dir.write_text("not a repo\n")
    assert working_tree_paths(not_a_dir) is None
    assert paths_committed_since(not_a_dir, "HEAD") is None


def test_a_real_repo_still_answers(git_repo: Path):
    """The other half: the guard must not turn every reading into None."""
    assert working_tree_paths(git_repo) == set()
    (git_repo / "new.txt").write_text("hello\n")
    assert working_tree_paths(git_repo) == {"new.txt"}


def test_cannot_say_is_distinct_from_touched_nothing(git_repo: Path, tmp_path: Path):
    """The distinction the whole function exists to preserve, asserted as one
    fact rather than two — `None` and `set()` must not be interchangeable."""
    could_not_look = working_tree_paths(tmp_path / "gone")
    looked_found_nothing = working_tree_paths(git_repo)

    assert could_not_look is None
    assert looked_found_nothing == set()
    assert could_not_look is not looked_found_nothing


def test_a_git_that_runs_and_fails_still_reads_as_cannot_say(tmp_path: Path):
    """The half that already worked, pinned alongside the half that did not, so
    a later refactor cannot fix one by breaking the other."""
    failed = mock.Mock(returncode=128, stdout="", stderr="fatal: not a git repository")
    with mock.patch("loregarden.services.git_commit_push_service.run_git", return_value=failed):
        assert working_tree_paths(tmp_path) is None
        assert paths_committed_since(tmp_path, "HEAD") is None


def test_an_os_error_from_git_itself_reads_as_cannot_say(tmp_path: Path):
    """Whatever the OS refuses — a missing binary, a permission denial — is a
    reading that could not be made, not an empty one."""
    with mock.patch(
        "loregarden.services.git_commit_push_service.run_git",
        side_effect=PermissionError("git not executable"),
    ):
        assert working_tree_paths(tmp_path) is None
        assert paths_committed_since(tmp_path, "HEAD") is None


def test_a_missing_repo_root_does_not_abandon_the_run(db_session, tmp_path: Path):
    """AC3, and the reason this ticket is a bug rather than a tidy-up.

    `record_run_evidence` is called from cli.py with no try/except, and
    `complete_run` is the very next statement. While this raised, a removed
    worktree did not degrade to "changed paths not recorded" — it aborted the
    completion and left the run RUNNING with nothing behind it.

    Asserted at the function cli.py actually calls, so the test fails for the
    reason the defect existed rather than pinning the shape of the call site.
    """
    from loregarden.agents.cli_adapters import CliInvocation
    from loregarden.agents.executors.run_evidence import record_run_evidence
    from loregarden.services.seed import seed_database
    from tests.factories import make_agent_run, make_workspace_ticket

    seed_database(db_session)
    ticket = make_workspace_ticket(db_session, "evidence-713")
    run = make_agent_run(
        db_session,
        workspace_id=ticket.workspace_id,
        ticket_id=ticket.id,
        run_code="run_713",
    )

    record_run_evidence(
        db_session,
        run,
        repo_root=tmp_path / "worktree-that-was-removed",
        paths_before=set(),
        stdout="",
        invocation=CliInvocation(argv=["claude"], adapter="claude"),
    )

    db_session.refresh(run)
    assert run.changed_paths_recorded_at is None, (
        "a reading that could not be made must leave no record, not an empty one"
    )
