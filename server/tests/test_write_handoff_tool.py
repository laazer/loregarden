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


# --------------------------------------------------------------------------
# 134 — a gate that could not judge must not read as a gate that approved.
#
# `_validate_via_workspace_gate` collapsed four outcomes into one permissive
# `stored_unvalidated`: no gate module, a timeout, unparseable output, and a
# non-zero exit. Only the first is structural — a workspace with no gate has no
# catalog to violate. The other three are a gate that was supposed to judge this
# handoff and did not, and storing anyway makes "nobody checked" and "checked
# and fine" the same result.
# --------------------------------------------------------------------------

#: Exits 0 and prints prose where its JSON result belongs.
_UNPARSEABLE_GATE = "def run(inputs):\n    print('not json at all')\n    return None\n"

#: Raises, so the validator subprocess exits non-zero.
_CRASHING_GATE = "def run(inputs):\n    raise RuntimeError('gate exploded')\n"

#: Outlasts VALIDATION_TIMEOUT_SECONDS, which the test shortens.
_HANGING_GATE = (
    "import time\n\ndef run(inputs):\n    time.sleep(30)\n    return {'status': 'PASS'}\n"
)


def _repo_with_gate_source(tmp_path: Path, source: str) -> Path:
    repo = make_repo(tmp_path, name="repo")
    gates = repo / "ci" / "scripts" / "gates"
    gates.mkdir(parents=True)
    (gates / "__init__.py").write_text("", encoding="utf-8")
    (gates / "handoff_validation_check.py").write_text(source, encoding="utf-8")
    (repo / "project_board" / "checkpoints").mkdir(parents=True)
    return repo


def _write(session, ticket):
    return write_handoff(
        session,
        ticket_id=ticket.external_id,
        workspace_slug="wsx",
        from_agent="test_designer",
        to_agent="test_breaker",
        checklist=_good_checklist(),
    )


@pytest.mark.parametrize(
    ("source", "expected_skip"),
    [
        (_UNPARSEABLE_GATE, "unparseable"),
        (_CRASHING_GATE, "errored"),
    ],
    ids=["unparseable", "crash"],
)
def test_a_gate_that_cannot_judge_fails_closed(source, expected_skip, isolated_db, tmp_path):
    """AC1/AC2. The handoff must not be stored on an operational gate failure."""
    repo = _repo_with_gate_source(tmp_path, source)
    with Session(isolated_db) as session:
        ticket = _seed(session, repo)
        ticket_pk = ticket.id
        result = _write(session, ticket)

    assert result["status"] == "GATE_ERROR", result
    assert result["skip"] == expected_skip, result
    assert result["rolled_back"] is True, result
    with Session(isolated_db) as session:
        assert latest_handoff_doc(session, ticket_pk) is None, (
            "an unjudged handoff was stored; 'nobody checked' now reads as 'checked and fine'"
        )


def test_a_gate_that_times_out_fails_closed(isolated_db, tmp_path, monkeypatch):
    """AC1, on its own path — a timeout is caught before any output exists to parse."""
    monkeypatch.setattr(
        "loregarden.services.handoff_writer.VALIDATION_TIMEOUT_SECONDS", 1, raising=True
    )
    repo = _repo_with_gate_source(tmp_path, _HANGING_GATE)
    with Session(isolated_db) as session:
        ticket = _seed(session, repo)
        ticket_pk = ticket.id
        result = _write(session, ticket)

    assert result["status"] == "GATE_ERROR", result
    assert result["skip"] == "timed_out", result
    with Session(isolated_db) as session:
        assert latest_handoff_doc(session, ticket_pk) is None


