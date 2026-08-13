"""Handoff storage and the export the workspace gates read.

Handoffs are database-authoritative; the YAML is an export to a gitignored scratch
tree. Pin the parts that are easy to regress: the export must not write into the repo's
tracked checkpoints, it must carry co-located artifacts the *other* gates read
(todos-latest.json), and a stale export must never be read as the current handoff.
"""

from __future__ import annotations

import json

import yaml
from loregarden.models.domain import Artifact, GitBoundary, Ticket, Workspace
from loregarden.services.handoff_store import (
    CHECKPOINTS_SUBDIR,
    HANDOFF_ARTIFACT_KIND,
    HANDOFF_FILENAME,
    HANDOFF_SCRATCH_SUBDIR,
    boundary_from_doc,
    build_handoff_doc,
    export_for_gate,
    latest_handoff_doc,
    store_handoff,
)
from sqlmodel import Session, select


def _seed(session: Session, repo) -> tuple[Workspace, Ticket]:
    ws = Workspace(slug="wsx", name="WSX", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    ticket = Ticket(external_id="t1-demo", workspace_id=ws.id, title="demo")
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ws, ticket


def _doc(
    from_agent: str = "test_designer",
    *,
    item_key: str = "test_suite_complete",
    boundary: GitBoundary | None = None,
) -> dict:
    return build_handoff_doc(
        external_id="t1-demo",
        from_agent=from_agent,
        to_agent="test_breaker",
        checklist=[
            {
                "item_key": item_key,
                "item": "Test suite complete",
                "required": True,
                "status": "complete",
                "evidence": "tests/x.py",
            }
        ],
        required_items_met=1,
        total_required_items=1,
        boundary=boundary or GitBoundary(),
    )


def test_store_and_read_back(isolated_db, tmp_path):
    with Session(isolated_db) as session:
        _ws, ticket = _seed(session, tmp_path / "repo")
        store_handoff(session, ticket=ticket, doc=_doc())
        session.commit()
        loaded = latest_handoff_doc(session, ticket.id)
    assert loaded is not None
    assert loaded["handoff"]["from_agent"] == "test_designer"


def test_latest_wins_and_history_is_kept(isolated_db, tmp_path):
    with Session(isolated_db) as session:
        _ws, ticket = _seed(session, tmp_path / "repo")
        store_handoff(session, ticket=ticket, doc=_doc("first"))
        store_handoff(session, ticket=ticket, doc=_doc("second"))
        session.commit()
        loaded = latest_handoff_doc(session, ticket.id)
        rows = session.exec(select(Artifact).where(Artifact.kind == HANDOFF_ARTIFACT_KIND)).all()
    assert loaded["handoff"]["from_agent"] == "second"
    # Append-only: the earlier attestation is still on the record.
    assert len(rows) == 2


def test_no_handoff_reads_as_none(isolated_db, tmp_path):
    with Session(isolated_db) as session:
        _ws, ticket = _seed(session, tmp_path / "repo")
        assert latest_handoff_doc(session, ticket.id) is None


def test_export_writes_scratch_not_tracked_checkpoints(isolated_db, tmp_path):
    repo = tmp_path / "repo"
    (repo / CHECKPOINTS_SUBDIR).mkdir(parents=True)
    with Session(isolated_db) as session:
        ws, ticket = _seed(session, repo)
        store_handoff(session, ticket=ticket, doc=_doc())
        session.commit()
        root = export_for_gate(session, ws, ticket)

    exported = root / "t1-demo" / HANDOFF_FILENAME
    assert exported.is_file()
    assert root == repo / HANDOFF_SCRATCH_SUBDIR
    doc = yaml.safe_load(exported.read_text(encoding="utf-8"))
    assert doc["handoff"]["from_agent"] == "test_designer"
    # The whole point: nothing lands where git would pick it up.
    assert not (repo / CHECKPOINTS_SUBDIR / "t1-demo" / HANDOFF_FILENAME).exists()


def test_export_mirrors_colocated_gate_artifacts(isolated_db, tmp_path):
    """The todo gate reads the same --checkpoints-dir, so its file must come along."""
    repo = tmp_path / "repo"
    ticket_dir = repo / CHECKPOINTS_SUBDIR / "t1-demo"
    ticket_dir.mkdir(parents=True)
    (ticket_dir / "todos-latest.json").write_text(json.dumps({"todos": []}), encoding="utf-8")

    with Session(isolated_db) as session:
        ws, ticket = _seed(session, repo)
        store_handoff(session, ticket=ticket, doc=_doc())
        session.commit()
        root = export_for_gate(session, ws, ticket)

    assert (root / "t1-demo" / "todos-latest.json").is_file()
    assert (root / "t1-demo" / HANDOFF_FILENAME).is_file()


def test_export_without_stored_handoff_omits_the_file(isolated_db, tmp_path):
    """Absence must stay absence: no handoff stored means the gate fails the
    transition, exactly as a missing file used to."""
    repo = tmp_path / "repo"
    (repo / CHECKPOINTS_SUBDIR).mkdir(parents=True)
    with Session(isolated_db) as session:
        ws, ticket = _seed(session, repo)
        root = export_for_gate(session, ws, ticket)
    assert not (root / "t1-demo" / HANDOFF_FILENAME).exists()


def test_export_replaces_a_stale_previous_export(isolated_db, tmp_path):
    repo = tmp_path / "repo"
    (repo / CHECKPOINTS_SUBDIR).mkdir(parents=True)
    with Session(isolated_db) as session:
        ws, ticket = _seed(session, repo)
        store_handoff(session, ticket=ticket, doc=_doc("first"))
        session.commit()
        export_for_gate(session, ws, ticket)

        stale = repo / HANDOFF_SCRATCH_SUBDIR / "t1-demo" / "leftover.txt"
        stale.write_text("from an earlier transition", encoding="utf-8")

        store_handoff(session, ticket=ticket, doc=_doc("second"))
        session.commit()
        root = export_for_gate(session, ws, ticket)

    assert not stale.exists()
    doc = yaml.safe_load((root / "t1-demo" / HANDOFF_FILENAME).read_text(encoding="utf-8"))
    assert doc["handoff"]["from_agent"] == "second"


def test_the_document_carries_the_boundary_it_was_written_against():
    boundary = GitBoundary(
        repo_path="/w/proj",
        branch="loregarden/t1-demo",
        head_sha="abc123",
        dirty_paths=["server/x.py"],
    )

    doc = _doc(boundary=boundary)

    assert boundary_from_doc(doc) == boundary


def test_the_schema_version_stays_1_0_so_workspace_gates_keep_accepting_it():
    """The validator is each workspace's own gate, and it allows a closed set of
    versions. A bump here fails every handoff in every workspace whose gate has
    not been updated first — and `write_handoff` rolls back on FAIL, so the write
    path would stop working rather than degrade."""
    assert _doc()["handoff"]["schema_version"] == "1.0"


def test_a_document_written_before_boundaries_existed_reads_as_unrecorded():
    legacy = _doc()
    del legacy["handoff"]["boundary"]

    assert not boundary_from_doc(legacy).is_recorded


def test_the_stored_row_takes_its_commit_sha_from_the_document(isolated_db, tmp_path):
    """The row and the document cannot disagree, because there is one source for
    both. It was previously a parameter no caller passed, so every handoff
    artifact carried an empty sha and none were reachable from the commit-scoped
    evidence queries."""
    with Session(isolated_db) as session:
        _ws, ticket = _seed(session, tmp_path / "repo")

        artifact = store_handoff(
            session, ticket=ticket, doc=_doc(boundary=GitBoundary(head_sha="deadbeef"))
        )
        session.commit()

        assert artifact.commit_sha == "deadbeef"


def test_an_unrecorded_boundary_stores_an_empty_sha(isolated_db, tmp_path):
    with Session(isolated_db) as session:
        _ws, ticket = _seed(session, tmp_path / "repo")

        artifact = store_handoff(session, ticket=ticket, doc=_doc())
        session.commit()

        assert artifact.commit_sha == ""


def test_the_boundary_survives_the_yaml_export(isolated_db, tmp_path):
    """The export and the row project from one document, so the gate reads the
    same boundary the database holds."""
    repo = tmp_path / "repo"
    (repo / CHECKPOINTS_SUBDIR).mkdir(parents=True)
    boundary = GitBoundary(repo_path=str(repo), branch="main", head_sha="c0ffee")

    with Session(isolated_db) as session:
        ws, ticket = _seed(session, repo)
        store_handoff(session, ticket=ticket, doc=_doc(boundary=boundary))
        session.commit()
        root = export_for_gate(session, ws, ticket)

    exported = yaml.safe_load((root / "t1-demo" / HANDOFF_FILENAME).read_text(encoding="utf-8"))
    assert boundary_from_doc(exported) == boundary
