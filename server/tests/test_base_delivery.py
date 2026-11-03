"""A done ticket says whether its landed work is on the base, squash publishes included.

`done` means the work landed, usually on the tree's integration branch; the base
gets it only when the tree publishes and its PR merges. 679 read done for three
weeks with its tests on an unpublished branch.
"""

from __future__ import annotations

import subprocess
from unittest import mock

import pytest
from loregarden.models.domain import Delivery
from loregarden.services import git_automation
from loregarden.services.base_delivery import delivery_of, record_base_holdings
from loregarden.services.land_ticket import land_ticket
from loregarden.services.publish_tree import PublishOutcome
from loregarden.services.reconciliation import PERIODIC_STEPS
from loregarden.services.tree_publish_scheduler import publish_and_settle, report_publish_failure
from sqlmodel import Session
from tests.tree_publish_helpers import (
    clear_process_state,
    fake_gh,
    landed_tree,
    make_workspace,
    repo_with_remote,
    squash_onto_remote_main,
)
from tests.worktree_helpers import commit_on, git, make_tree_ticket


@pytest.fixture(autouse=True)
def _fresh_process_state():
    clear_process_state()
    yield
    clear_process_state()


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return repo_with_remote(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    return make_workspace(session, repo, "base-delivery")


def test_nothing_landed_says_so(session, workspace):
    ticket = make_tree_ticket(session, workspace, "solo")

    assert delivery_of(session, ticket).state is Delivery.NOT_LANDED


def test_a_landing_the_base_lacks_reads_not_on_the_base(session, workspace, repo):
    ms, child, branch = landed_tree(session, workspace, repo)

    assert record_base_holdings(session) == 0
    view = delivery_of(session, child)

    assert view.state is Delivery.LANDED_NOT_ON_BASE
    assert view.branch == branch
    assert (view.root_id, view.root_ref, view.base) == (ms.id, ms.external_id, "main")
    assert view.publish_card_id == ""


def test_the_roots_publish_card_is_named(session, workspace, repo):
    ms, child, branch = landed_tree(session, workspace, repo)
    card = report_publish_failure(
        session,
        ms,
        PublishOutcome(ok=False, branch=branch, detail="push denied", failed_step="push"),
    )

    view = delivery_of(session, child)

    assert view.publish_card_id == card.id
    assert "push denied" in view.publish_failure


def test_a_squash_merged_publish_is_recorded_for_every_ticket_in_the_tree(session, workspace, repo):
    """Ancestry cannot see a squash, and a child's own landing commit rarely can either."""
    ms, child, _ = landed_tree(session, workspace, repo)
    with mock.patch.object(git_automation, "_gh", side_effect=fake_gh()):
        assert publish_and_settle(session, ms, workspace).ok
    squash_onto_remote_main(repo)

    assert record_base_holdings(session) == 2

    session.refresh(child)
    session.refresh(ms)
    assert child.on_base_sha and ms.on_base_sha
    assert delivery_of(session, child).state is Delivery.ON_BASE
    assert delivery_of(session, child).on_base_sha == child.on_base_sha


def test_a_child_landed_before_the_branch_was_reset_is_still_held(session, workspace, repo):
    """After the base holds the branch the next cut moves it to the base tip, and the
    child's landing commit is in no branch's history any more, so git may collect it."""
    _, child, branch = landed_tree(session, workspace, repo)
    squash_onto_remote_main(repo)
    git(repo, "branch", "-f", branch, "main")
    git(repo, "branch", "-D", child.branch)
    git(repo, "reflog", "expire", "--expire=now", "--all")
    git(repo, "gc", "-q", "--prune=now")
    with pytest.raises(subprocess.CalledProcessError):
        git(repo, "cat-file", "-e", child.landed_sha)

    assert record_base_holdings(session) >= 1

    session.refresh(child)
    assert child.on_base_sha


def test_a_later_landing_reads_not_on_the_base_until_its_publish_merges(session, workspace, repo):
    ms, first, branch = landed_tree(session, workspace, repo)
    squash_onto_remote_main(repo)
    record_base_holdings(session)
    session.refresh(first)
    assert first.on_base_sha

    second = make_tree_ticket(session, workspace, "ms-c2", parent=ms)
    git(repo, "branch", second.branch, branch)
    commit_on(repo, second.branch, "c2.txt", "second\n")
    assert land_ticket(session, second, workspace).ok
    session.refresh(second)

    record_base_holdings(session)
    session.refresh(second)
    assert delivery_of(session, second).state is Delivery.LANDED_NOT_ON_BASE
    assert delivery_of(session, first).state is Delivery.ON_BASE, "recorded once, never re-asked"


def test_a_ticket_with_no_tree_is_asked_on_its_own(session, workspace, repo):
    ticket = make_tree_ticket(session, workspace, "solo")
    tip = commit_on(repo, "main", "solo.txt", "solo\n")
    git(repo, "push", "-q", "origin", "main")
    ticket.landed_branch = ticket.branch
    ticket.landed_sha = tip
    session.add(ticket)
    session.commit()

    assert record_base_holdings(session) == 1
    session.refresh(ticket)
    assert ticket.on_base_sha == tip


def test_it_is_a_reconcile_step():
    assert "record_base_holdings" in [step.name for step in PERIODIC_STEPS]
