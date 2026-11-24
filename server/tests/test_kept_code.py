"""Unused code names the ticket it waits for, and the ticket names it back.

The Pixi Hive floor was offered for deletion on an import search while an open
ticket's criteria named it; nothing joined the two.
"""

from __future__ import annotations

import json

import pytest
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import DoctorCheck, DoctorStatus, TicketState
from loregarden.services.doctor import CHECKS, run_checks
from loregarden.services.kept_code import KeptCode, KeptCodeUnreadable, kept_code
from sqlmodel import Session
from tests.tree_publish_helpers import make_workspace
from tests.worktree_helpers import commit_on, make_repo, make_tree_ticket


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    return make_workspace(session, repo, "kept-code")


def _ticket(session, workspace, external_id, state=TicketState.BACKLOG):
    ticket = make_tree_ticket(session, workspace, external_id)
    ticket.state = state
    session.add(ticket)
    session.commit()
    return ticket


def _keep(repo, path, ticket, *, comment="// {}"):
    marker = comment.format(f"kept-for: {ticket} - its criteria need this module")
    commit_on(repo, "main", path, f"{marker}\nexport const x = 1;\n")


def _doctor(session, workspace, repo):
    (finding,) = run_checks(session, workspace, repo, checks=(DoctorCheck.KEPT_CODE_TICKETS,))
    return finding


@pytest.mark.parametrize("comment", ["// {}", " * {}", "/* {} */", "/** {} */", "# {}"])
def test_a_marker_is_read_from_any_comment_style(repo, comment):
    _keep(repo, "src/a.ts", "lg-floor-72", comment=comment)
    assert kept_code(repo) == [
        KeptCode("src/a.ts", 1, "lg-floor-72", "its criteria need this module")
    ]


@pytest.mark.parametrize(
    ("path", "text"),
    [
        ("src/__tests__/a.test.ts", "// kept-for: lg-floor-72 - quoted by a test\n"),
        ("server/tests/test_a.py", "# kept-for: lg-floor-72 - quoted by a test\n"),
        ("src/a.ts", 'const s = "kept-for: lg-floor-72 - in a string";\n'),
        (
            "src/a.ts",
            "const x = 1; // kept-for: lg-floor-72 - trailing code, as the gate reads it\n",
        ),
        ("DOC.md", "Write `kept-for: <ticket> - <reason>` in the module.\n"),
    ],
)
def test_text_that_only_mentions_a_marker_is_not_one(repo, path, text):
    commit_on(repo, "main", path, text)
    assert kept_code(repo) == []


def test_a_directory_git_cannot_search_is_an_error_not_an_empty_answer(tmp_path):
    with pytest.raises(KeptCodeUnreadable):
        kept_code(tmp_path / "missing")
    (tmp_path / "plain").mkdir()
    with pytest.raises(KeptCodeUnreadable):
        kept_code(tmp_path / "plain")


def test_the_ticket_lists_the_code_kept_for_it_and_no_one_elses(session, workspace, repo):
    _ticket(session, workspace, "lg-floor-72")
    _keep(repo, "src/floor.ts", "lg-floor-72")
    _keep(repo, "src/other.ts", "lg-other-73")

    args = normalize_tool_arguments(
        "loregarden_get_ticket_by_external",
        {"workspace_slug": workspace.slug, "external_id": "lg-floor-72"},
    )
    payload = json.loads(execute_tool(session, "loregarden_get_ticket_by_external", args))

    assert [item["path"] for item in payload["kept_code"]] == ["src/floor.ts"]
    assert "kept_code_error" not in payload


def test_a_ticket_whose_repo_cannot_be_read_says_so(session, workspace, tmp_path):
    ticket = _ticket(session, workspace, "lg-floor-72")
    workspace.repo_path = str(tmp_path / "gone")
    session.add(workspace)
    session.commit()

    args = normalize_tool_arguments(
        "loregarden_get_ticket_by_external",
        {"workspace_slug": workspace.slug, "external_id": ticket.external_id},
    )
    payload = json.loads(execute_tool(session, "loregarden_get_ticket_by_external", args))

    assert payload["kept_code"] == []
    assert "gone" in payload["kept_code_error"]


def test_doctor_passes_when_every_marker_names_an_open_ticket(session, workspace, repo):
    assert DoctorCheck.KEPT_CODE_TICKETS in CHECKS
    _ticket(session, workspace, "lg-floor-72", TicketState.BLOCKED)
    _keep(repo, "src/floor.ts", "lg-floor-72")

    finding = _doctor(session, workspace, repo)

    assert finding.status is DoctorStatus.PASS
    assert "All 1" in finding.finding


@pytest.mark.parametrize("state", [TicketState.DONE, TicketState.WONT_DO])
def test_doctor_names_code_kept_for_a_ticket_that_is_over(session, workspace, repo, state):
    _ticket(session, workspace, "lg-floor-72", state)
    _ticket(session, workspace, "lg-open-73")
    _keep(repo, "src/floor.ts", "lg-floor-72")
    _keep(repo, "src/open.ts", "lg-open-73")

    finding = _doctor(session, workspace, repo)

    assert finding.status is DoctorStatus.WARN
    assert "src/floor.ts:1" in finding.finding
    assert state.value in finding.finding
    assert "src/open.ts" not in finding.finding


def test_doctor_names_a_marker_for_a_ticket_that_does_not_exist(session, workspace, repo):
    _keep(repo, "src/floor.ts", "lg-nowhere-9999")

    finding = _doctor(session, workspace, repo)

    assert finding.status is DoctorStatus.WARN
    assert "lg-nowhere-9999" in finding.finding


def test_doctor_names_a_marker_using_a_tickets_old_id(session, workspace, repo):
    """The ticket's own brief matches its current id, so it would not list this code."""
    ticket = _ticket(session, workspace, "lg-floor-72")
    ticket.legacy_external_id = "lg-old-floor-7"
    session.add(ticket)
    session.commit()
    _keep(repo, "src/floor.ts", "lg-old-floor-7")

    finding = _doctor(session, workspace, repo)

    assert finding.status is DoctorStatus.WARN
    assert "lg-old-floor-7 is now lg-floor-72" in finding.finding


def test_doctor_does_not_pass_a_checkout_it_could_not_search(session, workspace, tmp_path):
    (tmp_path / "plain").mkdir()
    finding = _doctor(session, workspace, tmp_path / "plain")
    assert finding.status is DoctorStatus.WARN
    assert "Could not search" in finding.finding
