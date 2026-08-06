"""Shared agent-turn execution for Home chat, triage, and related surfaces.

Adapters differ in protocol (Claude stream-json bridge vs Codex/Cursor/LM Studio
oneshot), but every surface should pick a strategy the same way:

* resolve the effective adapter
* decide intent (advisory vs execute)
* run through this module

That keeps switching providers a runtime choice instead of a per-surface fork.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from loregarden.agents.cli_adapters import build_interactive_invocation
from loregarden.agents.executors.permission_bridge import PermissionBridgeRunner
from loregarden.models.domain import AgentRun, RunStatus, Ticket, Workspace
from loregarden.services.chat_thinking import ChatTurnThinkingSink
from loregarden.services.cli_agent_runner import (
    CliAgentProfile,
    resolve_agent_timeout,
    run_cli_agent_turn,
)
from loregarden.services.cli_output import extract_triage_reply
from loregarden.services.cli_settings import (
    resolve_effective_adapter,
    resolve_effort_for_adapter,
    resolve_model_for_adapter,
)
from loregarden.services.run_concurrency import (
    find_active_workspace_chat_run,
    new_run_code,
)
from loregarden.services.workspace_paths import resolve_workspace_root
from sqlmodel import Session

TurnIntent = Literal["advisory", "execute"]
TurnStrategy = Literal["permission_bridge", "writable_oneshot", "advisory_oneshot"]


@dataclass(frozen=True)
class AdapterCapabilities:
    """What the operator can expect after switching the runtime picker."""

    adapter: str
    permission_bridge: bool
    inbox_approvals: bool
    plan_execute: bool
    stream_thinking: bool
    steer: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "adapter": self.adapter,
            "permission_bridge": self.permission_bridge,
            "inbox_approvals": self.inbox_approvals,
            "plan_execute": self.plan_execute,
            "stream_thinking": self.stream_thinking,
            "steer": self.steer,
        }


def adapter_capabilities(adapter: str) -> AdapterCapabilities:
    """Capability matrix for the resolved CLI adapter id."""
    selected = (adapter or "claude").strip() or "claude"
    if selected == "claude":
        return AdapterCapabilities(
            adapter=selected,
            permission_bridge=True,
            inbox_approvals=True,
            plan_execute=True,
            stream_thinking=True,
            steer=True,
        )
    if selected == "cursor":
        return AdapterCapabilities(
            adapter=selected,
            permission_bridge=False,
            inbox_approvals=False,
            plan_execute=True,
            stream_thinking=True,
            steer=False,
        )
    if selected in {"codex", "lmstudio"}:
        return AdapterCapabilities(
            adapter=selected,
            permission_bridge=False,
            inbox_approvals=False,
            plan_execute=True,
            stream_thinking=False,
            steer=False,
        )
    return AdapterCapabilities(
        adapter=selected,
        permission_bridge=False,
        inbox_approvals=False,
        plan_execute=False,
        stream_thinking=False,
        steer=False,
    )


def capabilities_for_workspace(
    workspace: Workspace, *, agent_adapter: str = "claude"
) -> AdapterCapabilities:
    selected = resolve_effective_adapter(agent_adapter=agent_adapter, workspace=workspace)
    return adapter_capabilities(selected)


def resolve_turn_strategy(adapter: str, intent: TurnIntent) -> TurnStrategy:
    """Pick the execution strategy for this adapter + intent.

    Same map everywhere: Claude execute → permission bridge; other execute-capable
    adapters → writable oneshot; otherwise advisory oneshot.
    """
    caps = adapter_capabilities(adapter)
    if intent == "execute" and caps.permission_bridge:
        return "permission_bridge"
    if intent == "execute" and caps.plan_execute:
        return "writable_oneshot"
    return "advisory_oneshot"


@dataclass
class AgentTurnRequest:
    session: Session
    workspace: Workspace
    prompt: str
    profile: CliAgentProfile
    agent: dict
    intent: TurnIntent
    user_prompt: str | None = None
    turn_id: str = ""
    stage_key: str = ""
    agent_id: str = ""
    run_id: str = ""
    manage_run: bool = False
    ticket: Ticket | None = None
    workspace_root: Path | None = None
    workspace_stage_key: str = ""
    claude_model_env: str = ""
    conflict_error: Callable[[str], Exception] | None = None
    track_workflow_stage: bool = False
    adapter: str = ""
    """Pre-resolved adapter. When set, the runner does not re-resolve."""


@dataclass
class AgentTurnResult:
    reply: str
    strategy: TurnStrategy
    adapter: str
    run_id: str = ""


def run_agent_turn(request: AgentTurnRequest) -> AgentTurnResult:
    """Execute one turn with the shared adapter strategy map."""
    selected = (request.adapter or "").strip() or resolve_effective_adapter(
        agent_adapter=request.agent.get("adapter", "claude"),
        workspace=request.workspace,
    )
    strategy = resolve_turn_strategy(selected, request.intent)
    if strategy == "permission_bridge":
        reply, run_id = _run_permission_bridge(request)
    elif strategy == "writable_oneshot":
        reply, run_id = _run_oneshot(request, read_only=False)
    else:
        reply, run_id = _run_oneshot(request, read_only=True)
    return AgentTurnResult(
        reply=reply, strategy=strategy, adapter=selected, run_id=run_id
    )


def _start_run(request: AgentTurnRequest) -> AgentRun:
    if request.run_id:
        run = request.session.get(AgentRun, request.run_id)
        if not run:
            raise ValueError(f"Agent run not found: {request.run_id}")
        return run

    stage_key = request.stage_key
    if not stage_key:
        raise ValueError("stage_key is required when creating a run")

    if find_active_workspace_chat_run(
        request.session, request.workspace.id, stage_key=stage_key
    ):
        message = "An agent turn is already running — wait for it to finish."
        if request.conflict_error:
            raise request.conflict_error(message)
        raise RuntimeError(message)

    run = AgentRun(
        run_code=new_run_code(),
        ticket_id=request.ticket.id if request.ticket else None,
        workspace_id=request.workspace.id,
        agent_id=request.agent_id or "triage",
        stage_key=stage_key,
        status=RunStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    request.session.add(run)
    request.session.commit()
    request.session.refresh(run)
    return run


def _finish_run(session: Session, run_id: str, *, status: RunStatus, stderr: str) -> None:
    run = session.get(AgentRun, run_id)
    if not run:
        return
    run.status = status
    run.stderr = stderr[:4000]
    run.finished_at = datetime.now(timezone.utc)
    session.add(run)
    session.commit()


def _run_permission_bridge(request: AgentTurnRequest) -> tuple[str, str]:
    root = request.workspace_root or resolve_workspace_root(request.workspace)
    if not root.is_dir():
        raise ValueError(f"Workspace repo path does not exist: {root}")

    model_env = request.claude_model_env.strip()
    claude_model = (
        (os.environ.get(model_env, "").strip() if model_env else "")
        or resolve_model_for_adapter("claude", request.workspace)
        or "haiku"
    )
    timeout = resolve_agent_timeout(request.agent, request.profile.timeout_env)
    run = _start_run(request)
    thinking = ChatTurnThinkingSink(request.turn_id) if request.turn_id else None
    try:
        with TemporaryDirectory(prefix=request.profile.tmp_prefix) as tmp:
            prompt_file = Path(tmp) / "prompt.md"
            prompt_file.write_text(request.prompt, encoding="utf-8")
            invocation = build_interactive_invocation(
                adapter="claude",
                prompt_file=prompt_file,
                workspace_root=root,
                claude_model=claude_model,
                claude_effort=resolve_effort_for_adapter("claude", request.workspace),
                partial_messages=thinking is not None,
                db_session=request.session,
            )
            bridge_kwargs: dict = {
                "run_id": run.id,
                "invocation": invocation,
                "prompt": request.prompt,
                "timeout_seconds": timeout,
                "streamer": thinking,
            }
            if request.ticket is not None:
                bridge_kwargs["ticket"] = request.ticket
            else:
                bridge_kwargs["workspace"] = request.workspace
                if request.workspace_stage_key:
                    bridge_kwargs["workspace_stage_key"] = request.workspace_stage_key
            result = PermissionBridgeRunner(
                request.session, track_workflow_stage=request.track_workflow_stage
            ).run(**bridge_kwargs)
    except Exception as exc:
        if request.manage_run:
            _finish_run(request.session, run.id, status=RunStatus.FAILED, stderr=str(exc))
        raise
    finally:
        if thinking:
            thinking.close()

    reply = extract_triage_reply(result.stdout)[: request.profile.reply_cap]
    if result.status != RunStatus.SUCCEEDED:
        if request.manage_run:
            _finish_run(
                request.session, run.id, status=result.status, stderr=result.stderr
            )
        raise RuntimeError(result.stderr or f"Agent run {result.status.value}")
    if not reply:
        if request.manage_run:
            _finish_run(
                request.session,
                run.id,
                status=RunStatus.FAILED,
                stderr=result.stderr or "empty response",
            )
        raise RuntimeError("Agent returned an empty response")
    if request.manage_run:
        _finish_run(request.session, run.id, status=RunStatus.SUCCEEDED, stderr="")
    return reply, run.id


def _run_oneshot(request: AgentTurnRequest, *, read_only: bool) -> tuple[str, str]:
    run: AgentRun | None = None
    run_id = request.run_id
    if request.manage_run and not read_only:
        run = _start_run(request)
        run_id = run.id

    thinking = ChatTurnThinkingSink(request.turn_id) if request.turn_id else None
    try:
        reply = run_cli_agent_turn(
            request.profile,
            workspace=request.workspace,
            prompt=request.prompt,
            user_prompt=request.user_prompt,
            run_id="" if read_only else run_id,
            workspace_slug=request.workspace.slug or "",
            granted_tools=(
                None if read_only else list(request.agent.get("mcp_tools") or [])
            ),
            read_only=read_only,
            thinking_sink=thinking,
        )
    except Exception as exc:
        if run is not None and request.manage_run:
            _finish_run(request.session, run.id, status=RunStatus.FAILED, stderr=str(exc))
        raise
    finally:
        if thinking:
            thinking.close()

    if run is not None and request.manage_run:
        _finish_run(request.session, run.id, status=RunStatus.SUCCEEDED, stderr="")
    return reply, run_id or ""
