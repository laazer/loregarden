"""Two-way sync between tickets and GitHub issues.

`gh` is replaced at its one seam (`github_issue_client.run_gh`) by an in-memory
issue tracker that answers the same argv, so the client's parsing, the merge,
and the ticket edit path all run for real.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from loregarden.config import settings
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import (
    ConflictPolicy,
    GithubIssueLink,
    IssueClosure,
    IssueSnapshot,
    SyncField,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services.github_issue_sync import (
    FieldOutcome,
    link_for_ticket,
    merge_field,
    plan_sync,
    publish_ticket,
    sync_link,
    sync_workspace,
)
from loregarden.services.ticket_service import TicketService
from sqlmodel import Session, select

REPO = "acme/widgets"


class FakeGh:
    """Just enough of `gh issue`/`gh repo` to be the other side of a sync."""

    def __init__(self) -> None:
        self.issues: dict[int, dict[str, Any]] = {}
        self.calls: list[list[str]] = []
        self.fail_next: str = ""
        #: Every call with this verb fails, e.g. "edit".
        self.fail_verb: str = ""

    def add(
        self,
        title: str,
        body: str = "",
        *,
        state: str = "OPEN",
        reason: str | None = None,
        labels=(),
    ) -> int:
        number = len(self.issues) + 1
        self.issues[number] = {
            "number": number,
            "title": title,
            "body": body,
            "state": state,
            "stateReason": reason,
            "url": f"https://github.com/{REPO}/issues/{number}",
            "labels": [{"name": name} for name in labels],
        }
        return number

    @staticmethod
    def _opt(args: list[str], flag: str) -> str | None:
        return args[args.index(flag) + 1] if flag in args else None

    def __call__(self, args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        if self.fail_next or args[1] == self.fail_verb:
            message, self.fail_next = self.fail_next or "HTTP 502", ""
            return subprocess.CompletedProcess(args, 1, "", message)
        out = self._dispatch(args)
        return subprocess.CompletedProcess(args, 0, out, "")

    def _dispatch(self, args: list[str]) -> str:
        noun, verb = args[0], args[1]
        if (noun, verb) == ("repo", "view"):
            return REPO + "\n"
        if verb == "list":
            return json.dumps([i for i in self.issues.values() if i["state"] == "OPEN"])
        if verb == "create":
            number = self.add(self._opt(args, "--title") or "", self._opt(args, "--body") or "")
            return self.issues[number]["url"] + "\n"
        issue = self.issues[int(args[2])]
        if verb == "view":
            return json.dumps(issue)
        if verb == "edit":
            for flag, key in (("--title", "title"), ("--body", "body")):
                if (value := self._opt(args, flag)) is not None:
                    issue[key] = value
        elif verb == "close":
            issue["state"] = "CLOSED"
            reason = self._opt(args, "--reason")
            issue["stateReason"] = "NOT_PLANNED" if reason == "not planned" else "COMPLETED"
        elif verb == "reopen":
            issue["state"], issue["stateReason"] = "OPEN", "REOPENED"
        return ""


@pytest.fixture(name="gh")
def gh_fixture():
    fake = FakeGh()
    with patch("loregarden.services.github_issue_client.run_gh", side_effect=fake):
        yield fake


@pytest.fixture(name="workspace")
def workspace_fixture(db_session: Session) -> Workspace:
    return db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()


@pytest.fixture(name="milestone")
def milestone_fixture(db_session: Session, workspace: Workspace) -> Ticket:
    return TicketService(db_session).create_ticket(
        workspace_slug=workspace.slug, title="Inbox", work_item_type=WorkItemType.MILESTONE
    )


@pytest.fixture(name="ticket")
def ticket_fixture(db_session: Session, workspace: Workspace, milestone: Ticket) -> Ticket:
    return TicketService(db_session).create_ticket(
        workspace_slug=workspace.slug,
        title="Widgets wobble",
        description="They wobble when spun.",
        work_item_type=WorkItemType.BUG,
        parent_ticket_id=milestone.id,
    )


@pytest.fixture(name="linked")
def linked_fixture(db_session: Session, gh: FakeGh, ticket: Ticket) -> GithubIssueLink:
    publish_ticket(db_session, ticket)
    link = link_for_ticket(db_session, ticket.id)
    assert link is not None
    return link


def _reload(session: Session, ticket: Ticket) -> Ticket:
    session.expire_all()
    fresh = session.get(Ticket, ticket.id)
    assert fresh is not None
    return fresh


# --- the merge, pure ----------------------------------------------------------


@pytest.mark.parametrize(
    ("base", "local", "remote", "expected"),
    [
        ("a", "a", "a", FieldOutcome.UNCHANGED),
        ("a", "b", "a", FieldOutcome.PUSH),
        ("a", "a", "b", FieldOutcome.PULL),
        ("a", "b", "b", FieldOutcome.CONVERGED),
        ("a", "b", "c", FieldOutcome.CONFLICT),
    ],
)
def test_merge_field(base: str, local: str, remote: str, expected: FieldOutcome):
    assert merge_field(base, local, remote) is expected


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        (ConflictPolicy.REPORT, FieldOutcome.CONFLICT),
        (ConflictPolicy.LOCAL, FieldOutcome.PUSH),
        (ConflictPolicy.REMOTE, FieldOutcome.PULL),
    ],
)
def test_conflict_policy_settles_only_conflicts(policy: ConflictPolicy, expected: FieldOutcome):
    base = IssueSnapshot(title="t", body="b", closure=IssueClosure.OPEN)
    local = IssueSnapshot(title="t-local", body="b-local", closure=IssueClosure.OPEN)
    remote = IssueSnapshot(title="t-remote", body="b", closure=IssueClosure.OPEN)

    plan = plan_sync(base, local, remote, policy)

    assert plan[SyncField.TITLE] is expected
    assert plan[SyncField.BODY] is FieldOutcome.PUSH
    assert plan[SyncField.CLOSURE] is FieldOutcome.UNCHANGED


# --- publish ------------------------------------------------------------------


def test_publish_opens_an_issue_and_records_the_base(db_session: Session, gh: FakeGh, linked):
    issue = gh.issues[linked.issue_number]
    assert (issue["title"], issue["body"]) == ("Widgets wobble", "They wobble when spun.")
    assert linked.repo == REPO
    assert linked.issue_url == issue["url"]
    assert (linked.synced_title, linked.synced_body) == ("Widgets wobble", "They wobble when spun.")


def test_publishing_twice_is_refused(db_session: Session, gh: FakeGh, ticket: Ticket, linked):
    with pytest.raises(ValueError, match="already linked"):
        publish_ticket(db_session, ticket)


# --- sync, each direction -----------------------------------------------------


def test_remote_edit_is_pulled_into_the_ticket(db_session: Session, gh: FakeGh, ticket, linked):
    gh.issues[linked.issue_number]["title"] = "Widgets wobble at speed"

    result = sync_link(db_session, linked)

    assert result.pulled == [SyncField.TITLE]
    assert result.pushed == []
    assert _reload(db_session, ticket).title == "Widgets wobble at speed"
    assert linked.synced_title == "Widgets wobble at speed"


def test_local_edit_is_pushed_to_the_issue(db_session: Session, gh: FakeGh, ticket, linked):
    ticket.description = "They wobble, then fall over."
    db_session.add(ticket)
    db_session.commit()

    result = sync_link(db_session, linked)

    assert result.pushed == [SyncField.BODY]
    assert gh.issues[linked.issue_number]["body"] == "They wobble, then fall over."
    assert linked.synced_body == "They wobble, then fall over."


def test_a_second_sync_after_a_push_changes_nothing(
    db_session: Session, gh: FakeGh, ticket, linked
):
    ticket.title = "Renamed"
    db_session.add(ticket)
    db_session.commit()
    sync_link(db_session, linked)
    gh.calls.clear()

    result = sync_link(db_session, linked)

    assert (result.pushed, result.pulled, result.conflicts) == ([], [], [])
    assert [c[1] for c in gh.calls] == ["view"]


def test_crlf_from_the_web_editor_is_not_an_edit(db_session: Session, gh: FakeGh, linked):
    gh.issues[linked.issue_number]["body"] = "They wobble when spun.\r\n"

    result = sync_link(db_session, linked)

    assert (result.pushed, result.pulled) == ([], [])


def test_both_sides_edited_is_reported_and_nothing_is_written(
    db_session: Session, gh: FakeGh, ticket, linked
):
    ticket.title = "Local title"
    db_session.add(ticket)
    db_session.commit()
    gh.issues[linked.issue_number]["title"] = "Remote title"

    result = sync_link(db_session, linked)

    assert [c.field for c in result.conflicts] == [SyncField.TITLE]
    assert result.conflicts[0].local == "Local title"
    assert result.conflicts[0].remote == "Remote title"
    assert _reload(db_session, ticket).title == "Local title"
    assert gh.issues[linked.issue_number]["title"] == "Remote title"
    # The base stays, so the conflict is reported again rather than forgotten.
    assert linked.synced_title == "Widgets wobble"


def test_policy_local_resolves_a_conflict_by_pushing(
    db_session: Session, gh: FakeGh, ticket, linked
):
    ticket.title = "Local title"
    db_session.add(ticket)
    db_session.commit()
    gh.issues[linked.issue_number]["title"] = "Remote title"

    result = sync_link(db_session, linked, policy=ConflictPolicy.LOCAL)

    assert result.conflicts == []
    assert gh.issues[linked.issue_number]["title"] == "Local title"
    assert linked.synced_title == "Local title"


# --- closure ------------------------------------------------------------------


def test_closing_the_issue_as_not_planned_abandons_the_ticket(
    db_session: Session, gh: FakeGh, ticket, linked
):
    gh.issues[linked.issue_number].update(state="CLOSED", stateReason="NOT_PLANNED")

    result = sync_link(db_session, linked)

    assert result.pulled == [SyncField.CLOSURE]
    assert _reload(db_session, ticket).state is TicketState.WONT_DO


def test_finishing_the_ticket_closes_the_issue_as_completed(
    db_session: Session, gh: FakeGh, ticket, linked
):
    ticket.state = TicketState.DONE
    db_session.add(ticket)
    db_session.commit()

    sync_link(db_session, linked)

    issue = gh.issues[linked.issue_number]
    assert (issue["state"], issue["stateReason"]) == ("CLOSED", "COMPLETED")
    assert linked.synced_closure is IssueClosure.COMPLETED


def test_reopening_the_issue_returns_a_done_ticket_to_the_backlog(
    db_session: Session, gh: FakeGh, ticket, linked
):
    ticket.state = TicketState.DONE
    db_session.add(ticket)
    db_session.commit()
    sync_link(db_session, linked)
    gh.issues[linked.issue_number].update(state="OPEN", stateReason="REOPENED")

    sync_link(db_session, linked)

    assert _reload(db_session, ticket).state is TicketState.BACKLOG


def test_wont_do_to_completed_goes_through_the_backlog(
    db_session: Session, gh: FakeGh, ticket, linked
):
    """wont_do -> done is not a chosen move; the pull must not raise on it."""
    gh.issues[linked.issue_number].update(state="CLOSED", stateReason="NOT_PLANNED")
    sync_link(db_session, linked)
    gh.issues[linked.issue_number].update(stateReason="COMPLETED")

    result = sync_link(db_session, linked)

    assert result.error == ""
    assert _reload(db_session, ticket).state is TicketState.DONE


# --- failure is recorded, not swallowed ----------------------------------------


def test_a_gh_failure_is_returned_and_recorded_on_the_link(db_session: Session, gh: FakeGh, linked):
    gh.fail_next = "HTTP 404: Could not resolve to an issue"

    result = sync_link(db_session, linked)

    assert "Could not resolve" in result.error
    assert "Could not resolve" in linked.last_error


def test_a_failed_push_does_not_advance_the_base(db_session: Session, gh: FakeGh, ticket, linked):
    ticket.title = "Pushed later"
    db_session.add(ticket)
    db_session.commit()
    gh.fail_verb = "edit"

    result = sync_link(db_session, linked)

    assert "failed" in result.error
    assert linked.synced_title == "Widgets wobble"
    gh.fail_verb = ""
    assert sync_link(db_session, linked).pushed == [SyncField.TITLE]


# --- workspace sync and import ------------------------------------------------


def test_workspace_sync_imports_unlinked_issues_under_the_parent(
    db_session: Session, gh: FakeGh, workspace: Workspace, milestone: Ticket, linked
):
    number = gh.add("Gears grind", "Loudly.", labels=("bug", "gears"))
    gh.add("Already closed", state="CLOSED", reason="COMPLETED")

    result = sync_workspace(db_session, workspace, import_parent_ticket_id=milestone.id)

    assert [r.issue_number for r in result.imported] == [number]
    imported = db_session.get(Ticket, result.imported[0].ticket_id)
    assert imported is not None
    assert (imported.title, imported.description) == ("Gears grind", "Loudly.")
    assert imported.parent_ticket_id == milestone.id
    assert imported.work_item_type is WorkItemType.BUG
    assert json.loads(imported.tags_json) == ["bug", "gears"]
    # The already-linked issue was synced, not imported a second time.
    assert [r.issue_number for r in result.links] == [linked.issue_number]
    again = sync_workspace(db_session, workspace, import_parent_ticket_id=milestone.id)
    assert again.imported == []


def test_workspace_sync_without_a_parent_imports_nothing(
    db_session: Session, gh: FakeGh, workspace: Workspace
):
    gh.add("Gears grind")

    result = sync_workspace(db_session, workspace)

    assert result.imported == []
    assert not any(call[1] == "list" for call in gh.calls)


# --- lifecycle ----------------------------------------------------------------


def test_deleting_the_ticket_deletes_its_link(db_session: Session, gh: FakeGh, ticket, linked):
    TicketService(db_session).delete_ticket(ticket.id)

    assert db_session.exec(select(GithubIssueLink)).all() == []


# --- HTTP ---------------------------------------------------------------------


def test_rest_publish_status_sync_and_unlink(client, gh: FakeGh, ticket: Ticket):
    assert client.get(f"/api/tickets/{ticket.id}/github-issue").json() is None

    published = client.post(f"/api/tickets/{ticket.id}/github-issue")
    assert published.status_code == 200, published.text
    number = published.json()["issue_number"]

    status = client.get(f"/api/tickets/{ticket.id}/github-issue").json()
    assert status["issue_number"] == number
    gh.issues[number]["title"] = "Edited on GitHub"
    synced = client.post(f"/api/tickets/{ticket.id}/github-issue/sync", json={"policy": "report"})
    assert synced.json()["pulled"] == ["title"]

    assert client.delete(f"/api/tickets/{ticket.id}/github-issue").status_code == 204
    assert client.delete(f"/api/tickets/{ticket.id}/github-issue").status_code == 404


def _signed(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _issue_event(action: str, number: int, title: str) -> bytes:
    return json.dumps(
        {
            "action": action,
            "issue": {
                "number": number,
                "title": title,
                "body": "They wobble when spun.",
                "state": "open",
                "state_reason": None,
                "html_url": f"https://github.com/{REPO}/issues/{number}",
                "labels": [],
            },
            "repository": {"full_name": REPO},
        }
    ).encode()


def test_webhook_refuses_an_unsigned_delivery_without_a_secret(client, workspace: Workspace):
    with patch.object(settings, "ci_webhook_secret", ""):
        res = client.post(
            f"/api/github/issues/webhook/{workspace.id}",
            content=_issue_event("edited", 1, "x"),
            headers={"X-GitHub-Event": "issues"},
        )
    assert res.status_code == 403


def test_webhook_edit_is_pulled_into_the_linked_ticket(
    client, db_session: Session, gh: FakeGh, workspace: Workspace, ticket: Ticket, linked
):
    body = _issue_event("edited", linked.issue_number, "Edited via webhook")
    gh.calls.clear()
    with patch.object(settings, "ci_webhook_secret", "s3cret"):
        res = client.post(
            f"/api/github/issues/webhook/{workspace.id}",
            content=body,
            headers={"X-GitHub-Event": "issues", "X-Hub-Signature-256": _signed(body, "s3cret")},
        )

    assert res.status_code == 200, res.text
    assert res.json()["status"] == "ok"
    assert _reload(db_session, ticket).title == "Edited via webhook"
    # The payload was the issue; nothing needed fetching.
    assert gh.calls == []


def test_webhook_imports_an_opened_issue_only_when_given_a_parent(
    client, db_session: Session, gh: FakeGh, workspace: Workspace, milestone: Ticket
):
    body = _issue_event("opened", 42, "Brand new")
    headers = {"X-GitHub-Event": "issues", "X-Hub-Signature-256": _signed(body, "s3cret")}
    url = f"/api/github/issues/webhook/{workspace.id}"
    with patch.object(settings, "ci_webhook_secret", "s3cret"):
        ignored = client.post(url, content=body, headers=headers)
        imported = client.post(
            f"{url}?parent_ticket_id={milestone.id}", content=body, headers=headers
        )

    assert ignored.json()["status"] == "ignored"
    assert imported.json()["status"] == "ok"
    link = db_session.exec(select(GithubIssueLink).where(GithubIssueLink.issue_number == 42)).one()
    assert db_session.get(Ticket, link.ticket_id).title == "Brand new"


# --- MCP ----------------------------------------------------------------------


def _mcp(session: Session, args: dict[str, Any]) -> Any:
    name = "loregarden_sync_github_issues"
    return json.loads(execute_tool(session, name, normalize_tool_arguments(name, args)))


def test_mcp_status_publish_and_sync(db_session: Session, gh: FakeGh, ticket: Ticket):
    assert _mcp(db_session, {"action": "status", "ticket_id": ticket.id})["linked"] is False

    published = _mcp(db_session, {"action": "publish", "ticket_id": ticket.id})
    gh.issues[published["issue_number"]]["body"] = "Remote body"
    synced = _mcp(db_session, {"action": "sync", "ticket_id": ticket.id})

    assert synced["pulled"] == ["body"]
    assert _mcp(db_session, {"action": "status", "ticket_id": ticket.id})["linked"] is True


def test_mcp_rejects_an_unknown_action(db_session: Session, ticket: Ticket):
    with pytest.raises(ValueError, match="invalid arguments"):
        _mcp(db_session, {"action": "delete_everything", "ticket_id": ticket.id})
