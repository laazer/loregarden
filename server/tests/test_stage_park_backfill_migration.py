"""Re-kinding the parks that were already parked when the fix landed.

The rows that provoked this fix are sitting in someone's inbox. Left as
WORKFLOW_GATEs they keep the old resolution — approving one still marks the
stage DONE without running it — so the migration has to reach them, and has to
reach only them.
"""

import pytest
from loregarden.db.migrations import apply_migrations
from loregarden.models.domain import Approval, ApprovalKind, ApprovalStatus, Workspace
from sqlalchemy import create_engine, text
from sqlmodel import Session, SQLModel


@pytest.fixture(name="migrated")
def migrated_fixture(tmp_path):
    """A database carrying one of each row the backfill must judge, migrated once.

    Rows are built through the models rather than raw INSERTs so the fixture
    cannot drift from the schema — and because foreign keys are enforced on the
    engine here, so the approvals need a workspace that actually exists.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'backfill.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        workspace = Workspace(slug="proj", name="proj", repo_path=str(tmp_path))
        session.add(workspace)
        session.commit()
        session.refresh(workspace)
        rows = [
            ("env-park", "Environment preflight failed on LG-1", ApprovalStatus.PENDING),
            ("boundary-park", "Boundary check on LG-2: branch_changed", ApprovalStatus.PENDING),
            ("real-gate", "Approve Add the thing", ApprovalStatus.PENDING),
            ("resolved-park", "Environment preflight failed on LG-3", ApprovalStatus.APPROVED),
        ]
        for approval_id, title, status in rows:
            session.add(
                Approval(
                    id=approval_id,
                    workspace_id=workspace.id,
                    kind=ApprovalKind.WORKFLOW_GATE,
                    title=title,
                    stage_key="implement",
                    status=status,
                )
            )
        session.commit()
    apply_migrations(engine)
    return engine


def _kinds(engine) -> dict[str, str]:
    with engine.connect() as conn:
        return {r[0]: r[1] for r in conn.execute(text("SELECT id, kind FROM approvals"))}


def test_pending_parks_are_re_kinded(migrated):
    kinds = _kinds(migrated)
    assert kinds["env-park"] == "stage_park"
    assert kinds["boundary-park"] == "stage_park"


def test_a_real_pending_gate_is_left_alone(migrated):
    """The titles are the only signal a park left before the kind existed, so
    the match has to be narrow enough not to swallow genuine sign-offs."""
    assert _kinds(migrated)["real-gate"] == "workflow_gate"


def test_resolved_history_is_not_rewritten(migrated):
    """A resolved park records what the operator was shown and what the code did
    at the time. Rewriting it would make the stages this skipped harder to find,
    not easier."""
    assert _kinds(migrated)["resolved-park"] == "workflow_gate"


def test_the_waiver_columns_are_added(migrated):
    with migrated.connect() as conn:
        ticket_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(tickets)"))}
        run_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(agent_runs)"))}
    assert {"dispatch_waiver_stage_key", "dispatch_waiver_approval_id"} <= ticket_cols
    assert "dispatch_waiver_approval_id" in run_cols


def test_running_it_again_changes_nothing(migrated):
    before = _kinds(migrated)
    assert apply_migrations(migrated) == []
    assert _kinds(migrated) == before
