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
from loregarden.models.domain import Artifact, Ticket, Workspace
from loregarden.services.handoff_store import boundary_from_doc, latest_handoff_doc
from loregarden.services.handoff_writer import HandoffWriteError, write_handoff
from sqlmodel import Session, select
from tests.worktree_helpers import git, make_repo

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
    # Both items are required and complete, but neither claims more than
    # `inferred` — prose in `evidence` used to satisfy the met-counter and no
    # longer does. See test_verified_items_count_toward_the_met_counter.
    assert result["required_items_met"] == 0
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


def test_the_stored_handoff_carries_the_boundary_of_the_tree_it_describes(isolated_db, tmp_path):
    """Resolved server-side, from the tree the stages actually wrote to.

    The agent is not asked for it: an agent reporting the tree it worked in is
    the claim, not the evidence for the claim.
    """
    repo = make_repo(tmp_path, name="repo")
    _make_repo(repo, with_gate=False)
    (repo / "unstaged.txt").write_text("mid-stage edit\n", encoding="utf-8")

    with Session(isolated_db) as session:
        ticket = _seed(session, repo)
        ticket_pk = ticket.id
        write_handoff(
            session,
            ticket_id=ticket.external_id,
            workspace_slug="wsx",
            from_agent="test_designer",
            to_agent="test_breaker",
            checklist=_good_checklist(),
        )

    with Session(isolated_db) as session:
        doc = latest_handoff_doc(session, ticket_pk)
        artifact = session.exec(
            select(Artifact).where(Artifact.kind == "handoff", Artifact.ticket_id == ticket_pk)
        ).one()

    boundary = boundary_from_doc(doc)
    assert boundary.repo_path == str(repo)
    assert boundary.branch == "main"
    assert boundary.head_sha == git(repo, "rev-parse", "HEAD").stdout.strip()
    assert "unstaged.txt" in boundary.dirty_paths
    # The row and the document agree because there is one source for both.
    assert artifact.commit_sha == boundary.head_sha


def test_a_workspace_whose_repo_has_no_git_still_writes_a_handoff(isolated_db, tmp_path):
    """A plain directory is not an error here. The boundary is unknown, the
    handoff still stores — an unreadable repo must not be able to block an
    agent from attesting to its work."""
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
    with Session(isolated_db) as session:
        doc = latest_handoff_doc(session, ticket_pk)
    assert not boundary_from_doc(doc).is_recorded


def test_the_gate_reads_the_same_boundary_the_database_holds(isolated_db, tmp_path):
    """The export and the row project from one document, so a gate that inspects
    the boundary sees what was stored rather than a second rendering of it."""
    repo = make_repo(tmp_path, name="repo")
    gates = repo / "ci" / "scripts" / "gates"
    gates.mkdir(parents=True)
    (gates / "__init__.py").write_text("", encoding="utf-8")
    (gates / "handoff_validation_check.py").write_text(
        textwrap.dedent(
            """
            import pathlib, yaml

            def run(inputs):
                p = (pathlib.Path(inputs["checkpoints_dir"]) / inputs["ticket_id"]
                     / "handoff-latest.yaml")
                doc = yaml.safe_load(p.read_text())["handoff"]
                if doc.get("schema_version") not in ("1.0", "1"):
                    return {"status": "FAIL", "message": "unsupported schema_version",
                            "violations": [{"rule": "handoff_artifact_invalid"}]}
                if not doc.get("boundary", {}).get("head_sha"):
                    return {"status": "FAIL", "message": "no boundary",
                            "violations": [{"rule": "handoff_artifact_invalid"}]}
                return {"status": "PASS", "message": "ok"}
            """
        ),
        encoding="utf-8",
    )
    (repo / "project_board" / "checkpoints").mkdir(parents=True)

    with Session(isolated_db) as session:
        ticket = _seed(session, repo)
        result = write_handoff(
            session,
            ticket_id=ticket.external_id,
            workspace_slug="wsx",
            from_agent="test_designer",
            to_agent="test_breaker",
            checklist=_good_checklist(),
        )

    assert result["status"] == "PASS", result.get("message")
