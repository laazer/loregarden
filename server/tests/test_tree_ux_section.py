"""A tree's PR carries the user-facing surfaces section its UX check needs.

#622 (2026-10-15): 491's tree PR carried 917's changes to the stage overflow
menu, `publish_tree` wrote a description with no "User-facing surfaces"
section, and the "PR UX section" check failed until a person wrote it by hand.
Every assertion on a description here runs that check's own code against it.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest
from loregarden.mcp.ux_answers_tools import HANDLERS
from loregarden.models.domain import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    WorkItemType,
    Workspace,
)
from loregarden.services import git_automation
from loregarden.services.git_automation import GENERATED_END, GENERATED_START
from loregarden.services.land_ticket import land_ticket
from loregarden.services.publish_tree import publish_tree
from loregarden.services.target_branch import resolve_target_branch
from loregarden.services.tree_ux_section import missing_card_title
from loregarden.services.ux_answers import UxAnswers, is_surface, latest_ux_answers
from pydantic import ValidationError
from sqlmodel import Session, select
from tests.worktree_helpers import commit_on, git, make_repo, make_tree_ticket

_SCRIPT = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "pr_ux_section_check.py"
_spec = importlib.util.spec_from_file_location("pr_ux_section_check", _SCRIPT)
assert _spec and _spec.loader
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

RECORD = "loregarden_record_ux_answers"
SURFACE = "client/src/pages/Dashboard.tsx"
OPEN_PR = {"commit": True, "push": True, "open_pr": True}

ANSWERS = {
    "question": "Can I paste this command and trust the tree it runs in?",
    "action": "Copy terminal command, in the stage's overflow menu",
    "real_data": "7 of 39 handed-off worktrees were dirty and got the warning",
}


class FakeGitHub:
    """The gh calls the publish chain makes, against one in-memory PR."""

    URL = "https://github.com/example/repo/pull/7"

    def __init__(self) -> None:
        self.body: str | None = None
        self.edits = 0

    def __call__(self, args, cwd):
        def ok(stdout=""):
            return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

        if args[:2] == ["pr", "list"]:
            return ok(f"{self.URL}\n" if self.body is not None else "\n")
        if args[:2] == ["pr", "create"]:
            self.body = args[args.index("--body") + 1]
            return ok(f"{self.URL}\n")
        if args[:2] == ["pr", "view"]:
            return ok(f"{self.body}\n")
        if args[:2] == ["pr", "edit"]:
            self.body = args[args.index("--body") + 1]
            self.edits += 1
            return ok(f"{self.URL}\n")
        raise AssertionError(f"unexpected gh call: {args}")


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    repo = make_repo(tmp_path)
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "-u", "origin", "main")
    return repo


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="tree-ux", name="tree-ux", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="github")
def github_fixture():
    fake = FakeGitHub()
    with mock.patch.object(git_automation, "_gh", side_effect=fake):
        yield fake


def _land(session, workspace, repo, child, path, content):
    target = resolve_target_branch(session, child, workspace, repo_root=repo)
    git(repo, "branch", child.branch, target)
    commit_on(repo, child.branch, path, content)
    assert land_ticket(session, child, workspace).ok
    return target


@pytest.fixture(name="tree")
def tree_fixture(session, workspace, repo):
    """Milestone `ms` whose child `c1` landed a change to a client page."""
    ms = make_tree_ticket(
        session,
        workspace,
        "ms",
        kind=WorkItemType.MILESTONE,
        git_automation_json=json.dumps(OPEN_PR),
    )
    c1 = make_tree_ticket(session, workspace, "c1", parent=ms)
    target = _land(session, workspace, repo, c1, SURFACE, "export const page = 1;\n")
    return ms, c1, target


def _record(session, ticket, **answers) -> str:
    return HANDLERS[RECORD](session, {"ticket_id": ticket.external_id, **answers})


def _changed(repo, target) -> list[str]:
    return git(repo, "diff", "--name-only", "main", target).stdout.split()


def _cards(session, root) -> list[Approval]:
    return list(
        session.exec(
            select(Approval).where(
                Approval.ticket_id == root.id,
                Approval.kind == ApprovalKind.HUMAN_ACTION,
                Approval.status == ApprovalStatus.PENDING,
                Approval.title == missing_card_title(root),
            )
        ).all()
    )


# --- the PR the publisher writes ------------------------------------------------


def test_recorded_answers_make_the_tree_pr_pass_the_ux_check(
    session, workspace, repo, tree, github
):
    ms, c1, target = tree
    _record(session, c1, **ANSWERS)

    outcome = publish_tree(session, ms, workspace)

    assert outcome.ok, outcome.detail
    assert check.problems(github.body, _changed(repo, target)) == []
    assert "#### c1: Ticket c1" in github.body and SURFACE in github.body
    assert _cards(session, ms) == []


def test_missing_answers_are_named_then_filled_in_on_the_next_publish(
    session, workspace, repo, tree, github
):
    ms, c1, target = tree

    assert publish_tree(session, ms, workspace).ok

    assert check.problems(github.body, _changed(repo, target)), "the check fails, truthfully"
    assert "c1" in github.body and RECORD in github.body
    [card] = _cards(session, ms)
    assert "c1" in card.impact and SURFACE in card.impact

    _record(session, c1, **ANSWERS)
    c2 = make_tree_ticket(session, workspace, "c2", parent=ms)
    _land(session, workspace, repo, c2, "server/notes.txt", "a later landing\n")
    assert publish_tree(session, ms, workspace).ok

    assert github.edits == 1, "the open PR's description was refreshed"
    assert check.problems(github.body, _changed(repo, target)) == []
    assert _cards(session, ms) == []


def test_a_refresh_keeps_what_a_person_wrote_outside_the_markers(
    session, workspace, repo, tree, github
):
    ms, c1, _ = tree
    _record(session, c1, **ANSWERS)
    assert publish_tree(session, ms, workspace).ok
    github.body += "\n\nReviewer note: merge after the release cut."
    c2 = make_tree_ticket(session, workspace, "c2", parent=ms)
    _land(session, workspace, repo, c2, "client/src/components/Badge.tsx", "export {};\n")
    _record(session, c2, no_change_reason="renames a prop; nothing a person can see changes")

    assert publish_tree(session, ms, workspace).ok

    assert "Reviewer note: merge after the release cut." in github.body
    assert github.body.count(GENERATED_START) == 1 and github.body.count(GENERATED_END) == 1
    assert "#### c2: Ticket c2" in github.body


def test_a_description_without_markers_is_left_alone(session, workspace, repo, tree, github):
    ms, c1, _ = tree
    github.body = "Written by a person before the markers existed."

    assert publish_tree(session, ms, workspace).ok

    assert github.body == "Written by a person before the markers existed."
    assert github.edits == 0


def test_a_tree_that_changes_no_surface_gets_no_section(session, workspace, repo, github):
    ms = make_tree_ticket(
        session,
        workspace,
        "ms",
        kind=WorkItemType.MILESTONE,
        git_automation_json=json.dumps(OPEN_PR),
    )
    c1 = make_tree_ticket(session, workspace, "c1", parent=ms)
    _land(session, workspace, repo, c1, "server/x.py", "x = 1\n")

    assert publish_tree(session, ms, workspace).ok

    assert "User-facing surfaces" not in github.body
    assert _cards(session, ms) == []


def test_a_surface_change_no_landing_accounts_for_is_the_roots(
    session, workspace, repo, tree, github
):
    ms, c1, target = tree
    _record(session, c1, **ANSWERS)
    commit_on(repo, target, "client/src/components/Root.tsx", "export {};\n")

    assert publish_tree(session, ms, workspace).ok

    assert "#### ms: Ticket ms" in github.body
    [card] = _cards(session, ms)
    assert "`ms`" in card.impact and "Root.tsx" in card.impact


# --- recording the answers ------------------------------------------------------


def test_the_tool_refuses_what_the_check_would_refuse(session, tree):
    _, c1, _ = tree
    with pytest.raises(ValueError, match="real_data has no number"):
        _record(session, c1, **{**ANSWERS, "real_data": "it looked fine on the sandbox"})
    with pytest.raises(ValueError, match="not both"):
        _record(session, c1, **ANSWERS, no_change_reason="nothing visible changes here at all")
    assert latest_ux_answers(session, c1) is None

    _record(session, c1, **ANSWERS)
    assert latest_ux_answers(session, c1) == UxAnswers(**ANSWERS)


# --- the record agrees with the check --------------------------------------------

CASES = [
    ANSWERS,
    {**ANSWERS, "real_data": "looked right everywhere"},
    {**ANSWERS, "question": "short"},
    {**ANSWERS, "action": "<the button>"},
    {"no_change_reason": "renames a prop; nothing a person can see changes"},
    {"no_change_reason": "refactor"},
    {"no_change_reason": "<why nobody can see it, at length>"},
]


@pytest.mark.parametrize("answers", CASES)
def test_the_record_and_the_ci_check_agree(answers):
    try:
        lines = UxAnswers(**answers).lines
    except ValidationError:
        recorded = False
        lines = [
            f"- **{key.replace('_', ' ').capitalize()}:** {value}" for key, value in answers.items()
        ]
    else:
        recorded = True
    body = "## User-facing surfaces\n\n" + "\n".join(lines) + "\n"
    if "no_change_reason" in answers:
        body = (
            "## User-facing surfaces\n\n"
            + f"- **No user-visible change:** {answers['no_change_reason']}\n"
        )
    assert recorded == (check.problems(body, [SURFACE]) == [])


@pytest.mark.parametrize(
    "path",
    [
        SURFACE,
        "client/src/components/a/B.css",
        "client/src/components/__tests__/B.tsx",
        "client/src/pages/B.test.tsx",
        "client/src/lib/x.tsx",
        "client/src/pages/notes.md",
    ],
)
def test_the_surface_predicate_matches_the_check(path):
    assert is_surface(path) == check.is_surface(path)
