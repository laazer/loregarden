"""One answer to "what did this run inherit" (spec S7, AC15).

`record_changed_paths` scopes a run's commit to what the run itself made
dirty. Today the "before" set is a live `TreeSnapshot.bracket_paths()` taken in
`cli.execute` — which a *reattaching* process cannot take, because the run
started minutes or hours ago and the tree has moved since.

The column already holds exactly that set: `stamp_run_boundary` writes
`boundary.dirty_paths`, which is `TreeSnapshot.dirty_paths`, which is what
`bracket_paths()` returns. So the fix is to read the row rather than the tree,
from both call sites — and then there is one answer rather than two.
"""

from __future__ import annotations

import json

import pytest
from loregarden.agents.executors.run_evidence import run_paths_before
from loregarden.models.domain import AgentRun, RunStatus, Ticket, Workspace
from sqlmodel import Session


@pytest.fixture(name="run")
def run_fixture(isolated_db) -> AgentRun:
    with Session(isolated_db) as session:
        workspace = Workspace(slug="paths-before", name="Paths", repo_path=".")
        session.add(workspace)
        session.commit()
        session.refresh(workspace)
        ticket = Ticket(external_id="paths-before-1", workspace_id=workspace.id, title="demo")
        session.add(ticket)
        session.commit()
        session.refresh(ticket)
        run = AgentRun(
            workspace_id=workspace.id,
            ticket_id=ticket.id,
            run_code="run_pb001",
            agent_id="backend_implementer",
            stage_key="implement",
            status=RunStatus.RUNNING,
            start_dirty_paths_json=json.dumps(["client/src/", "server/loregarden/"]),
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        session.expunge(run)
        return run


def test_the_inherited_paths_come_from_the_runs_own_row(run):
    """AC15. The same set `bracket_paths()` would have returned, from the row."""
    assert run_paths_before(run) == {"client/src/", "server/loregarden/"}


def test_a_run_with_nothing_dirty_at_its_start_inherits_nothing(run):
    """`[]` is the column's default and means "looked, found nothing dirty"."""
    run.start_dirty_paths_json = "[]"

    assert run_paths_before(run) == set()


def test_an_unreadable_boundary_inherits_nothing_rather_than_raising(run):
    """The conservative direction *for bracketing only*: the post-run delta
    then attributes what it finds to the run rather than dropping paths, and
    the post-run read reports its own failure."""
    run.start_dirty_paths_json = ""

    assert run_paths_before(run) == set()


def test_the_set_is_deduplicated_and_order_independent(run):
    run.start_dirty_paths_json = json.dumps(["a/", "a/", "b/"])

    assert run_paths_before(run) == {"a/", "b/"}
