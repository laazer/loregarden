"""Tying a gate evaluation to the agent run whose work it judged.

`GateEvaluated.orchestration_run_id` is an orchestration run, never an agent
run, so nothing tied a gate outcome to the adapter and model that produced the
work. These tests pin the join (same rule for new events and old ones), the
adapter read from the run's own argv, and the worktree snapshot that lets a
failure be replayed from exactly what the gate saw.
"""

import json
import subprocess
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from loregarden.models.domain import (
    AgentRun,
    CliAdapter,
    DomainEvent,
    EventType,
    GateOutcome,
    TicketState,
)
from loregarden.services.gate_observability import record_gate_evaluation
from loregarden.services.gate_producer import (
    ProducerJoin,
    adapter_from_command,
    pick_producer,
    snapshot_worktree,
)
from loregarden.services.gate_runner import GateRunResult
from sqlmodel import Session, select
from tests.factories import make_agent_run, make_orchestration_run, make_ticket, make_workspace

GATE_AT = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("command", "adapter"),
    [
        ("/opt/homebrew/bin/claude -p --model claude-opus-5-5", CliAdapter.CLAUDE),
        ("cursor-agent --print --output-format stream-json", CliAdapter.CURSOR),
        ("[terminal-handoff] /usr/bin/claude --resume x", CliAdapter.CLAUDE),
        ("/usr/bin/codex exec", CliAdapter.CODEX),
        ("opencode run", CliAdapter.OPENCODE),
        (
            "/venv/bin/python -m loregarden.agents.executors.lmstudio_runner --model q",
            CliAdapter.LMSTUDIO,
        ),
        ("/venv/bin/python -m loregarden.agents.executors.local_runner", CliAdapter.LOCAL),
        ("/usr/local/bin/my-wrapper --print", None),
        ("", None),
    ],
)
def test_adapter_is_read_from_the_argv_that_ran(command, adapter):
    assert adapter_from_command(command) is adapter


def _run(
    run_id: str, *, orch: str | None, stage: str, finished_minutes_before: int | None
) -> AgentRun:
    return AgentRun(
        id=run_id,
        run_code=run_id,
        workspace_id="ws",
        agent_id=f"agent-{run_id}",
        orchestration_run_id=orch,
        stage_key=stage,
        command="claude -p",
        finished_at=(
            None
            if finished_minutes_before is None
            else GATE_AT - timedelta(minutes=finished_minutes_before)
        ),
    )


def test_prefers_the_latest_run_of_the_same_orchestration_and_stage():
    runs = [
        _run("old-same-orch", orch="o1", stage="implement", finished_minutes_before=30),
        _run("new-same-orch", orch="o1", stage="implement", finished_minutes_before=5),
        _run("newer-other-orch", orch="o2", stage="implement", finished_minutes_before=1),
        _run("other-stage", orch="o1", stage="script_review", finished_minutes_before=0),
    ]
    producer = pick_producer(runs, orchestration_run_id="o1", stage_key="implement", before=GATE_AT)
    assert (producer.agent_run_id, producer.join) == (
        "new-same-orch",
        ProducerJoin.ORCHESTRATION_RUN,
    )


def test_falls_back_to_the_ticket_and_matches_legacy_stage_spellings():
    runs = [_run("r1", orch="o2", stage="implement", finished_minutes_before=3)]
    producer = pick_producer(
        runs, orchestration_run_id="o1", stage_key="implementation", before=GATE_AT
    )
    assert (producer.agent_run_id, producer.join) == ("r1", ProducerJoin.TICKET)


def test_runs_finishing_after_the_gate_or_unfinished_are_not_producers():
    runs = [
        _run("after", orch="o1", stage="implement", finished_minutes_before=-1),
        _run("running", orch="o1", stage="implement", finished_minutes_before=None),
    ]
    assert (
        pick_producer(runs, orchestration_run_id="o1", stage_key="implement", before=GATE_AT)
        is None
    )


def _git(root, *args) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_snapshot_captures_uncommitted_and_untracked_work_without_touching_the_index(git_repo):
    (git_repo / "seed.txt").write_text("edited\n")
    (git_repo / "new_module.py").write_text("x = 1\n")
    status_before = _git(git_repo, "status", "--porcelain")

    snapshot = snapshot_worktree(git_repo)

    assert snapshot.error == ""
    assert snapshot.head_sha == _git(git_repo, "rev-parse", "HEAD")
    assert _git(git_repo, "ls-tree", "--name-only", snapshot.tree_sha).split() == [
        "new_module.py",
        "seed.txt",
    ]
    assert _git(git_repo, "cat-file", "-p", f"{snapshot.tree_sha}:seed.txt") == "edited"
    assert _git(git_repo, "status", "--porcelain") == status_before


def test_snapshot_outside_a_repository_reports_why(tmp_path):
    snapshot = snapshot_worktree(tmp_path)
    assert snapshot.head_sha is None
    assert snapshot.error


def test_recorded_gate_event_names_its_producer_and_tree(db_session: Session, git_repo):
    ws = make_workspace(db_session, slug="producer-ws", repo_path=str(git_repo))
    ticket = make_ticket(
        db_session, workspace_id=ws.id, external_id="PROD-1", state=TicketState.IN_PROGRESS
    )
    orch = make_orchestration_run(db_session, workspace_id=ws.id, ticket_id=ticket.id)
    run = make_agent_run(
        db_session,
        workspace_id=ws.id,
        ticket_id=ticket.id,
        orchestration_run_id=orch.id,
        agent_id="backend_implementer",
        stage_key="implement",
        command="/usr/local/bin/cursor-agent --print",
        model="gpt-5",
        finished_at=datetime.now(timezone.utc) - timedelta(seconds=5),
    )

    record_gate_evaluation(
        db_session,
        MagicMock(),
        ticket,
        orch,
        GateRunResult(ok=False, outcome=GateOutcome.FAILED, message="Would reformat: a.py"),
        from_stage="implement",
        to_stage="script_review",
    )

    event = db_session.exec(
        select(DomainEvent).where(DomainEvent.type == EventType.GATE_EVALUATED)
    ).one()
    payload = json.loads(event.payload_json)
    assert payload["agent_run_id"] == run.id
    assert (payload["agent_id"], payload["adapter"], payload["model"]) == (
        "backend_implementer",
        "cursor",
        "gpt-5",
    )
    assert payload["head_sha"] == _git(git_repo, "rev-parse", "HEAD")
    assert payload["tree_sha"]
    assert payload["snapshot_error"] is None
