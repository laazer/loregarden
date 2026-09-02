"""A tree git could not read is not a clean tree.

`working_tree_paths` returned an empty set when `git status` failed, which is
the same value it returns for a genuinely clean tree. Every caller therefore
read "could not look" as "nothing is dirty".

That is the shape lg-workflow-integrity-450 fixed for gates — a diagnostic that
cannot tell "the check passed" from "the check could not run" reports success it
never had — and it is why lg-workflow-integrity-406's question was unanswerable:
`changed_paths_json` records what a run touched, and a failed read wrote "this
run changed nothing" indistinguishably from the truth.

The type checker cannot catch a missed call site here: this repo's mypy config
disables the `operator` and `union-attr` error codes, so `None - set` typechecks
clean. These tests are the safety net instead.
"""

from pathlib import Path
from unittest import mock

from loregarden.services import evidence as evidence_module
from loregarden.services.git_commit_push_service import working_tree_paths
from tests.worktree_helpers import make_repo


def test_a_readable_tree_answers_its_dirty_paths(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "dirty.txt").write_text("x", encoding="utf-8")

    assert working_tree_paths(repo) == {"dirty.txt"}


def test_a_clean_tree_answers_an_empty_set(tmp_path):
    repo = make_repo(tmp_path)

    assert working_tree_paths(repo) == set()


def test_a_tree_git_cannot_read_answers_none_not_empty(tmp_path):
    """The distinction the whole change exists for."""
    not_a_repo = tmp_path / "somewhere-else"
    not_a_repo.mkdir()

    assert working_tree_paths(not_a_repo) is None


def test_evidence_does_not_treat_an_unreadable_tree_as_clean(tmp_path):
    """The sharpest consequence.

    Current evidence is keyed to HEAD and is only valid while nothing is dirty —
    an uncommitted edit leaves HEAD unchanged while the tree a downstream stage
    sees is no longer the one that was proven. Reading a failed `git status` as
    "no dirty paths" would hand back evidence for a tree nobody inspected.
    """
    repo = make_repo(tmp_path)

    with (
        mock.patch.object(evidence_module, "working_tree_paths", return_value=None),
        mock.patch.object(evidence_module, "resolve_head_sha", return_value="abc123"),
        mock.patch.object(evidence_module, "evidence_for_commit", return_value=[]) as looked_up,
    ):
        kinds = evidence_module.evidence_kinds_at_head(
            mock.Mock(), mock.Mock(), repo_root=Path(repo)
        )

    assert kinds == set()
    assert not looked_up.called, (
        "an unreadable tree must short-circuit before evidence is even looked up"
    )
