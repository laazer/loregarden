"""A chat turn's run lease is renewed for as long as its agent is answering.

Chat turns run on their own thread outside `supervised`, so nothing renewed
them: `last_seen_at` stayed unset and `agent_run_lease_expired` judged the run
against `started_at`. Every Home turn that outran AGENT_RUN_LEASE was failed by
the reconciliation sweep while its agent was still working (7 home-chat runs in
the fortnight to 2026-11-12, `last_seen_at` never stamped on any of 43).

Both turn paths in `agent_turn_runner` are covered: the permission bridge
(claude) and the one-shot CLI (codex, cursor, a read-only turn). The check runs
*during* the turn, with `started_at` pushed past the lease, because
`lease_renewal` stops when the body returns and a check afterwards would pass
against a renewer that fired once and died.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from loregarden.agents.executors.permission_bridge import HOME_CHAT_STAGE_KEY, BridgeResult
from loregarden.models.domain import AgentRun, RunStatus, Workspace
from loregarden.services import agent_turn_runner, baxter_chat_service
from loregarden.services.agent_turn_runner import AgentTurnRequest
from loregarden.services.run_lease import AGENT_RUN_LEASE, agent_run_lease_expired
from sqlalchemy.engine import Engine
from sqlmodel import Session, select


def _expired_while_running(engine: Engine, run_id: str) -> bool:
    """Push the run's start past the lease, then report whether it still reads
    as expired once the renewer has had time to stamp it.

    A fresh session, because the renewer commits on its own connection. Polled,
    because `lease_renewal` yields before its first stamp is guaranteed to land.
    """
    stale = datetime.now(timezone.utc) - (AGENT_RUN_LEASE + timedelta(minutes=1))
    with Session(engine) as probe:
        run = probe.get(AgentRun, run_id)
        assert run is not None
        assert run.status == RunStatus.RUNNING
        run.started_at = stale
        probe.add(run)
        probe.commit()

    deadline = time.monotonic() + 10.0
    expired = True
    while time.monotonic() < deadline:
        with Session(engine) as probe:
            run = probe.get(AgentRun, run_id)
            assert run is not None
            expired = agent_run_lease_expired(probe, run)
        if not expired:
            break
        time.sleep(0.05)
    return expired


def test_a_home_chat_turn_is_renewed_while_the_bridge_runs(
    client: TestClient, monkeypatch, isolated_db: Engine
) -> None:
    observed: dict[str, bool] = {}

    def fake_bridge_run(self, **kwargs):
        observed["expired"] = _expired_while_running(isolated_db, kwargs["run_id"])
        return BridgeResult(status=RunStatus.SUCCEEDED, stdout="done", stderr="")

    monkeypatch.delenv("LOREGARDEN_BAXTER_CHAT_STUB_RESPONSE", raising=False)
    monkeypatch.setattr(baxter_chat_service, "resolve_effective_adapter", lambda **_: "claude")
    monkeypatch.setattr(agent_turn_runner, "resolve_effective_adapter", lambda **_: "claude")
    monkeypatch.setattr(
        agent_turn_runner,
        "build_interactive_invocation",
        lambda **_: MagicMock(adapter="claude", argv=["claude"], cwd="/tmp", resume_session_id=""),
    )
    monkeypatch.setattr(
        agent_turn_runner, "resolve_workspace_root", lambda _ws: MagicMock(is_dir=lambda: True)
    )
    monkeypatch.setattr(agent_turn_runner.PermissionBridgeRunner, "run", fake_bridge_run)
    monkeypatch.setattr(agent_turn_runner, "extract_triage_reply", lambda stdout: stdout)

    session_id = client.post("/api/workspaces/loregarden/baxter-chat/sessions", json={}).json()[
        "id"
    ]
    res = client.post(
        f"/api/workspaces/loregarden/baxter-chat/sessions/{session_id}/messages",
        json={"content": "Investigate the inbox"},
    )
    assert res.status_code == 202

    assert observed == {"expired": False}, (
        "the home chat run stayed past its lease while the agent was answering, so "
        "the reconciliation sweep would fail it mid-turn"
    )


def test_a_one_shot_turn_renews_the_run_it_was_handed(
    db_session: Session, monkeypatch, isolated_db: Engine
) -> None:
    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    run = AgentRun(
        run_code="oneshot_lease",
        workspace_id=workspace.id,
        agent_id="triage",
        stage_key=HOME_CHAT_STAGE_KEY,
        status=RunStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    observed: dict[str, bool] = {}

    def fake_oneshot(_profile, **_kwargs):
        observed["expired"] = _expired_while_running(isolated_db, run.id)
        return "answered"

    monkeypatch.setattr(agent_turn_runner, "run_cli_agent_turn", fake_oneshot)
    monkeypatch.setattr(agent_turn_runner, "_resolve_turn_root", lambda _req, _run: "/tmp")

    request = AgentTurnRequest(
        session=db_session,
        workspace=workspace,
        prompt="p",
        profile=MagicMock(),
        agent={},
        intent="advisory",
        run_id=run.id,
    )
    reply, run_id = agent_turn_runner._run_oneshot(request, read_only=True)

    assert (reply, run_id) == ("answered", run.id)
    assert observed == {"expired": False}, (
        "a one-shot turn left the run it was handed unrenewed while the agent answered"
    )
