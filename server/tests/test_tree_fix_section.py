"""A tree's PR carries the fix coverage section its bug tickets owe.

Every assertion on a description runs the "PR fix coverage" check's own code
against it, rooted at this checkout so the invariant and the test it names are
checked for real.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest import mock

import pytest
from loregarden.mcp.fix_coverage_tools import HANDLERS
from loregarden.models.domain import WorkItemType, Workspace
from loregarden.services import git_automation
from loregarden.services.publish_tree import publish_tree
from loregarden.services.tree_fix_section import tree_fix_section
from sqlmodel import Session
from tests.test_tree_ux_section import FakeGitHub
from tests.worktree_helpers import git, make_repo, make_tree_ticket

_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "pr_fix_coverage_check", _ROOT / ".github" / "scripts" / "pr_fix_coverage_check.py"
)
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

BRANCH = "loregarden/integration/ms"
ANSWERS = {
    "existing": "0 rows: `select count(*) from tickets where block_kind is null` on live",
    "reentry": "`every_block_is_settled` reports a block written around settle_block",
    "prevention": "server/tests/test_invariants.py::test_invariant_names_are_unique",
    "learning": "A block written outside settle_block reads as handled when nothing handled it",
}


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session, tmp_path):
    ws = Workspace(slug="tree-fix", name="tree-fix", repo_path=str(tmp_path))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _landed(session, workspace, external_id, *, parent, kind):
    ticket = make_tree_ticket(
        session, workspace, external_id, parent=parent, kind=kind, landed_branch=BRANCH
    )
    ticket.landed_sha = "a" * 40
    session.add(ticket)
    session.commit()
    return ticket


def _record(session, ticket, **answers) -> None:
    HANDLERS["loregarden_record_fix_coverage"](
        session, {"ticket_id": ticket.external_id, **answers}
    )


def test_a_tree_with_no_bug_has_no_section(session, workspace):
    ms = make_tree_ticket(session, workspace, "ms", kind=WorkItemType.MILESTONE)
    _landed(session, workspace, "f1", parent=ms, kind=WorkItemType.FEATURE)

    assert tree_fix_section(session, ms, branch=BRANCH) == ""


def test_each_landed_bug_gets_a_block_the_check_accepts(session, workspace):
    ms = make_tree_ticket(session, workspace, "ms", kind=WorkItemType.MILESTONE)
    b1 = _landed(session, workspace, "b1", parent=ms, kind=WorkItemType.BUG)
    b2 = _landed(session, workspace, "b2", parent=ms, kind=WorkItemType.BUG)
    _record(session, b1, **ANSWERS)
    _record(session, b2, not_a_state_bug="A wrong label on a toast; nothing is persisted")

    section = tree_fix_section(session, ms, branch=BRANCH)

    assert "#### b1: Ticket b1" in section and "#### b2: Ticket b2" in section
    assert check.problems("ms: Ticket ms", section, _ROOT) == []


def test_a_bug_with_no_record_fails_the_check_under_its_own_name(session, workspace):
    ms = make_tree_ticket(session, workspace, "ms", kind=WorkItemType.MILESTONE)
    b1 = _landed(session, workspace, "b1", parent=ms, kind=WorkItemType.BUG)
    _landed(session, workspace, "b2", parent=ms, kind=WorkItemType.BUG)
    _record(session, b1, **ANSWERS)

    section = tree_fix_section(session, ms, branch=BRANCH)
    found = check.problems("ms: Ticket ms", section, _ROOT)

    assert "loregarden_record_fix_coverage" in section
    assert found and all(problem.startswith("b2: Ticket b2: ") for problem in found)


def test_a_bug_that_has_not_landed_on_this_branch_is_not_in_it(session, workspace):
    ms = make_tree_ticket(session, workspace, "ms", kind=WorkItemType.MILESTONE)
    make_tree_ticket(session, workspace, "b1", parent=ms, kind=WorkItemType.BUG)

    assert tree_fix_section(session, ms, branch=BRANCH) == ""


def test_the_publisher_writes_the_section_into_the_tree_pr(session, tmp_path):
    repo = make_repo(tmp_path)
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "-u", "origin", "main")
    workspace = Workspace(slug="tree-fix-pub", name="tree-fix-pub", repo_path=str(repo))
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    bug = make_tree_ticket(
        session,
        workspace,
        "b1",
        kind=WorkItemType.BUG,
        git_automation_json=json.dumps({"commit": True, "push": True, "open_pr": True}),
    )
    git(repo, "branch", bug.branch, "main")
    (repo / "fix.txt").write_text("fixed\n")
    git(repo, "switch", "-q", bug.branch)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "fix")
    git(repo, "switch", "-q", "main")
    _record(session, bug, **ANSWERS)
    fake = FakeGitHub()

    with mock.patch.object(git_automation, "_gh", side_effect=fake):
        outcome = publish_tree(session, bug, workspace)

    assert outcome.ok, outcome.detail
    assert "## Fix coverage" in (fake.body or "")
    assert check.problems("b1: Ticket b1", fake.body or "", _ROOT) == []
