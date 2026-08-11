"""Two tickets running at the same time, in one repository and one database.

The spike behind this asked two questions: does SQLite hold up under concurrent
orchestration writers, and is anything in the execution path built for exactly
one live run. Both are answered here rather than in prose — the first by
committing from two threads against the app's own engine settings, the second
by resolving two tickets' execution roots concurrently and checking neither
touched the shared checkout.
"""

import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest
from loregarden.models.domain import AgentRun, RunStatus, Ticket, Workspace
from loregarden.services.ticket_worktree import resolve_execution_root
from sqlmodel import Session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    def git(*args):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "Test")
    (root / "seed.txt").write_text("seed\n")
    git("add", "-A")
    git("commit", "-q", "-m", "seed")
    return root


@pytest.fixture(name="workspace_id")
def workspace_id_fixture(isolated_db, repo):
    with Session(isolated_db) as session:
        ws = Workspace(slug="proj", name="proj", repo_path=str(repo))
        session.add(ws)
        session.commit()
        session.refresh(ws)
        return ws.id


def _seed_ticket(isolated_db, workspace_id, external_id):
    with Session(isolated_db) as session:
        ticket = Ticket(
            external_id=external_id,
            workspace_id=workspace_id,
            title=f"Work on {external_id}",
            branch=f"loregarden/{external_id.lower()}",
        )
        session.add(ticket)
        session.commit()
        run = AgentRun(
            run_code=f"run-{external_id}",
            ticket_id=ticket.id,
            workspace_id=workspace_id,
            agent_id="backend_implementer",
            status=RunStatus.RUNNING,
        )
        session.add(run)
        session.commit()
        return ticket.id, run.id


def _head_branch(path):
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_two_tickets_execute_at_once_without_touching_the_shared_checkout(
    isolated_db, workspace_id, repo
):
    tickets = [_seed_ticket(isolated_db, workspace_id, f"LG-{n}") for n in (1, 2)]

    def work(ids):
        ticket_id, run_id = ids
        # A session per thread, as the orchestrator's own parallel path does.
        with Session(isolated_db) as session:
            ticket = session.get(Ticket, ticket_id)
            run = session.get(AgentRun, run_id)
            workspace = session.get(Workspace, workspace_id)
            root = resolve_execution_root(session, run, ticket, workspace)
            (root / f"{ticket.external_id}.txt").write_text("work\n")
            for args in (["add", "-A"], ["commit", "-q", "-m", ticket.external_id]):
                subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
            # A concurrent DB write while the other thread is doing the same:
            # this is the SQLite contention the spike was about.
            run.status = RunStatus.SUCCEEDED
            session.add(run)
            session.commit()
            return root

    with ThreadPoolExecutor(max_workers=2) as pool:
        roots = list(pool.map(work, tickets))

    assert roots[0] != roots[1]
    assert (roots[0] / "LG-1.txt").exists()
    assert (roots[1] / "LG-2.txt").exists()
    # Neither run moved the shared checkout, and neither saw the other's files.
    assert _head_branch(repo) == "main"
    assert not (roots[0] / "LG-2.txt").exists()
    assert not (repo / "LG-1.txt").exists()

    with Session(isolated_db) as session:
        statuses = {session.get(AgentRun, run_id).status for _ticket_id, run_id in tickets}
    assert statuses == {RunStatus.SUCCEEDED}
