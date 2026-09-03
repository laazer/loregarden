"""A stage cannot hand off work it never committed (429).

Observed on blobert ticket 22 (2026-08-14). The implementation stage scored 4/4
required checklist items — all `evidence_type: attestation`, none of which
asserts the work is committed — and the handoff was honest: `head_sha 1c932d72`
with all six produced files listed under `dirty_paths`. The transition gate
caught the delta and blocked, but only *after* the stage was declared complete,
and with no repair path. The agent writing the handoff could still have
committed; the gate reading it later could not.

**Why this is not simply "intersect the ticket's recorded paths".** Measured on
this control plane: 23 of 72 succeeded `implement` runs record any
`changed_paths_json` at all, and 89 of 1168 runs overall. A check keyed only on
the ticket's recorded paths would pass vacuously for two thirds of implement
handoffs while looking exactly like a check that worked — the failure this
repository has now produced nine times. So the basis is explicit, and the
weakest of the three does not read like the strongest.
"""

import json

import pytest
from loregarden.models.domain import AgentRun, Ticket, Workspace
from loregarden.models.domain.enums import CommittedWorkBasis
from loregarden.services.handoff_committed_work import uncommitted_ticket_work
from loregarden.services.handoff_writer import HandoffWriteError, write_handoff
from sqlmodel import Session
from tests.worktree_helpers import git, make_repo


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


def _seed(session, repo):
    ws = Workspace(slug="wsx", name="WSX", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    ticket = Ticket(external_id="t1", workspace_id=ws.id, title="demo")
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ws, ticket


def _record_paths(session, ticket, ws, paths):
    run = AgentRun(
        workspace_id=ws.id,
        ticket_id=ticket.id,
        run_code="R1",
        agent_id="backend_implementer",
        stage_key="implement",
        changed_paths_json=json.dumps(paths),
    )
    session.add(run)
    session.commit()


def _dirty(repo, relpath="src/produced.py"):
    target = repo / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x = 1\n", encoding="utf-8")


def test_a_clean_tree_reports_nothing_uncommitted(session, tmp_path):
    repo = make_repo(tmp_path, name="repo")
    ws, ticket = _seed(session, repo)

    result = uncommitted_ticket_work(session, ticket, ticket_root=repo, is_ticket_worktree=True)

    assert result.paths == ()
    assert result.blocks_handoff is False


def test_dirty_ticket_paths_block_and_are_named(session, tmp_path):
    """AC1/AC3. The message has to name the files, or the agent cannot act on it."""
    repo = make_repo(tmp_path, name="repo")
    ws, ticket = _seed(session, repo)
    _record_paths(session, ticket, ws, ["src/produced.py"])
    _dirty(repo)

    result = uncommitted_ticket_work(session, ticket, ticket_root=repo, is_ticket_worktree=True)

    assert result.basis is CommittedWorkBasis.TICKET_PATHS
    assert result.paths == ("src/produced.py",)
    assert result.blocks_handoff is True
    assert "src/produced.py" in result.message()


def test_dirty_unrelated_paths_do_not_block_when_the_ticket_is_known(session, tmp_path):
    """AC4's third case. With recorded paths the check is precise."""
    repo = make_repo(tmp_path, name="repo")
    ws, ticket = _seed(session, repo)
    _record_paths(session, ticket, ws, ["src/mine.py"])
    _dirty(repo, "src/someone_else.py")

    result = uncommitted_ticket_work(session, ticket, ticket_root=repo, is_ticket_worktree=True)

    assert result.basis is CommittedWorkBasis.TICKET_PATHS
    assert result.paths == ()
    assert result.blocks_handoff is False


def test_no_recorded_paths_in_a_ticket_worktree_still_blocks(session, tmp_path):
    """The two-thirds case, and the whole reason this is not a one-line intersect.

    Nothing else runs in a ticket's own worktree, so uncommitted work there is
    this ticket's whether or not any run bothered to record it.
    """
    repo = make_repo(tmp_path, name="repo")
    ws, ticket = _seed(session, repo)
    _dirty(repo)

    result = uncommitted_ticket_work(session, ticket, ticket_root=repo, is_ticket_worktree=True)

    assert result.basis is CommittedWorkBasis.WHOLE_WORKTREE
    assert result.paths == ("src/produced.py",)
    assert result.blocks_handoff is True


def test_no_recorded_paths_in_a_shared_checkout_reports_but_does_not_block(session, tmp_path):
    """The honest third answer.

    In a shared checkout the dirt may be anyone's. Blocking every handoff
    written from one would stop far more work than the defect does, and calling
    it clean would be the vacuous pass. It says it could not tell.
    """
    repo = make_repo(tmp_path, name="repo")
    ws, ticket = _seed(session, repo)
    _dirty(repo)

    result = uncommitted_ticket_work(session, ticket, ticket_root=repo, is_ticket_worktree=False)

    assert result.basis is CommittedWorkBasis.UNDETERMINED
    assert result.paths == ("src/produced.py",)
    assert result.blocks_handoff is False, "a shared checkout must not block on someone else's dirt"


def test_write_handoff_refuses_a_stage_that_left_its_work_uncommitted(session, tmp_path):
    """AC3 end to end: the refusal reaches the agent, in-stage, naming the paths.

    The incident's stage completed 4/4 and was caught later. This is the same
    situation reaching the writer instead.
    """
    repo = make_repo(tmp_path, name="repo")
    gates = repo / "ci" / "scripts" / "gates"
    gates.mkdir(parents=True)
    (gates / "__init__.py").write_text("", encoding="utf-8")
    (repo / "project_board" / "checkpoints").mkdir(parents=True)
    ws, ticket = _seed(session, repo)
    _record_paths(session, ticket, ws, ["src/produced.py"])
    _dirty(repo)

    with pytest.raises(HandoffWriteError) as excinfo:
        write_handoff(
            session,
            ticket_id="t1",
            workspace_slug="wsx",
            from_agent="backend_implementer",
            to_agent="verifier",
            checklist=[
                {
                    "item_key": "impl_ac_complete",
                    "item": "All acceptance criteria implemented",
                    "status": "complete",
                    "evidence": "done",
                }
            ],
        )

    assert "src/produced.py" in str(excinfo.value)
    assert "Commit them" in str(excinfo.value)


def test_write_handoff_still_writes_when_the_work_is_committed(session, tmp_path):
    """The control. Without it, refusing every handoff passes the test above."""
    repo = make_repo(tmp_path, name="repo")
    (repo / "project_board" / "checkpoints").mkdir(parents=True)
    ws, ticket = _seed(session, repo)
    _record_paths(session, ticket, ws, ["src/produced.py"])
    _dirty(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "commit the work")

    result = write_handoff(
        session,
        ticket_id="t1",
        workspace_slug="wsx",
        from_agent="backend_implementer",
        to_agent="verifier",
        checklist=[
            {
                "item_key": "impl_ac_complete",
                "item": "All acceptance criteria implemented",
                "status": "complete",
                "evidence": "done",
            }
        ],
    )

    assert result["status"] == "stored_unvalidated", result