def test_a_workspace_with_no_gate_still_stores_and_says_so(isolated_db, tmp_path):
    """AC3. The structural case stays permissive, and now names itself.

    This is the discriminator: a fix that simply failed closed on every
    `ran: False` would pass both tests above and break every workspace that has
    no gate module — which is most of them.
    """
    repo = tmp_path / "repo"
    _make_repo(repo, with_gate=False)

    with Session(isolated_db) as session:
        ticket = _seed(session, repo)
        ticket_pk = ticket.id
        result = _write(session, ticket)

    assert result["status"] == "stored_unvalidated", result
    assert result["skip"] == "absent", result
    with Session(isolated_db) as session:
        assert latest_handoff_doc(session, ticket_pk) is not None, (
            "a workspace with no gate could no longer record a handoff"
        )


def test_a_working_gate_still_passes(isolated_db, tmp_path):
    """The other control: none of this touches a gate that actually judges."""
    repo = tmp_path / "repo"
    _make_repo(repo, with_gate=True)

    with Session(isolated_db) as session:
        ticket = _seed(session, repo)
        result = _write(session, ticket)

    assert result["status"] == "PASS", result


# --- an unvalidated handoff has to reach an operator -------------------------


def test_an_unvalidated_handoff_reaches_the_ticket_not_just_the_agent(isolated_db, tmp_path):
    """lg-workflow-integrity-89. The status was returned as JSON to the calling
    agent and appeared nowhere else — no artifact, no event, no ticket state, no
    UI — so an operator had no way to learn that a handoff on disk had never been
    checked. The live example read `required_items_met: 2 of 4` with items marked
    incomplete, written, never validated, and persisting.
    """
    from loregarden.models.domain import Artifact
    from sqlmodel import select

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
        errors = session.exec(
            select(Artifact).where(Artifact.ticket_id == ticket_pk, Artifact.kind == "error")
        ).all()
        assert len(errors) == 1
        body = errors[0].content_json or ""
        assert "not validated" in errors[0].title.lower()
        # The reason has to travel with it, or the operator learns only that
        # something is wrong and not what to do about it.
        assert "no handoff gate" in body.lower()
        # And the counters, which are the part they can act on directly.
        assert "Required items met" in body


def test_a_validated_handoff_files_no_error(isolated_db, tmp_path):
    """The surface is for the unchecked case. A gate that ran and passed must not
    leave an error artifact behind — that would train operators to ignore them."""
    from loregarden.models.domain import Artifact
    from sqlmodel import select

    repo = tmp_path / "repo"
    _make_repo(repo, with_gate=True)
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

    with Session(isolated_db) as session:
        errors = session.exec(
            select(Artifact).where(Artifact.ticket_id == ticket_pk, Artifact.kind == "error")
        ).all()
        assert errors == []


def test_each_way_the_gate_can_fail_to_run_names_itself(isolated_db, tmp_path):
    """AC3, asserted rather than assumed. Four separate paths reach `ran: False`
    — no gate module, timeout, unparseable output, non-zero exit — and an
    operator reading "not validated" needs to know which one, because the fix
    differs for each."""
    from loregarden.services.handoff_writer import _validate_via_workspace_gate

    repo = tmp_path / "nogate"
    _make_repo(repo, with_gate=False)
    result = _validate_via_workspace_gate(
        repo,
        external_id="t1-demo",
        from_agent="test_designer",
        to_agent="test_breaker",
        checkpoints_dir="scratch",
    )
    assert result["ran"] is False
    # Names the missing module and where it was looked for, not just "failed".
    assert "no handoff gate" in result["reason"].lower()


def test_the_tool_description_does_not_promise_a_repo_path_it_never_writes(
    isolated_db,
):
    """AC2. The description claimed it writes
    `project_board/checkpoints/<ticket>/handoff-latest.yaml`, while the handoff
    is stored as an artifact row and the YAML goes to a gitignored scratch tree.
    An agent that believed the description would look for a file that is not
    there."""
    from loregarden.mcp.tool_ids import McpTool
    from loregarden.mcp.tools import TOOL_DEFINITIONS

    tool = next(t for t in TOOL_DEFINITIONS if t["name"] == McpTool.WRITE_HANDOFF)
    description = tool["description"]

    assert "project_board/checkpoints" not in description
    assert "artifact row" in description
    assert "stored_unvalidated" in description
