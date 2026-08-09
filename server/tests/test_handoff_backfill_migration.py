"""Migration 0071: import committed handoff YAML into the artifacts table.

The files are the only record of what agents attested to at historical transitions, so
the import must be lossless, must not double-import on a re-run, and must not fail the
whole migration over one unreadable file or an unmounted workspace repo.
"""

from __future__ import annotations

import json

import pytest
import yaml
from loregarden.db.migrations_handoffs import (
    CHECKPOINTS_SUBDIR,
    HANDOFF_ARTIFACT_KIND,
    m_backfill_handoff_artifacts,
)
from loregarden.models.domain import Ticket, Workspace
from sqlalchemy import create_engine, text
from sqlmodel import Session, SQLModel


@pytest.fixture(name="engine")
def engine_fixture(tmp_path):
    """A current-schema database — the shape a migration runs against."""
    engine = create_engine(f"sqlite:///{tmp_path / 'backfill.db'}")
    SQLModel.metadata.create_all(engine)
    return engine


def _backfill(engine) -> None:
    with engine.begin() as conn:
        m_backfill_handoff_artifacts(conn)


def _handoff_yaml(ticket: str, from_agent: str, validated_at: str) -> str:
    return yaml.safe_dump(
        {
            "handoff": {
                "schema_version": "1.0",
                "ticket_id": ticket,
                "from_agent": from_agent,
                "to_agent": "test_breaker",
                "validated_at": validated_at,
                "required_items_met": 1,
                "total_required_items": 1,
                "checklist": [
                    {
                        "item_key": "test_suite_complete",
                        "item": "Test suite complete",
                        "required": True,
                        "status": "complete",
                        "evidence": "tests/x.py",
                    }
                ],
            }
        }
    )


def _seed_workspace(engine, repo, *, external_ids: list[str]) -> None:
    with Session(engine) as session:
        ws = Workspace(slug="wsx", name="WSX", repo_path=str(repo))
        session.add(ws)
        session.commit()
        session.refresh(ws)
        for ext in external_ids:
            session.add(Ticket(external_id=ext, workspace_id=ws.id, title="demo"))
        session.commit()


def _write_handoff(repo, ticket: str, *, from_agent="test_designer", at="2026-01-01T00:00:00Z"):
    d = repo / CHECKPOINTS_SUBDIR / ticket
    d.mkdir(parents=True, exist_ok=True)
    (d / "handoff-latest.yaml").write_text(_handoff_yaml(ticket, from_agent, at), encoding="utf-8")


def _handoff_rows(engine) -> list[tuple[str, str]]:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT ticket_id, content_json FROM artifacts WHERE kind = :k"),
            {"k": HANDOFF_ARTIFACT_KIND},
        ).fetchall()


def test_backfill_imports_committed_handoffs(engine, tmp_path):
    repo = tmp_path / "repo"
    _seed_workspace(engine, repo, external_ids=["t1", "t2"])
    _write_handoff(repo, "t1", from_agent="alpha")
    _write_handoff(repo, "t2", from_agent="beta")

    _backfill(engine)

    rows = _handoff_rows(engine)
    assert len(rows) == 2
    agents = {json.loads(c)["handoff"]["from_agent"] for _t, c in rows}
    assert agents == {"alpha", "beta"}


def test_backfill_is_idempotent(engine, tmp_path):
    repo = tmp_path / "repo"
    _seed_workspace(engine, repo, external_ids=["t1"])
    _write_handoff(repo, "t1")

    _backfill(engine)
    _backfill(engine)

    assert len(_handoff_rows(engine)) == 1


def test_backfill_skips_checkpoint_dir_with_no_ticket(engine, tmp_path):
    repo = tmp_path / "repo"
    _seed_workspace(engine, repo, external_ids=["t1"])
    _write_handoff(repo, "t1")
    _write_handoff(repo, "orphaned-ticket")

    _backfill(engine)

    assert len(_handoff_rows(engine)) == 1


def test_backfill_survives_an_unreadable_file(engine, tmp_path):
    repo = tmp_path / "repo"
    _seed_workspace(engine, repo, external_ids=["t1", "t2"])
    _write_handoff(repo, "t1", from_agent="alpha")
    bad = repo / CHECKPOINTS_SUBDIR / "t2"
    bad.mkdir(parents=True, exist_ok=True)
    (bad / "handoff-latest.yaml").write_text("not: [valid", encoding="utf-8")

    _backfill(engine)

    rows = _handoff_rows(engine)
    assert len(rows) == 1
    assert json.loads(rows[0][1])["handoff"]["from_agent"] == "alpha"


def test_backfill_skips_unmounted_workspace(engine, tmp_path):
    """A workspace repo that is not on this machine is not an error."""
    _seed_workspace(engine, tmp_path / "does-not-exist", external_ids=["t1"])

    _backfill(engine)

    assert _handoff_rows(engine) == []
