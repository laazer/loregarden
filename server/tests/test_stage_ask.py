"""A running stage asks an earlier stage on its ticket one question (`services.stage_ask`)."""

from __future__ import annotations

import json
import subprocess
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from loregarden.agents.cli_adapters import ran_on_claude
from loregarden.agents.mcp_context import build_mcp_run_context
from loregarden.agents.stage_digest import earlier_stage_runs
from loregarden.config import settings
from loregarden.mcp.tool_ids import AUTO_APPROVED_MCP_TOOLS, STAGE_DEFAULT_MCP_TOOLS, McpTool
from loregarden.mcp.tools import execute_tool
from loregarden.models.domain import (
    AgentRun,
    ControlPlaneTransport,
    RunStatus,
    Ticket,
    WorkflowStageDef,
    Workspace,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services import stage_ask
from loregarden.services.rework_resume import _prior_run
from loregarden.services.session_prompt import keep_session_prompt, session_prompt_path
from loregarden.testing.factories import make_orchestration_run
from sqlmodel import Session, select
from tests.factories import make_workspace_ticket

TOOL = McpTool.ASK_EARLIER_STAGE.value
TARGET_SESSION = "sess-plan-1111"
FORK_SESSION = "sess-fork-2222"
KEPT_PROMPT = "# Run: run_plan\nYou are the planner. Emit a stage report."
ANSWER = "parse_header lives in server/loregarden/io/headers.py:40; we kept it pure."
CLAUDE_COMMAND = "/usr/local/bin/claude -p --verbose --output-format stream-json"


def _stream(*, result: str = ANSWER, is_error: bool = False) -> str:
    events = [
        {"type": "system", "subtype": "init", "session_id": FORK_SESSION},
        {
            "type": "result",
            "subtype": "error_during_execution" if is_error else "success",
            "is_error": is_error,
            "result": result,
            "session_id": FORK_SESSION,
            "usage": {
                "input_tokens": 12,
                "output_tokens": 80,
                "cache_read_input_tokens": 90_000,
                "cache_creation_input_tokens": 1_500,
            },
        },
    ]
    return "\n".join(json.dumps(event) for event in events) + "\n"


def _completed(stdout: str, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture(autouse=True)
def run_log_dir(tmp_path: Path):
    with patch.object(settings, "run_log_dir", str(tmp_path / "runs")):
        yield


@pytest.fixture
def ticket(db_session: Session) -> Ticket:
    return make_workspace_ticket(db_session, "stage-ask")


@pytest.fixture
def orch_id(db_session: Session, ticket: Ticket) -> str:
    return make_orchestration_run(
        db_session, workspace_id=ticket.workspace_id, ticket_id=ticket.id
    ).id


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    path = tmp_path / "ticket-worktree"
    path.mkdir()
    return path


def _run(
    session: Session,
    ticket: Ticket,
    stage_key: str,
    *,
    orch_id: str | None,
    agent_id: str,
    minutes_ago: int = 0,
    status: RunStatus = RunStatus.SUCCEEDED,
    session_id: str = "",
    command: str = CLAUDE_COMMAND,
    repo: Path | None = None,
) -> AgentRun:
    run = AgentRun(
        run_code=f"run_{stage_key}_{minutes_ago}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        orchestration_run_id=orch_id,
        agent_id=agent_id,
        stage_key=stage_key,
        status=status,
        cli_session_id=session_id,
        command=command,
        start_repo_path=str(repo) if repo else "",
        model="claude-opus-5",
        created_at=utcnow() - timedelta(minutes=minutes_ago),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


@pytest.fixture
def target(db_session: Session, ticket: Ticket, orch_id: str, checkout: Path) -> AgentRun:
    run = _run(
        db_session, ticket, "plan", orch_id=orch_id, agent_id="planner", minutes_ago=20,
        session_id=TARGET_SESSION, repo=checkout,
    )  # fmt: skip
    keep_session_prompt(db_session, run, KEPT_PROMPT)
    return run


@pytest.fixture
def asker(db_session: Session, ticket: Ticket, orch_id: str, target: AgentRun) -> AgentRun:
    return _run(
        db_session, ticket, "implement", orch_id=orch_id, agent_id="implementer",
        status=RunStatus.RUNNING,
    )  # fmt: skip


def _ask(
    session: Session, ticket: Ticket, asker: AgentRun, stage_key: str = "plan", **kwargs
) -> dict:
    reply = execute_tool(
        session,
        TOOL,
        {"ticket_id": ticket.external_id, "stage_key": stage_key, "question": "Where?", **kwargs},
        orchestrated=True,
        run_id=asker.id,
    )
    return json.loads(reply)


def _asks(session: Session, asker: AgentRun) -> list[AgentRun]:
    return list(session.exec(select(AgentRun).where(AgentRun.asked_by_run_id == asker.id)).all())


def _flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


# ---- the happy path ------------------------------------------------------------------


def test_an_ask_forks_the_earlier_session_read_only_under_its_kept_prompt(
    db_session, ticket, target, asker, checkout
):
    with patch.object(stage_ask.subprocess, "run", return_value=_completed(_stream())) as spawn:
        reply = _ask(db_session, ticket, asker)

    assert reply["answer"] == ANSWER
    assert reply["answered_by_run"] == target.run_code
    assert "note" not in reply
    argv = spawn.call_args.args[0]
    assert _flag(argv, "--resume") == TARGET_SESSION
    assert "--fork-session" in argv
    assert _flag(argv, "--append-system-prompt-file") == str(session_prompt_path(target))
    assert session_prompt_path(target).read_text(encoding="utf-8") == KEPT_PROMPT
    assert _flag(argv, "--model") == "claude-opus-5"
    assert set(_flag(argv, "--allowedTools").split(",")) == {"Read", "Glob", "Grep"}
    assert {"Bash", "Edit", "Write", "NotebookEdit", "Task", "WebFetch"} <= set(
        _flag(argv, "--disallowedTools").split(",")
    )
    assert _flag(argv, "--permission-mode") == "dontAsk"
    assert "--strict-mcp-config" in argv and "--mcp-config" not in argv
    assert _flag(argv, "--max-budget-usd") == stage_ask.ASK_MAX_BUDGET_USD
    assert spawn.call_args.kwargs["cwd"] == str(checkout)
    assert spawn.call_args.kwargs["timeout"] == stage_ask.ASK_TIMEOUT_SECONDS


def test_the_ask_is_its_own_run_linked_to_both_sides_with_its_cost(
    db_session, ticket, target, asker
):
    with patch.object(stage_ask.subprocess, "run", return_value=_completed(_stream())):
        reply = _ask(db_session, ticket, asker)

    [ask] = _asks(db_session, asker)
    assert ask.run_code == reply["ask_run"]
    assert ask.resumed_from_run_id == target.id
    assert ask.status is RunStatus.SUCCEEDED and ask.finished_at is not None
    assert ask.orchestration_run_id is None
    assert ask.cli_session_id == FORK_SESSION
    assert (ask.cache_read_tokens, ask.cache_write_tokens, ask.output_tokens) == (90_000, 1_500, 80)
    # The earlier run is untouched: its session is the one later rounds resume.
    db_session.refresh(target)
    assert target.cli_session_id == TARGET_SESSION


def test_an_ask_is_never_taken_for_stage_work(db_session, ticket, target, asker, orch_id):
    with patch.object(stage_ask.subprocess, "run", return_value=_completed(_stream())):
        _ask(db_session, ticket, asker)
    later = _run(db_session, ticket, "review", orch_id=orch_id, agent_id="static_qa")
    rework = _run(db_session, ticket, "plan", orch_id=orch_id, agent_id="planner")

    assert earlier_stage_runs(db_session, ticket, later)[0].id == target.id
    assert _prior_run(db_session, rework).id == target.id


def test_a_run_whose_prompt_was_not_kept_is_asked_without_one_and_says_so(
    db_session, ticket, target, asker
):
    session_prompt_path(target).unlink()
    with patch.object(stage_ask.subprocess, "run", return_value=_completed(_stream())) as spawn:
        reply = _ask(db_session, ticket, asker)

    assert "--append-system-prompt-file" not in spawn.call_args.args[0]
    assert "note" in reply


# ---- refusals: nothing is spawned, and the asker is told why --------------------------


def _refused(session, ticket, asker, match: str, stage_key: str = "plan", **kwargs) -> None:
    spawn = MagicMock()
    with patch.object(stage_ask.subprocess, "run", spawn), pytest.raises(ValueError, match=match):
        _ask(session, ticket, asker, stage_key, **kwargs)
    spawn.assert_not_called()


def test_no_such_earlier_run_is_refused_naming_the_stages_there_are(
    db_session, ticket, target, asker
):
    _refused(db_session, ticket, asker, r"no earlier stage 'spec'.*can ask: plan", "spec")


def test_a_failed_earlier_run_is_not_a_target(db_session, ticket, orch_id, checkout):
    _run(
        db_session, ticket, "spec", orch_id=orch_id, agent_id="spec", minutes_ago=5,
        status=RunStatus.FAILED, session_id="sess-spec", repo=checkout,
    )  # fmt: skip
    asker = _run(db_session, ticket, "implement", orch_id=orch_id, agent_id="implementer")
    _refused(db_session, ticket, asker, "no earlier stage 'spec'", "spec")


def test_a_run_with_no_session_is_refused(db_session, ticket, orch_id, checkout):
    _run(
        db_session,
        ticket,
        "plan",
        orch_id=orch_id,
        agent_id="planner",
        minutes_ago=5,
        repo=checkout,
    )
    asker = _run(db_session, ticket, "implement", orch_id=orch_id, agent_id="implementer")
    _refused(db_session, ticket, asker, "recorded no session")


def test_a_run_on_another_adapter_is_refused(db_session, ticket, orch_id, checkout):
    _run(
        db_session, ticket, "plan", orch_id=orch_id, agent_id="planner", minutes_ago=5,
        session_id="sess-cursor", command="/usr/local/bin/cursor-agent -p --resume x", repo=checkout,
    )  # fmt: skip
    asker = _run(db_session, ticket, "implement", orch_id=orch_id, agent_id="implementer")
    _refused(db_session, ticket, asker, "another adapter than claude")


def test_a_run_whose_checkout_is_gone_is_refused(db_session, ticket, orch_id, tmp_path):
    _run(
        db_session, ticket, "plan", orch_id=orch_id, agent_id="planner", minutes_ago=5,
        session_id="sess-plan", repo=tmp_path / "reaped",
    )  # fmt: skip
    asker = _run(db_session, ticket, "implement", orch_id=orch_id, agent_id="implementer")
    _refused(db_session, ticket, asker, "which is gone")


def test_asking_your_own_stage_is_refused(db_session, ticket, target, asker):
    _refused(db_session, ticket, asker, "your own stage", "implement")


def test_a_caller_that_is_not_a_stage_run_is_refused(db_session, ticket, target):
    spawn = MagicMock()
    with (
        patch.object(stage_ask.subprocess, "run", spawn),
        pytest.raises(ValueError, match="only a stage run"),
    ):
        execute_tool(
            db_session, TOOL, {"ticket_id": ticket.id, "stage_key": "plan", "question": "Where?"}
        )
    spawn.assert_not_called()


def test_another_tickets_stage_is_refused(db_session, ticket, target, asker):
    other = make_workspace_ticket(db_session, "stage-ask-other")
    spawn = MagicMock()
    with (
        patch.object(stage_ask.subprocess, "run", spawn),
        pytest.raises(ValueError, match="another ticket"),
    ):
        execute_tool(
            db_session,
            TOOL,
            {"ticket_id": other.id, "stage_key": "plan", "question": "Where?"},
            orchestrated=True,
            run_id=asker.id,
        )
    spawn.assert_not_called()


def test_a_run_may_ask_only_so_many_times(db_session, ticket, target, asker):
    with patch.object(stage_ask.subprocess, "run", return_value=_completed(_stream())):
        for _ in range(stage_ask.MAX_ASKS_PER_RUN):
            _ask(db_session, ticket, asker)
    _refused(db_session, ticket, asker, "the most one run may")


def test_an_empty_question_is_refused(db_session, ticket, target, asker):
    _refused(db_session, ticket, asker, "empty", question="   ")


# ---- judging stages do not ask --------------------------------------------------------


@pytest.mark.parametrize(
    ("stage_key", "agent_id"),
    [
        ("review", "architecture_reviewer"),
        ("review", "static_qa"),
        ("verify", "verifier"),
        ("gate", "gatekeeper"),
    ],
)
def test_a_judging_stage_may_not_ask_the_producers(
    db_session, ticket, target, orch_id, stage_key, agent_id
):
    judge = _run(db_session, ticket, stage_key, orch_id=orch_id, agent_id=agent_id)
    _refused(db_session, ticket, judge, "judging stage")


def test_the_prompt_offers_the_ask_to_producers_and_not_to_judges(db_session, ticket, asker):
    workspace = db_session.get(Workspace, ticket.workspace_id)

    def context(offer: bool) -> str:
        return build_mcp_run_context(
            ticket=ticket,
            run=asker,
            workspace=workspace,
            stage_def=WorkflowStageDef(key="implement", name="Implement", order=6),
            transport=ControlPlaneTransport.MCP,
            offer_stage_ask=offer,
        )

    assert TOOL in context(True).split("Available tools:")[0]
    assert TOOL not in context(False).split("Available tools:")[0]


# ---- the turn fails: the asker hears why, and the run says so --------------------------


@pytest.mark.parametrize(
    ("completed", "reason"),
    [
        (
            _completed(_stream(result="Exceeded budget", is_error=True), returncode=1),
            "Exceeded budget",
        ),
        (_completed("", returncode=1, stderr="No conversation found"), "No conversation found"),
        (_completed(_stream(result="  ")), "no answer"),
    ],
)
def test_a_failed_turn_is_an_error_and_a_failed_run(
    db_session, ticket, target, asker, completed, reason
):
    with (
        patch.object(stage_ask.subprocess, "run", return_value=completed),
        pytest.raises(ValueError, match=reason),
    ):
        _ask(db_session, ticket, asker)

    [ask] = _asks(db_session, asker)
    assert ask.status is RunStatus.FAILED and reason in ask.stderr


def test_a_turn_that_runs_out_of_time_is_an_error_and_a_failed_run(
    db_session, ticket, target, asker
):
    timeout = subprocess.TimeoutExpired(cmd="claude", timeout=stage_ask.ASK_TIMEOUT_SECONDS)
    with (
        patch.object(stage_ask.subprocess, "run", side_effect=timeout),
        pytest.raises(ValueError, match="timed out"),
    ):
        _ask(db_session, ticket, asker)

    [ask] = _asks(db_session, asker)
    assert ask.status is RunStatus.FAILED


# ---- registration ---------------------------------------------------------------------


def test_every_stage_is_offered_the_tool_and_it_runs_unattended():
    assert McpTool.ASK_EARLIER_STAGE in STAGE_DEFAULT_MCP_TOOLS
    assert McpTool.ASK_EARLIER_STAGE in AUTO_APPROVED_MCP_TOOLS


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("/Users/x/.local/bin/claude -p --verbose", True),
        ("claude -p", True),
        ("/usr/local/bin/cursor-agent -p", False),
        ("[terminal-handoff] claude -p", False),
        ("", False),
    ],
)
def test_ran_on_claude_reads_the_recorded_binary(command, expected):
    with patch.dict("os.environ", {"LOREGARDEN_CLAUDE_BIN": "/opt/claude"}):
        assert ran_on_claude(command) is expected
