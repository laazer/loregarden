"""A long triage reply must reach the chat whole, or say that it was cut.

The triage chat cut every reply at 8,000 characters with no marker and no log:
`TRIAGE_CLI_PROFILE.reply_cap` sliced it inside both executor paths (the Claude
permission bridge and the oneshot CLI runner), and `TriageTurnExecutor` sliced
it again at `[:8000]` before saving it. A long answer simply stopped
mid-sentence, which is the silent failure CLAUDE.md forbids.

`test_reply_cap.py` covers the stubbed and read-only saved-reply paths. These
cover what it does not: the acting path, where a work note is appended after the
cut, and the two executor paths themselves. Each drives a reply well past the
old cap and accepts either fix: the reply is kept whole (its tail survives), or
the cut is visible in the text itself.

The tests are `xfail(strict=True)` until the fix lands, following
`test_open_defect_repros.py`: the suite stays green while the cap exists, and
the fix's branch goes red on XPASS until it deletes the markers here. Setup
lives in fixtures so a broken harness errors instead of passing as XFAIL.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from loregarden.agents.executors.permission_bridge import HOME_CHAT_STAGE_KEY, BridgeResult
from loregarden.models.domain import RunStatus, Ticket, TriageMessage, Workspace
from loregarden.services import agent_turn_runner, cli_agent_runner, triage_run_service
from loregarden.services.agent_turn_runner import (
    AgentTurnRequest,
    AgentTurnResult,
    run_agent_turn,
)
from loregarden.services.cli_agent_runner import run_cli_agent_turn
from loregarden.services.triage_run_service import TriageTurnExecutor, start_triage_run
from loregarden.services.triage_service import TRIAGE_CLI_PROFILE
from sqlmodel import Session, select

OPEN_DEFECT = pytest.mark.xfail(
    strict=True,
    reason="open: triage replies are cut at 8,000 chars with no marker (reply_cap / [:8000])",
)

TAIL = "END-OF-TRIAGE-REPLY"
LONG_REPLY = ("Baxter explains the ticket at length. " * 600) + TAIL
assert len(LONG_REPLY) > 2 * 8000


def _kept_whole_or_marked(content: str) -> bool:
    return TAIL in content or "truncated" in content.lower()


def _last_triage_message(session: Session, ticket_id: str) -> str:
    messages = session.exec(
        select(TriageMessage)
        .where(TriageMessage.ticket_id == ticket_id)
        .where(TriageMessage.role == "assistant")
    ).all()
    assert messages, "the turn saved no assistant reply"
    return messages[-1].content


@pytest.fixture
def ticket(client: TestClient, db_session: Session) -> Ticket:
    found = db_session.get(Ticket, client.get("/api/tickets").json()[0]["id"])
    assert found is not None
    return found


@pytest.fixture
def workspace(client: TestClient, db_session: Session) -> Workspace:
    found = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).first()
    assert found is not None
    return found


@pytest.fixture
def long_acting_turn(monkeypatch):
    """An acting (codex) `run_agent_turn` answering with LONG_REPLY."""
    monkeypatch.delenv(TRIAGE_CLI_PROFILE.stub_env, raising=False)
    adapter = "codex"

    def fake_turn(turn: AgentTurnRequest) -> AgentTurnResult:
        return AgentTurnResult(
            reply=LONG_REPLY, strategy="oneshot", adapter=adapter, run_id=turn.run_id
        )

    with (
        patch.object(triage_run_service, "resolve_chat_adapter", return_value=adapter),
        patch.object(triage_run_service, "run_agent_turn", side_effect=fake_turn),
        # The acting path appends a "where your work landed" note; its contents
        # are not under test, only that the answer above it survives.
        patch.object(TriageTurnExecutor, "_work_note", return_value="\n\n(work note)"),
    ):
        yield


@pytest.fixture
def bridge_answering_long(monkeypatch):
    """The Claude permission bridge, answering with LONG_REPLY on stdout."""
    monkeypatch.delenv("LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE", raising=False)
    patches = [
        patch.object(
            agent_turn_runner,
            "build_interactive_invocation",
            return_value=MagicMock(adapter="claude", argv=["claude"], cwd="/tmp"),
        ),
        patch.object(
            agent_turn_runner,
            "resolve_workspace_root",
            return_value=MagicMock(is_dir=lambda: True),
        ),
        patch.object(
            agent_turn_runner.PermissionBridgeRunner,
            "run",
            return_value=BridgeResult(status=RunStatus.SUCCEEDED, stdout=LONG_REPLY, stderr=""),
        ),
        patch.object(agent_turn_runner, "extract_triage_reply", side_effect=lambda out: out),
    ]
    for p in patches:
        p.start()
    yield
    for p in patches:
        p.stop()


class _FakeProc:
    returncode = 0
    stdin = None
    stderr = None

    def __init__(self, text: str) -> None:
        self._text = text
        self.stdout = MagicMock()

    def communicate(self, timeout=None):
        return (self._text.encode("utf-8"), b"")

    def kill(self) -> None:
        return None


@pytest.fixture
def cli_answering_long():
    """The oneshot CLI subprocess, printing LONG_REPLY and exiting 0."""
    patches = [
        patch.object(
            cli_agent_runner,
            "build_triage_invocation",
            return_value=MagicMock(argv=["agent"], cwd="", stdin_prompt=""),
        ),
        patch.object(
            cli_agent_runner.subprocess, "Popen", side_effect=lambda *a, **k: _FakeProc(LONG_REPLY)
        ),
    ]
    for p in patches:
        p.start()
    yield
    for p in patches:
        p.stop()


@OPEN_DEFECT
def test_acting_turn_keeps_a_long_reply_above_its_work_note(
    client: TestClient, db_session: Session, ticket: Ticket, long_acting_turn
):
    _, run = start_triage_run(db_session, ticket, "explain everything")
    TriageTurnExecutor(db_session).execute(run, ticket)

    assert _kept_whole_or_marked(_last_triage_message(db_session, ticket.id))


@OPEN_DEFECT
def test_permission_bridge_returns_a_long_reply_whole(
    db_session: Session, workspace: Workspace, bridge_answering_long
):
    result = run_agent_turn(
        AgentTurnRequest(
            session=db_session,
            workspace=workspace,
            prompt="explain everything",
            profile=TRIAGE_CLI_PROFILE,
            agent={},
            intent="execute",
            adapter="claude",
            stage_key=HOME_CHAT_STAGE_KEY,
        )
    )

    assert result.strategy == "permission_bridge"
    assert _kept_whole_or_marked(result.reply)


@OPEN_DEFECT
def test_oneshot_cli_returns_a_long_reply_whole(
    workspace: Workspace, tmp_path: Path, cli_answering_long
):
    reply = run_cli_agent_turn(
        TRIAGE_CLI_PROFILE,
        workspace=workspace,
        prompt="explain everything",
        workspace_root=tmp_path,
    )

    assert _kept_whole_or_marked(reply)
