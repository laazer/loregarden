"""What a run touched must include what it COMMITTED, not only what it left dirty.

79.9% of successful implement runs record no changed paths (111 of 139), and it
has not improved on current code: 47 of 60 in the last 30 days. That column is
load-bearing — `commit_paths` stages exactly those paths, so an empty set means
the scoped commit commits nothing while the run reports success.

The mechanism below was reproduced against real git rather than inferred from the
schema: an agent that commits its own work during its turn leaves a CLEAN tree,
so the dirty-path delta is empty and the run records nothing.
"""

import os
import subprocess
from pathlib import Path

import pytest
from loregarden.services.git_commit_push_service import (
    paths_committed_since,
    working_tree_paths,
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    # GIT_DIR beats cwd, and it leaks in from the pre-push hook.
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    return subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real repository with one commit, built in a fixture rather than in the
    test body so a setup failure is an ERROR rather than a confusing assertion."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "seed.txt").write_text("seed\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "seed")
    return root


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _recorded(repo: Path, before: set[str], base_sha: str) -> list[str]:
    """Exactly what `_record_changed_paths` computes, without the executor."""
    after = working_tree_paths(repo)
    assert after is not None
    committed = paths_committed_since(repo, base_sha)
    return sorted((after - before) | (committed or set()))


# --- the defect ------------------------------------------------------------


def test_work_the_agent_committed_itself_is_recorded(repo: Path):
    """The mechanism, as a regression test. Before this, the file below was
    invisible: committed, therefore not dirty, therefore not in the delta."""
    base, before = _head(repo), working_tree_paths(repo) or set()

    (repo / "feature.py").write_text("print('x')\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "the agent commits its own work")

    assert working_tree_paths(repo) == set(), "precondition: the tree is clean again"
    assert _recorded(repo, before, base) == ["feature.py"]


def test_the_dirty_delta_alone_would_miss_it(repo: Path):
    """The control that names the bug. Without the committed half, the same run
    records nothing — which is what 111 rows in the corpus look like."""
    base, before = _head(repo), working_tree_paths(repo) or set()
    (repo / "feature.py").write_text("print('x')\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "agent commit")

    dirty_only = sorted((working_tree_paths(repo) or set()) - before)
    assert dirty_only == [], "the old behaviour: committed work vanishes"
    assert _recorded(repo, before, base) == ["feature.py"]


# --- what must keep working ------------------------------------------------


def test_uncommitted_work_is_still_recorded(repo: Path):
    """Unioned, not swapped: a run that leaves its work dirty is the common case
    and must not regress."""
    base, before = _head(repo), working_tree_paths(repo) or set()
    (repo / "scratch.py").write_text("x = 1\n")
    assert _recorded(repo, before, base) == ["scratch.py"]


def test_a_run_that_both_commits_and_leaves_work_dirty_records_both(repo: Path):
    base, before = _head(repo), working_tree_paths(repo) or set()
    (repo / "committed.py").write_text("a\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "half")
    (repo / "dirty.py").write_text("b\n")

    assert _recorded(repo, before, base) == ["committed.py", "dirty.py"]


def test_paths_already_dirty_before_the_run_are_not_claimed(repo: Path):
    """The delta exists so unrelated work in the tree is not swept into this
    run's commit. Union must not reintroduce that."""
    (repo / "someone_elses.py").write_text("not mine\n")
    base, before = _head(repo), working_tree_paths(repo) or set()

    (repo / "mine.py").write_text("mine\n")
    assert _recorded(repo, before, base) == ["mine.py"]


def test_a_run_that_changed_nothing_records_nothing(repo: Path):
    base, before = _head(repo), working_tree_paths(repo) or set()
    assert _recorded(repo, before, base) == []


# --- "could not look" is not "nothing there" -------------------------------


def test_an_unreadable_history_answers_none_not_empty(repo: Path):
    """Same contract as `working_tree_paths`, and for the same reason: a failed
    read reported as an empty set is what made the original 79% unmeasurable."""
    assert paths_committed_since(repo, "not-a-real-sha") is None


def test_no_base_sha_is_not_a_failure(repo: Path):
    """A run with no recorded start sha has nothing to diff against, and its work
    will be uncommitted anyway — the dirty set already covers it."""
    assert paths_committed_since(repo, "") == set()


# --- the executor is actually wired to it ----------------------------------


def test_the_recorder_itself_stores_committed_work(db_session, repo: Path):
    """Drives `_record_changed_paths`, not a copy of its logic.

    The tests above exercise the helper pair directly, which means neutering the
    executor's union does NOT fail them — a control run proved exactly that. A
    test that reimplements the code it is checking cannot see the code being
    unwired, so this one calls the real method.
    """
    import json

    from loregarden.agents.executors.cli import CliAgentExecutor
    from loregarden.models.domain import AgentRun, RunStatus, Workspace
    from sqlmodel import select
    from tests.factories import make_workspace_ticket

    ticket = make_workspace_ticket(db_session, "cpr-1")
    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    before = working_tree_paths(repo) or set()
    run = AgentRun(
        run_code="cpr_1",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        stage_key="implement",
        status=RunStatus.RUNNING,
        start_head_sha=_head(repo),
    )
    db_session.add(run)
    db_session.commit()

    (repo / "feature.py").write_text("print('x')\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "the agent commits its own work")

    CliAgentExecutor(db_session)._record_changed_paths(run, repo, before)

    db_session.refresh(run)
    assert json.loads(run.changed_paths_json) == ["feature.py"]
