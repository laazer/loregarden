"""The `loregarden_doctor` MCP surface.

The tool's job is to hand back structured findings, not a rendered report: a
terminal wants lines, the inbox wants a remediation string, and a tool that
pre-formats prose forces every consumer to parse it back out.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from loregarden.mcp.tool_ids import AUTO_APPROVED_MCP_TOOLS, McpTool
from loregarden.mcp.tool_registry import EXTENDED_TOOLS
from loregarden.mcp.tools import tool_names
from loregarden.models.domain import DoctorCheck, DoctorStatus, Workspace
from loregarden.services import doctor
from sqlmodel import Session
from tests.worktree_helpers import git, make_repo


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="stable_db_path", autouse=True)
def stable_db_path_fixture(monkeypatch):
    """Pin the database the db_resolution check reports on.

    Left alone it reports the truth about wherever the suite happens to run —
    which in a worktree is a FAIL, correctly, because the worktree has no
    database of its own. That is the trap the check exists to catch, and it is
    covered in test_doctor.py; here it would just make every assertion depend on
    the checkout the tests were started from.
    """
    monkeypatch.setattr(doctor, "resolved_database_path", lambda: Path(__file__))


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="proj", name="proj", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _call(session, **arguments) -> dict:
    return json.loads(EXTENDED_TOOLS[McpTool.DOCTOR.value](session, arguments))


def test_the_tool_is_registered():
    assert McpTool.DOCTOR.value in tool_names()


def test_the_tool_auto_approves_because_it_only_reads():
    """It writes nothing, contacts nothing, and reads no credential value —
    gating that behind a human click spends the run's timeout budget for nothing."""
    assert McpTool.DOCTOR in AUTO_APPROVED_MCP_TOOLS


def test_it_returns_structured_findings_not_prose(session, workspace):
    payload = _call(session, workspace_slug="proj")

    assert payload["ok"] is True
    assert {f["check"] for f in payload["findings"]} == {c.value for c in DoctorCheck}
    for finding in payload["findings"]:
        assert finding["status"] in {s.value for s in DoctorStatus}
        assert "finding" in finding and "remediation" in finding


def test_a_failing_check_makes_the_whole_report_not_ok(session, workspace, repo):
    git(repo, "config", "--local", "core.bare", "true")

    payload = _call(session, workspace_slug="proj")

    assert payload["ok"] is False
    assert payload["fail_count"] >= 1
    bare = next(f for f in payload["findings"] if f["check"] == DoctorCheck.GIT_CORE_BARE.value)
    assert bare["remediation"]


def test_a_warning_alone_does_not_make_the_report_not_ok(session, workspace):
    """A WARN is a thing to know, not a thing to stop for. The empty-database
    check warns here, and `ok` must stay true."""
    payload = _call(session, workspace_slug="proj", checks=[DoctorCheck.DB_RESOLUTION.value])

    assert payload["warn_count"] >= 1
    assert payload["ok"] is True


def test_a_subset_runs_only_what_was_asked_for(session, workspace):
    payload = _call(session, workspace_slug="proj", checks=[DoctorCheck.GIT_CORE_BARE.value])

    assert [f["check"] for f in payload["findings"]] == [DoctorCheck.GIT_CORE_BARE.value]


def test_an_unknown_check_name_is_an_error_not_a_silent_skip(session, workspace):
    """A caller asking for a check that does not exist wants to know that, not a
    clean report about the checks it did not mean."""
    with pytest.raises(ValueError):
        _call(session, workspace_slug="proj", checks=["no_such_check"])


def test_a_missing_workspace_slug_is_rejected(session):
    with pytest.raises(ValueError, match="workspace_slug"):
        _call(session)
