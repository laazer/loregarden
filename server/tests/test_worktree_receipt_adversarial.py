"""`landed_sha` is a text column, and git will resolve text.

lg-external-harness-1026, AC2/AC3. The three forgeries in
`test_worktree_receipt.py` all write something false into `landed_sha` — a sha
no ref contains, a real sha against another target, a real sha that predates
the tree's tip — and each is caught by *comparing*. This is the case where the
comparison itself answers yes.

`is_ancestor` takes a revision, not a digest: `main`, `HEAD`, `@`,
`refs/heads/main` and `main^{commit}` all resolve in a real repository, and each
resolves to a commit that reaches the target and contains the tree's tip. An
implementation that hands the column straight to git therefore answers "landed"
for a column somebody typed a branch name into. AC2 says identity *and digest*;
this is the test that makes "digest" mean something. A receipt names one
immutable object; `main` names whatever `main` is this afternoon.

Sibling modules from the same pass cover the other arms and the third conjunct
(`test_worktree_receipt_arms.py`), the release races
(`test_directory_release_races.py`) and the recorded premises
(`test_worktree_gate_premises.py`). Nothing here duplicates them.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from loregarden.models.domain import (
    Workspace,
    Worktree,
)
from loregarden.services.ticket_worktree import resolve_execution_root
from loregarden.services.worktree_receipt import Receipt, Unresolved, resolve_receipt
from sqlmodel import Session, select
from tests.worktree_helpers import git, grant_receipt, make_repo, make_run
from tests.worktree_helpers import make_ticket_on_id_branch as _ticket


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="proj", name="proj", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _tree_for(session, workspace, ticket) -> Path:
    return resolve_execution_root(
        session,
        make_run(session, workspace, ticket, f"run-{ticket.external_id}"),
        ticket,
        workspace,
    )


def _row(session, ticket) -> Worktree:
    return session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id)).one()


def _commit_in(path: Path, name: str = "work.txt", body: str = "real work\n") -> str:
    (path / name).write_text(body)
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", f"work: {name}")
    return git(path, "rev-parse", "HEAD").stdout.strip()


def _landed_tree(session, workspace):
    """A ticket-owned tree with a genuine receipt: the baseline every forgery
    below perturbs by exactly one field."""
    repo = Path(workspace.repo_path)
    ticket = _ticket(session, workspace)
    tree = _tree_for(session, workspace, ticket)
    _commit_in(tree)
    grant_receipt(session, ticket, repo)
    row = _row(session, ticket)
    assert isinstance(resolve_receipt(session, row), Receipt), (
        "fixture: the honest receipt must resolve before a forgery proves anything"
    )
    return ticket, tree, row


# ---------------------------------------------------------------------------
# AC2/AC3 — the column holds a digest, and git will resolve anything
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("revision", ["main", "HEAD", "@", "refs/heads/main", "main^{commit}"])
def test_a_revision_that_is_not_a_digest_is_not_a_receipt(session, workspace, revision: str):
    """The forgery that needs no sha at all.

    Every string here resolves in this repository, and each one resolves to a
    commit on `main` that contains the tree's tip — so an implementation that
    hands `landed_sha` to `is_ancestor` unexamined answers "landed" for a
    column somebody typed a branch name into. A receipt is a *digest*: 40 hex
    characters naming one immutable object. `main` names whatever `main` is
    this afternoon, which is the opposite of a receipt.
    """
    ticket, tree, row = _landed_tree(session, workspace)
    ticket.landed_sha = revision
    session.add(ticket)
    session.commit()

    answer = resolve_receipt(session, row)

    assert isinstance(answer, Unresolved), f"{revision!r} was accepted as a receipt"
    assert tree.is_dir()
