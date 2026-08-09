"""Tests for the loregarden_write_handoff MCP tool / handoff_writer service.

Uses a fake workspace repo with a stub handoff gate module so the write →
validate → rollback contract is exercised hermetically, independent of any real
workspace's gate.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments, tool_names
from loregarden.models.domain import Ticket, Workspace
from loregarden.services.handoff_store import latest_handoff_doc
from loregarden.services.handoff_writer import HandoffWriteError, write_handoff
from sqlmodel import Session

# A stub gate: PASSes only when the written file exists and carries the
# `test_suite_complete` key — enough to prove the file was rendered and discovered.
_STUB_GATE = textwrap.dedent(
    """
    import pathlib, yaml

    def run(inputs):
        tid = inputs["ticket_id"]
        root = inputs.get("checkpoints_dir", "project_board/checkpoints")
        p = pathlib.Path(root) / tid / "handoff-latest.yaml"
        if not p.is_file():
            return {"status": "FAIL", "message": "missing",
                    "violations": [{"rule": "handoff_artifact_missing", "message": "no file"}]}
        doc = yaml.safe_load(p.read_text())
        keys = {c["item_key"] for c in doc["handoff"]["checklist"]}
        if "test_suite_complete" in keys:
            return {"status": "PASS", "message": "ok"}
        return {"status": "FAIL", "message": "bad keys",
                "violations": [{"rule": "handoff_unknown_item", "message": "bad"}]}
    """
)


def _make_repo(root: Path, *, with_gate: bool = True) -> None:
    if with_gate:
        gates = root / "ci" / "scripts" / "gates"
        gates.mkdir(parents=True)
        (gates / "__init__.py").write_text("", encoding="utf-8")
        (gates / "handoff_validation_check.py").write_text(_STUB_GATE, encoding="utf-8")
    (root / "project_board" / "checkpoints").mkdir(parents=True)


def _seed(session: Session, repo: Path, *, ext: str = "t1-demo") -> Ticket:
    ws = Workspace(slug="wsx", name="WSX", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    ticket = Ticket(external_id=ext, workspace_id=ws.id, title="demo")
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _good_checklist() -> list[dict]:
    return [
        {
            "item_key": "test_suite_complete",
            "item": "Test suite complete per spec test plan",
            "status": "complete",
            "evidence_type": "path",
            "evidence": "tests/x.gd",
        },
        {
            "item_key": "test_all_runnable",
            "item": "All tests runnable",
            "status": "complete",
            "evidence": "runs clean",
        },
    ]


def test_registered_in_tool_list():
    assert "loregarden_write_handoff" in tool_names()


def test_write_handoff_pass(isolated_db, tmp_path):
    repo = tmp_path / "repo"
    _make_repo(repo)
    with Session(isolated_db) as session:
        ticket = _seed(session, repo)
        ticket_pk = ticket.id
        result = write_handoff(
            session,
            ticket_id=ticket.external_id,
            workspace_slug="wsx",
            from_agent="test_designer",
            to_agent="test_breaker",
            checklist=_good_checklist(),
        )
    assert result["status"] == "PASS"
    assert result["required_items_met"] == 2
    assert result["total_required_items"] == 2
    assert result["artifact_id"]
    with Session(isolated_db) as session:
        doc = latest_handoff_doc(session, ticket_pk)
    assert doc is not None
    assert {c["item_key"] for c in doc["handoff"]["checklist"]} == {
        "test_suite_complete",
        "test_all_runnable",
    }
    # Nothing lands in the repo's tracked checkpoints any more.
    tracked = repo / "project_board/checkpoints" / "t1-demo" / "handoff-latest.yaml"
    assert not tracked.exists()


def test_write_handoff_fail_rolls_back_to_prior(isolated_db, tmp_path):
    repo = tmp_path / "repo"
    _make_repo(repo)
    with Session(isolated_db) as session:
        ticket = _seed(session, repo)
        ticket_pk = ticket.id
        good = write_handoff(
            session,
            ticket_id=ticket.external_id,
            workspace_slug="wsx",
            from_agent="test_designer",
            to_agent="test_breaker",
            checklist=_good_checklist(),
        )
        assert good["status"] == "PASS"
        result = write_handoff(
            session,
            ticket_id=ticket.external_id,
            workspace_slug="wsx",
            from_agent="test_designer",
            to_agent="test_breaker",
            checklist=[
                {"item_key": "bogus_key", "item": "nope", "status": "complete", "evidence": "x"}
            ],
        )
    assert result["status"] == "FAIL"
    assert result["rolled_back"] is True
    assert result["violations"]
    # The prior stored handoff must still be the ticket's latest — a bad write
    # never clobbers a valid one.
    with Session(isolated_db) as session:
        doc = latest_handoff_doc(session, ticket_pk)
    assert doc is not None
    assert {c["item_key"] for c in doc["handoff"]["checklist"]} == {
        "test_suite_complete",
        "test_all_runnable",
    }


def test_write_handoff_fail_removes_when_no_prior(isolated_db, tmp_path):
    repo = tmp_path / "repo"
    _make_repo(repo)
    with Session(isolated_db) as session:
        ticket = _seed(session, repo)
        ticket_pk = ticket.id
        result = write_handoff(
            session,
            ticket_id=ticket.external_id,
            workspace_slug="wsx",
            from_agent="test_designer",
            to_agent="test_breaker",
            checklist=[{"item_key": "bogus", "item": "n", "status": "complete", "evidence": "x"}],
        )
    assert result["status"] == "FAIL"
    with Session(isolated_db) as session:
        assert latest_handoff_doc(session, ticket_pk) is None


def test_write_handoff_unvalidated_without_gate(isolated_db, tmp_path):
    repo = tmp_path / "repo"
    _make_repo(repo, with_gate=False)
    with Session(isolated_db) as session:
        ticket = _seed(session, repo)
        ticket_pk = ticket.id
        result = write_handoff(
            session,
            ticket_id=ticket.external_id,
            workspace_slug="wsx",
            from_agent="test_designer",
            to_agent="test_breaker",
            checklist=_good_checklist(),
        )
    assert result["status"] == "stored_unvalidated"
    # Stored anyway — there is no catalog to have violated.
    with Session(isolated_db) as session:
        assert latest_handoff_doc(session, ticket_pk) is not None


def test_bad_checklist_raises(isolated_db, tmp_path):
    repo = tmp_path / "repo"
    _make_repo(repo)
    with Session(isolated_db) as session:
        ticket = _seed(session, repo)
        with pytest.raises(HandoffWriteError):
            write_handoff(
                session,
                ticket_id=ticket.external_id,
                workspace_slug="wsx",
                from_agent="test_designer",
                to_agent="test_breaker",
                checklist=[{"item_key": "test_suite_complete", "status": "complete"}],
            )


def test_execute_tool_dispatch_and_stringified_checklist(isolated_db, tmp_path):
    repo = tmp_path / "repo"
    _make_repo(repo)
    with Session(isolated_db) as session:
        ticket = _seed(session, repo)
        raw_args = {
            "ticketId": ticket.external_id,
            "workspace": "wsx",
            "fromAgent": "test_designer",
            "toAgent": "test_breaker",
            "checklist": json.dumps(_good_checklist()),
        }
        norm = normalize_tool_arguments("loregarden_write_handoff", raw_args)
        out = json.loads(execute_tool(session, "loregarden_write_handoff", norm))
    assert out["status"] == "PASS"
