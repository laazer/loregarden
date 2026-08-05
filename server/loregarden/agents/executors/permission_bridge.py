"""Bridge external CLI permission prompts into Loregarden approvals."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlmodel import Session

if TYPE_CHECKING:
    from loregarden.services.run_stream_sink import RunStreamSink
from loregarden.agents.cli_adapters import CliInvocation
from loregarden.agents.executors.approval_scope import (  # noqa: F401
    BRANCH_TRIAGE_STAGE_KEY,
    HOME_CHAT_STAGE_KEY,
    ApprovalScope,
)
from loregarden.agents.executors.tool_auto_approve import (  # noqa: F401
    ASK_USER_QUESTION_TOOL,
    AUTO_APPROVED_CLI_TOOLS,
    AUTO_APPROVED_MCP_TOOLS,
    ORCHESTRATED_DENIED_MCP_TOOLS,
    bare_mcp_tool_name,
    build_ask_user_question_input,
    enrich_mcp_tool_input,
    is_ask_user_question,
    is_auto_approved_cli_tool,
    is_auto_approved_mcp_tool,
    is_orchestrated_agent_denied_mcp_tool,
    validate_question_answers,
)
from loregarden.agents.registry import get_agent
from loregarden.config import settings
from loregarden.db.session import engine
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    RunStatus,
    StageStatus,
    Ticket,
    Workspace,
)
from loregarden.services.agent_scope import (
    check_agent_scope,
    extract_target_path,
    owning_scoped_agent,
    relative_to_root,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.permission_allowlist import is_permission_allowed
from loregarden.services.rate_limit import rate_limit_denial
from loregarden.services.rework_feedback import record_reroute_exhausts_budget
from loregarden.services.run_cancellation import cancel_requested
from loregarden.services.run_errors import TIMEOUT_HARD_CAP_MULTIPLIER, agent_timeout_message
from loregarden.services.run_steering import (
    POLL_INTERVAL_SECONDS,
    mark_delivered,
    pending_messages,
)
from loregarden.services.studio_routing import resolve_scope_reroute_pin
from loregarden.services.subprocess_lines import SubprocessLineReader
from loregarden.services.tool_policy import (
    LOREGARDEN_SERVER,
    server_auto_approves,
    split_mcp_tool,
)
from loregarden.services.tool_telemetry import (
    DECISION_ALLOWLIST,
    DECISION_APPROVED,
    DECISION_RATE_LIMITED,
    DECISION_READ_ONLY_CLI,
    DECISION_REJECTED,
    DECISION_RUN_AUTO,
    DECISION_TRUSTED_SERVER,
    record_tool_call,
)
from loregarden.services.workflow_state import set_stage_status
from loregarden.services.workspace_paths import resolve_workspace_root

logger = logging.getLogger(__name__)

# Single durable budget for scope-denial handoffs on a ticket. All cross-scope
# reroutes share it (not one per direction) so a ticket that keeps bouncing
# between the frontend and backend implementers halts for a human after a few
# rounds instead of ping-ponging forever. Not a real stage key — only a counter.
_SCOPE_REROUTE_LEDGER_KEY = "scope-reroute"


@dataclass
class BridgeResult:
    status: RunStatus
    stdout: str
    stderr: str
    session_id: str = ""


def serialize_tool_input(tool_input: Any) -> str:
    """Persist full tool input JSON without truncation."""
    return json.dumps(tool_input, ensure_ascii=False)


def parse_stored_tool_input(raw: str) -> dict[str, Any]:
    """Parse stored tool input; tolerate legacy truncated payloads."""
    if not raw or raw == "{}":
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_ndjson_line(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def extract_permission_request(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize Claude/Cursor permission control messages."""
    msg_type = payload.get("type", "")
    if msg_type in {"control_request", "sdk_control_request"}:
        request = payload.get("request") or {}
        subtype = request.get("subtype", "")
        if subtype in {"permission", "can_use_tool"}:
            request_id = (
                payload.get("request_id") or request.get("request_id") or request.get("id") or ""
            )
            tool_name = request.get("tool_name") or request.get("tool") or "tool"
            tool_input = request.get("tool_input") or request.get("input") or {}
            return {
                "request_id": str(request_id),
                "tool_name": str(tool_name),
                "tool_input": tool_input,
                "raw": payload,
            }
    return None


@dataclass
class ApprovalResolution:
    approved: bool
    updated_input: dict[str, Any] | None = None
    message: str = ""


def build_control_response(
    *,
    request_id: str,
    approved: bool,
    message: str = "",
    updated_input: dict[str, Any] | None = None,
) -> dict[str, Any]:
    inner: dict[str, Any]
    if approved:
        inner = {"behavior": "allow"}
        # Empty updatedInput overwrites Claude's original tool args and breaks Bash/MCP.
        if updated_input:
            inner["updatedInput"] = updated_input
    else:
        inner = {"behavior": "deny", "message": message or "Denied via Loregarden inbox"}
    return {
        "type": "control_response",
        "response": {
            "subtype": "success",
            "request_id": request_id,
            "response": inner,
        },
    }


def build_user_message(prompt: str, *, session_id: str | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {
        "type": "user",
        "message": {"role": "user", "content": prompt},
        "parent_tool_use_id": None,
    }
    if session_id:
        message["session_id"] = session_id
    return message


def result_payload_status(payload: dict[str, Any]) -> tuple[bool, bool]:
    """Return (finished, failed) for Claude/Cursor stream-json result events."""
    if payload.get("type") != "result":
        return False, False
    failed = bool(payload.get("is_error")) or payload.get("subtype") == "error"
    return True, failed


def _close_stdin(proc: Any) -> None:
    stdin = getattr(proc, "stdin", None)
    if not stdin:
        return
    close = getattr(stdin, "close", None)
    if not callable(close):
        return
    try:
        close()
    except OSError:
        pass


def _drain_stdout_after_result(
    proc: Any,
    stdout_reader: SubprocessLineReader,
    stdout_lines: list[str],
    *,
    streamer: RunStreamSink | None,
    max_seconds: float = 5.0,
) -> None:
    """Read trailing stream-json lines and terminate a CLI that stayed alive after result."""
    import subprocess

    deadline = time.time() + max_seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        line = stdout_reader.readline(timeout=0.2)
        if line is None:
            continue
        line = line.rstrip("\n")
        stdout_lines.append(line)
        if streamer:
            streamer.append_stream_line(line)

    if proc.poll() is None:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def wait_for_approval_resolution(
    approval_id: str,
    *,
    poll_seconds: float = 2.0,
    timeout_seconds: float | None = None,
) -> ApprovalResolution:
    deadline = time.time() + (timeout_seconds or settings.permission_approval_timeout_seconds)
    while time.time() < deadline:
        resolution = poll_approval_resolution(approval_id)
        if resolution is not None:
            return resolution
        time.sleep(poll_seconds)
    raise TimeoutError(f"Timed out waiting for approval {approval_id}")


def poll_approval_resolution(approval_id: str) -> ApprovalResolution | None:
    """Return approval outcome when resolved; None while still pending."""
    with Session(engine) as session:
        approval = session.get(Approval, approval_id)
        if not approval:
            raise ValueError(f"Approval not found: {approval_id}")
        if approval.status == ApprovalStatus.APPROVED:
            stored = json.loads(approval.response_json or "{}")
            updated_input = stored.get("updated_input")
            if updated_input is None and approval.kind == ApprovalKind.CLI_PERMISSION:
                updated_input = parse_stored_tool_input(approval.tool_input_json)
            return ApprovalResolution(
                approved=True,
                updated_input=updated_input if isinstance(updated_input, dict) else None,
            )
        if approval.status == ApprovalStatus.REJECTED:
            return ApprovalResolution(
                approved=False,
                message="Rejected in Loregarden approval inbox",
            )
    return None


@dataclass
class _RunContext:
    """Static-for-the-run info resolved once up front."""

    workspace_slug: str
    workspace_root: str
    auto_approve: bool
    agent_id: str
    agent_name: str


@dataclass
class _LoopState:
    """Everything the permission loop mutates across iterations, bundled so
    it can be threaded through the extracted step handlers below."""

    stdout_lines: list[str]
    session_id: str
    last_persist: float
    pending_approval: Approval | None = None
    pending_request_id: str = ""
    pending_tool_input: dict[str, Any] | None = None
    pending_tool_name: str = ""
    pending_since: float = 0.0
    approval_deadline: float = 0.0
    finished_with_result: bool = False
    result_is_error: bool = False
    last_steer_poll: float = 0.0
    last_cancel_poll: float = 0.0
    idle_deadline: float = 0.0
    hard_deadline: float = 0.0


@dataclass
class _LoopStep:
    """What the driving while-loop in run() should do after a step handler
    runs: keep looping ("continue"), stop the loop ("break"), or return a
    result immediately ("return", with `result` set)."""

    action: str
    result: BridgeResult | None = None


def _continue_while_awaiting(
    *,
    streamer: RunStreamSink | None,
    stdout_reader: SubprocessLineReader,
    state: _LoopState,
    timeout_seconds: int,
) -> _LoopStep:
    line = stdout_reader.readline(timeout=0.5)
    if line is not None:
        line = line.rstrip("\n")
        state.last_persist = time.time()
        state.idle_deadline = state.last_persist + timeout_seconds
        state.stdout_lines.append(line)
        if streamer:
            streamer.append_stream_line(line)
        payload = _parse_ndjson_line(line)
        if payload:
            finished, failed = result_payload_status(payload)
            if finished and state.pending_approval is None:
                state.session_id = str(payload.get("session_id") or state.session_id)
                state.finished_with_result = True
                state.result_is_error = failed
                return _LoopStep("break")
    elif streamer and time.time() - state.last_persist >= 2.0:
        streamer.touch()
        state.last_persist = time.time()
    return _LoopStep("continue")


def _fail_after_rejection(
    runner: PermissionBridgeRunner,
    *,
    scope: ApprovalScope,
    run_id: str,
    proc: Any,
    state: _LoopState,
) -> _LoopStep:
    proc.kill()
    runner._mark_stage_blocked(scope, "Permission denied via Loregarden inbox")
    run = runner.session.get(AgentRun, run_id)
    if run:
        run.status = RunStatus.FAILED
        runner.session.add(run)
        runner.session.commit()
    return _LoopStep(
        "return",
        BridgeResult(
            status=RunStatus.FAILED,
            stdout="\n".join(state.stdout_lines),
            stderr="Permission denied via Loregarden inbox",
            session_id=state.session_id,
        ),
    )


def _resume_after_approval(
    runner: PermissionBridgeRunner,
    *,
    scope: ApprovalScope,
    run_id: str,
    streamer: RunStreamSink | None,
) -> _LoopStep:
    if runner._set_stage_status(scope, StageStatus.RUNNING):
        runner.session.add(scope.ticket)
        runner.session.commit()
    run = runner.session.get(AgentRun, run_id)
    if run:
        run.status = RunStatus.RUNNING
        runner.session.add(run)
        runner.session.commit()
    if streamer:
        streamer.set_live("Agent running…")
    return _LoopStep("continue")


def _check_cancel(run_id: str, proc: Any, state: _LoopState) -> BridgeResult | None:
    """Kill the CLI when the API has requested a cancel."""
    now = time.time()
    if now - state.last_cancel_poll < POLL_INTERVAL_SECONDS:
        return None
    state.last_cancel_poll = now

    if not cancel_requested(run_id):
        return None

    _close_stdin(proc)
    try:
        proc.kill()
    except OSError:
        pass
    return BridgeResult(
        status=RunStatus.CANCELLED,
        stdout="\n".join(state.stdout_lines),
        stderr="Cancelled by operator",
        session_id=state.session_id,
    )


class PermissionBridgeRunner:
    """Run CLIs with permission prompts routed to the Loregarden inbox."""

    def __init__(self, session: Session, *, track_workflow_stage: bool = True) -> None:
        self.session = session
        self.orch = OrchestrationService(session)
        self.track_workflow_stage = track_workflow_stage

    def run(
        self,
        *,
        run_id: str,
        invocation: CliInvocation,
        prompt: str,
        timeout_seconds: int,
        ticket: Ticket | None = None,
        workspace: Workspace | None = None,
        workspace_stage_key: str = HOME_CHAT_STAGE_KEY,
        spawn_process: Callable[..., Any] | None = None,
        wait_for_approval: Callable[..., ApprovalResolution] | None = None,
        streamer: RunStreamSink | None = None,
    ) -> BridgeResult:
        import subprocess

        if (ticket is None) == (workspace is None):
            raise ValueError("Pass exactly one of ticket= or workspace= to scope approvals")
        scope = (
            ApprovalScope.for_ticket(ticket)
            if ticket is not None
            else ApprovalScope.for_workspace(workspace, stage_key=workspace_stage_key)
        )

        spawn = spawn_process or subprocess.Popen
        ctx = self._prepare_context(scope, run_id)
        custom_wait = wait_for_approval
        custom_wait_seen: set[str] = set()

        def resolve_poll(approval_id: str) -> ApprovalResolution | None:
            if custom_wait and approval_id not in custom_wait_seen:
                custom_wait_seen.add(approval_id)
                return custom_wait(approval_id, timeout_seconds=1)
            return poll_approval_resolution(approval_id)

        proc = None
        state = _LoopState(
            stdout_lines=[],
            session_id=invocation.resume_session_id or "",
            last_persist=time.time(),
        )
        try:
            proc = spawn(
                invocation.argv,
                cwd=invocation.cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            assert proc.stdin and proc.stdout

            stdout_reader = SubprocessLineReader(proc.stdout)

            user_msg = build_user_message(
                prompt,
                session_id=invocation.resume_session_id or None,
            )
            proc.stdin.write((json.dumps(user_msg) + "\n").encode("utf-8"))
            proc.stdin.flush()

            stderr_lines: list[str] = []
            start = time.time()
            state.idle_deadline = start + timeout_seconds
            state.hard_deadline = start + timeout_seconds * TIMEOUT_HARD_CAP_MULTIPLIER

            while True:
                if time.time() >= state.idle_deadline or time.time() >= state.hard_deadline:
                    break
                if proc.poll() is not None:
                    break

                cancelled = _check_cancel(run_id, proc, state)
                if cancelled is not None:
                    return cancelled

                self._deliver_steer_messages(
                    run_id=run_id,
                    proc=proc,
                    state=state,
                    streamer=streamer,
                )

                if state.pending_approval is not None:
                    step = self._handle_pending_approval(
                        scope=scope,
                        run_id=run_id,
                        proc=proc,
                        streamer=streamer,
                        stdout_reader=stdout_reader,
                        timeout_seconds=timeout_seconds,
                        state=state,
                        resolve_poll=resolve_poll,
                    )
                elif state.finished_with_result:
                    break
                else:
                    step = self._handle_next_line(
                        ctx=ctx,
                        scope=scope,
                        run_id=run_id,
                        proc=proc,
                        invocation=invocation,
                        streamer=streamer,
                        stdout_reader=stdout_reader,
                        timeout_seconds=timeout_seconds,
                        state=state,
                    )

                if step.action == "return":
                    return step.result
                if step.action == "break":
                    break

            return self._finalize(
                proc=proc,
                stdout_reader=stdout_reader,
                timeout_seconds=timeout_seconds,
                state=state,
                stderr_lines=stderr_lines,
                streamer=streamer,
            )
        except subprocess.TimeoutExpired:
            if proc is not None:
                proc.kill()
            return BridgeResult(
                status=RunStatus.FAILED,
                stdout="\n".join(state.stdout_lines),
                stderr=agent_timeout_message(timeout_seconds),
                session_id=state.session_id,
            )

    def _deliver_steer_messages(
        self,
        *,
        run_id: str,
        proc: Any,
        state: _LoopState,
        streamer: RunStreamSink | None,
    ) -> None:
        """Write any operator messages for this run into the agent's stdin.

        Uses its own short-lived session: the API commits from a different
        connection, and the session driving the run may sit in a transaction old
        enough never to see those rows.

        A failure here is deliberately swallowed. Steering is an extra channel,
        and a broken write to it must not take down a run that is otherwise
        proceeding — the message stays undelivered, which is what the UI reports.
        """
        now = time.time()
        if now - state.last_steer_poll < POLL_INTERVAL_SECONDS:
            return
        state.last_steer_poll = now

        try:
            with Session(engine) as session:
                messages = pending_messages(session, run_id)
                for message in messages:
                    payload = build_user_message(
                        message.content, session_id=state.session_id or None
                    )
                    proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
                    proc.stdin.flush()
                    mark_delivered(session, message)
                    if streamer:
                        streamer.append("STEER", message.content, force=True)
        except Exception:  # noqa: BLE001 - never let the side channel kill the run
            logger.warning("Could not deliver steer message for run %s", run_id, exc_info=True)

    def _prepare_context(self, scope: ApprovalScope, run_id: str) -> _RunContext:
        workspace = self.session.get(Workspace, scope.workspace_id)
        workspace_slug = workspace.slug if workspace else ""
        workspace_root = str(resolve_workspace_root(workspace)) if workspace else ""
        run = self.session.get(AgentRun, run_id)
        auto_approve = bool(run and run.auto_approve)
        agent_id = run.agent_id if run else ""
        agent_config = get_agent(agent_id) if agent_id else None
        agent_name = (agent_config or {}).get("name", agent_id)
        return _RunContext(
            workspace_slug=workspace_slug,
            workspace_root=workspace_root,
            auto_approve=auto_approve,
            agent_id=agent_id,
            agent_name=agent_name,
        )

    @staticmethod
    def _send_response(proc: Any, response: dict[str, Any]) -> None:
        proc.stdin.write((json.dumps(response) + "\n").encode("utf-8"))
        proc.stdin.flush()

    def _handle_pending_approval(
        self,
        *,
        scope: ApprovalScope,
        run_id: str,
        proc: Any,
        streamer: RunStreamSink | None,
        stdout_reader: SubprocessLineReader,
        timeout_seconds: int,
        state: _LoopState,
        resolve_poll: Callable[[str], ApprovalResolution | None],
    ) -> _LoopStep:
        assert state.pending_approval is not None
        if time.time() > state.approval_deadline:
            proc.kill()
            return _LoopStep(
                "return",
                BridgeResult(
                    status=RunStatus.FAILED,
                    stdout="\n".join(state.stdout_lines),
                    stderr=f"Timed out waiting for approval {state.pending_approval.id}",
                    session_id=state.session_id,
                ),
            )

        resolution = resolve_poll(state.pending_approval.id)
        if resolution is None:
            return _continue_while_awaiting(
                streamer=streamer,
                stdout_reader=stdout_reader,
                state=state,
                timeout_seconds=timeout_seconds,
            )

        state.pending_approval = None
        allow_input = resolution.updated_input
        if (allow_input is None or allow_input == {}) and resolution.approved:
            allow_input = state.pending_tool_input
        response = build_control_response(
            request_id=state.pending_request_id,
            approved=resolution.approved,
            message=resolution.message or "Rejected in Loregarden approval inbox",
            updated_input=allow_input if resolution.approved else None,
        )
        approving_run = self.session.get(AgentRun, run_id)
        self._record(
            approving_run.agent_id if approving_run else "",
            scope,
            run_id,
            state.pending_tool_name,
            DECISION_APPROVED if resolution.approved else DECISION_REJECTED,
            decision_ms=int((time.time() - state.pending_since) * 1000)
            if state.pending_since
            else 0,
        )
        state.pending_tool_input = None
        state.pending_tool_name = ""
        state.pending_since = 0.0
        self._send_response(proc, response)

        if not resolution.approved:
            return _fail_after_rejection(self, scope=scope, run_id=run_id, proc=proc, state=state)

        return _resume_after_approval(self, scope=scope, run_id=run_id, streamer=streamer)

    def _scope_denial_result(
        self,
        *,
        ctx: _RunContext,
        scope: ApprovalScope,
        run_id: str,
        proc: Any,
        request_id: str,
        permission: dict[str, Any],
        streamer: RunStreamSink | None,
        state: _LoopState,
    ) -> BridgeResult | None:
        """Hard technical boundary, checked before auto-approve/allowlist/
        human-approval paths — a scoped agent writing outside its declared
        directory is refused outright, not merely flagged for someone to
        approve around. This is the backstop for role docs like "Modify only
        code within /server/**" that were previously prompt text only (see
        ticket 33 postmortem: a backend_implementer agent implemented
        frontend code because nothing actually stopped it)."""
        tool_input = permission["tool_input"] if isinstance(permission["tool_input"], dict) else {}
        scope_denial = check_agent_scope(
            agent_id=ctx.agent_id,
            agent_name=ctx.agent_name,
            tool_name=permission["tool_name"],
            tool_input=tool_input,
            workspace_root=ctx.workspace_root,
        )
        if not scope_denial:
            return None

        self._send_response(
            proc,
            build_control_response(request_id=request_id, approved=False, message=scope_denial),
        )
        rerouted_to = self._try_scope_reroute(
            ctx=ctx, scope=scope, tool_input=tool_input, run_id=run_id, message=scope_denial
        )
        if streamer:
            if rerouted_to:
                streamer.append(
                    "TOOL",
                    f"Out of scope for {ctx.agent_id}; handing this stage off to "
                    f"{rerouted_to}: {scope_denial}",
                    force=True,
                )
            else:
                streamer.append("TOOL", f"Denied (out of scope): {scope_denial}", force=True)
        if not rerouted_to:
            # No sibling can take this path (e.g. infra) or the handoff budget is
            # spent — fall back to halting the ticket for a human.
            self._mark_stage_blocked(scope, scope_denial)
        run = self.session.get(AgentRun, run_id)
        if run:
            run.status = RunStatus.FAILED
            self.session.add(run)
            self.session.commit()
        proc.kill()
        return BridgeResult(
            status=RunStatus.FAILED,
            stdout="\n".join(state.stdout_lines),
            stderr=scope_denial,
            session_id=state.session_id,
        )

    def _try_scope_reroute(
        self,
        *,
        ctx: _RunContext,
        scope: ApprovalScope,
        tool_input: dict[str, Any],
        run_id: str,
        message: str,
    ) -> str | None:
        """Turn a cross-scope write denial into a handoff to the sibling implementer.

        When a scoped implementer is denied a write onto another scoped
        implementer's subtree, the work isn't a human blocker — it just belongs
        to the other agent. Pin that sibling as the authoritative agent for the
        current stage's next run and reset the stage to re-run, so the
        orchestrator dispatches the sibling instead of blocking the ticket.

        Returns the sibling agent id when the handoff is set up, or None when the
        caller should block instead: the run isn't workflow-tracked, no scoped
        sibling owns the path, or the frontend<->backend handoff budget is spent
        (so a ticket that genuinely needs both never ping-pongs forever).
        """
        ticket = scope.ticket
        if not self.track_workflow_stage or ticket is None:
            return None
        target = extract_target_path(tool_input)
        if not target:
            return None
        relative = relative_to_root(target, ctx.workspace_root)
        if relative is None:
            return None
        sibling = owning_scoped_agent(relative)
        if not sibling or sibling == ctx.agent_id:
            return None

        instance, stages = self.orch._resolve_stages(ticket)
        if not instance or not stages or not ticket.workflow_stage_key:
            return None
        stage = next((s for s in stages if s.key == ticket.workflow_stage_key), None)
        if stage is None:
            return None
        # Reuse the dispatch-time validation: only reroute when this stage can
        # actually run the sibling (a classify route offers it, or it is the
        # stage's static agent). Setting the pin first lets that check see it.
        ticket.scope_reroute_agent = sibling
        if resolve_scope_reroute_pin(ticket, stage) is None:
            ticket.scope_reroute_agent = ""
            return None

        if record_reroute_exhausts_budget(
            self.session,
            ticket,
            target_stage=_SCOPE_REROUTE_LEDGER_KEY,
            from_stage=ticket.workflow_stage_key,
            context=message,
            run_id=run_id,
        ):
            # Budget spent: stop bouncing between implementers and block instead.
            # Clear the pin durably — a prior reroute committed one, and leaving
            # it would make a human's later resume dispatch the wrong specialist.
            ticket.scope_reroute_agent = ""
            self.session.add(ticket)
            self.session.commit()
            return None

        set_stage_status(ticket, instance, stages, ticket.workflow_stage_key, StageStatus.PENDING)
        ticket.next_status = "Proceed"
        ticket.blocking_issues = ""
        ticket.revision += 1
        self.session.add(ticket)
        self.session.add(instance)
        self.session.commit()
        return sibling

    def _record(
        self,
        agent_id: str,
        scope: ApprovalScope,
        run_id: str,
        tool_name: str,
        decision: str,
        decision_ms: int = 0,
    ) -> None:
        record_tool_call(
            self.session,
            run_id=run_id,
            ticket_id=scope.ticket_id or "",
            agent_id=agent_id,
            tool_name=tool_name,
            decision=decision,
            decision_ms=decision_ms,
        )

    def _third_party_auto_approved(self, tool_name: str, bare_mcp: str | None) -> str | None:
        """Server name when a *registered* server is trusted to run unattended.

        Loregarden's own tools are excluded here: they have their own curated
        allowlist, which is finer-grained than a whole-server decision.
        """
        if bare_mcp:
            return None
        split = split_mcp_tool(tool_name)
        if not split:
            return None
        server_name, _ = split
        if server_name == LOREGARDEN_SERVER:
            return None
        return server_name if server_auto_approves(self.session, server_name) else None

    def _deny_orchestrated_tool(
        self,
        *,
        ctx: _RunContext,
        scope: ApprovalScope,
        run_id: str,
        proc: Any,
        request_id: str,
        tool_name: str,
        bare_mcp: str,
        streamer: RunStreamSink | None,
    ) -> bool:
        message = (
            f"{bare_mcp} is denied to orchestrated pipeline agents (interim "
            "allowlist, a9-create-ticket-mcp-tool; superseded once "
            "a2-per-agent-server-policy lands). Use it only from interactive "
            "contexts (Ticket Studio chat, a human's terminal session, direct "
            "operator MCP/HTTP calls)."
        )
        self._send_response(
            proc,
            build_control_response(request_id=request_id, approved=False, message=message),
        )
        self._record(ctx.agent_id, scope, run_id, tool_name, DECISION_REJECTED)
        if streamer:
            streamer.append("TOOL", f"Denied (orchestrated agent policy): {bare_mcp}", force=True)
            streamer.set_live("Agent running…")
        return True

    def _approve_third_party_tool(
        self,
        *,
        ctx: _RunContext,
        scope: ApprovalScope,
        run_id: str,
        proc: Any,
        request_id: str,
        tool_name: str,
        server_name: str,
        streamer: RunStreamSink | None,
    ) -> bool:
        # Trust removed the only thing pacing this agent — a human clicking —
        # so the server's own ceiling is what is left to enforce.
        limited = rate_limit_denial(self.session, server_name)
        if limited:
            self._send_response(
                proc,
                build_control_response(request_id=request_id, approved=False, message=limited),
            )
            self._record(ctx.agent_id, scope, run_id, tool_name, DECISION_RATE_LIMITED)
            if streamer:
                streamer.append("TOOL", limited, force=True)
            return True

        # A registered server's tools carry no loregarden enrichment — the
        # ticket ids that enrichment injects mean nothing to them.
        self._send_response(proc, build_control_response(request_id=request_id, approved=True))
        # Recording this was missing: a trusted server's calls are exactly
        # the ones an operator wants to see, since nothing else reports them.
        self._record(ctx.agent_id, scope, run_id, tool_name, DECISION_TRUSTED_SERVER)
        if streamer:
            streamer.append("TOOL", f"Auto-approved {server_name}: {tool_name}", force=True)
            streamer.set_live("Agent running…")
        return True

    def _try_fast_approve(
        self,
        *,
        ctx: _RunContext,
        scope: ApprovalScope,
        run_id: str,
        proc: Any,
        request_id: str,
        permission: dict[str, Any],
        bare_mcp: str | None,
        question: bool,
        streamer: RunStreamSink | None,
    ) -> bool:
        """Auto-approve via the read-only-MCP allowlist, the run's
        auto_approve flag, or the persisted permission allowlist. Returns
        True if a response was already written (caller should treat the
        permission as handled and move on to the next line)."""
        tool_input = permission["tool_input"] if isinstance(permission["tool_input"], dict) else {}
        ticket = scope.ticket
        tool_name = permission["tool_name"]

        if (
            bare_mcp
            and self.track_workflow_stage
            and is_orchestrated_agent_denied_mcp_tool(tool_name)
        ):
            # Checked first, ahead of every approval path including the human
            # inbox — an orchestrated stage agent must never be able to spawn
            # tickets mid-run, not even with a click. See ORCHESTRATED_DENIED_MCP_TOOLS.
            # Interactive contexts (Ticket Studio chat, a human's terminal session)
            # construct PermissionBridgeRunner with track_workflow_stage=False and
            # fall through to the normal approval gate below instead.
            return self._deny_orchestrated_tool(
                ctx=ctx,
                scope=scope,
                run_id=run_id,
                proc=proc,
                request_id=request_id,
                tool_name=tool_name,
                bare_mcp=bare_mcp,
                streamer=streamer,
            )

        third_party = self._third_party_auto_approved(tool_name, bare_mcp)
        if third_party:
            return self._approve_third_party_tool(
                ctx=ctx,
                scope=scope,
                run_id=run_id,
                proc=proc,
                request_id=request_id,
                tool_name=tool_name,
                server_name=third_party,
                streamer=streamer,
            )

        if bare_mcp and is_auto_approved_mcp_tool(tool_name):
            enriched = enrich_mcp_tool_input(
                bare_tool=bare_mcp,
                tool_input=tool_input,
                ticket=ticket,
                workspace_slug=ctx.workspace_slug,
            )
            self._send_response(
                proc,
                build_control_response(
                    request_id=request_id, approved=True, updated_input=enriched
                ),
            )
            self._record(ctx.agent_id, scope, run_id, tool_name, DECISION_ALLOWLIST)
            if streamer:
                streamer.append("TOOL", f"Auto-approved Loregarden MCP: {bare_mcp}", force=True)
                streamer.set_live("Agent running…")
            return True

        if is_auto_approved_cli_tool(tool_name):
            self._send_response(proc, build_control_response(request_id=request_id, approved=True))
            self._record(ctx.agent_id, scope, run_id, tool_name, DECISION_READ_ONLY_CLI)
            if streamer:
                streamer.append("TOOL", f"Auto-approved read-only: {tool_name}", force=True)
                streamer.set_live("Agent running…")
            return True

        if ctx.auto_approve and not question:
            if bare_mcp:
                tool_input = enrich_mcp_tool_input(
                    bare_tool=bare_mcp,
                    tool_input=tool_input,
                    ticket=ticket,
                    workspace_slug=ctx.workspace_slug,
                )
            self._send_response(
                proc,
                build_control_response(
                    request_id=request_id, approved=True, updated_input=tool_input
                ),
            )
            self._record(ctx.agent_id, scope, run_id, tool_name, DECISION_RUN_AUTO)
            if streamer:
                streamer.append("TOOL", f"Auto-approved: {tool_name}", force=True)
                streamer.set_live("Agent running…")
            return True

        if not question:
            allow_scope = is_permission_allowed(
                self.session,
                workspace_id=scope.workspace_id,
                ticket_id=scope.ticket_id or "",
                stage_key=ticket.workflow_stage_key if ticket else "",
                tool_name=tool_name,
                tool_input=tool_input,
            )
            if allow_scope:
                allow_input = tool_input
                if bare_mcp:
                    allow_input = enrich_mcp_tool_input(
                        bare_tool=bare_mcp,
                        tool_input=tool_input,
                        ticket=ticket,
                        workspace_slug=ctx.workspace_slug,
                    )
                self._send_response(
                    proc,
                    build_control_response(
                        request_id=request_id, approved=True, updated_input=allow_input
                    ),
                )
                if streamer:
                    streamer.append(
                        "TOOL",
                        f"Auto-approved ({allow_scope} allowlist): {tool_name}",
                        force=True,
                    )
                    streamer.set_live("Agent running…")
                return True

        return False

    def _handle_next_line(
        self,
        *,
        ctx: _RunContext,
        scope: ApprovalScope,
        run_id: str,
        proc: Any,
        invocation: CliInvocation,
        streamer: RunStreamSink | None,
        stdout_reader: SubprocessLineReader,
        timeout_seconds: int,
        state: _LoopState,
    ) -> _LoopStep:
        line = stdout_reader.readline(timeout=1.0)
        if line is None:
            if streamer and time.time() - state.last_persist >= 2.0:
                streamer.touch()
                state.last_persist = time.time()
            return _LoopStep("continue")

        line = line.rstrip("\n")
        state.last_persist = time.time()
        state.idle_deadline = state.last_persist + timeout_seconds
        state.stdout_lines.append(line)
        if streamer:
            streamer.append_stream_line(line)
        payload = _parse_ndjson_line(line)
        if not payload:
            return _LoopStep("continue")

        if payload.get("type") == "system" and payload.get("subtype") == "init":
            state.session_id = str(payload.get("session_id") or state.session_id)

        finished, failed = result_payload_status(payload)
        if finished and state.pending_approval is None:
            state.session_id = str(payload.get("session_id") or state.session_id)
            state.finished_with_result = True
            state.result_is_error = failed
            return _LoopStep("break")

        permission = extract_permission_request(payload)
        if not permission:
            return _LoopStep("continue")

        request_id = permission["request_id"] or f"perm_{len(state.stdout_lines)}"
        bare_mcp = bare_mcp_tool_name(permission["tool_name"])
        question = is_ask_user_question(permission["tool_name"])

        scope_result = self._scope_denial_result(
            ctx=ctx,
            scope=scope,
            run_id=run_id,
            proc=proc,
            request_id=request_id,
            permission=permission,
            streamer=streamer,
            state=state,
        )
        if scope_result:
            return _LoopStep("return", scope_result)

        if self._try_fast_approve(
            ctx=ctx,
            scope=scope,
            run_id=run_id,
            proc=proc,
            request_id=request_id,
            permission=permission,
            bare_mcp=bare_mcp,
            question=question,
            streamer=streamer,
        ):
            return _LoopStep("continue")

        if streamer:
            if question:
                streamer.append("TOOL", "Agent asked clarifying questions", force=True)
                streamer.set_live("Awaiting your answers…")
            else:
                streamer.append(
                    "TOOL", f"Permission requested: {permission['tool_name']}", force=True
                )
                streamer.set_live(f"Awaiting approval for {permission['tool_name']}…")

        if question:
            approval = self._create_question_approval(
                run_id=run_id,
                scope=scope,
                request_id=request_id,
                tool_input=permission["tool_input"],
                cli_adapter=invocation.adapter,
                cli_session_id=state.session_id,
            )
        else:
            approval = self._create_permission_approval(
                run_id=run_id,
                scope=scope,
                request_id=request_id,
                tool_name=permission["tool_name"],
                tool_input=permission["tool_input"],
                cli_adapter=invocation.adapter,
                cli_session_id=state.session_id,
            )

        remaining_for_approval = min(state.idle_deadline, state.hard_deadline) - time.time()
        if remaining_for_approval <= 0:
            proc.kill()
            return _LoopStep(
                "return",
                BridgeResult(
                    status=RunStatus.FAILED,
                    stdout="\n".join(state.stdout_lines),
                    stderr=agent_timeout_message(timeout_seconds),
                    session_id=state.session_id,
                ),
            )

        state.pending_approval = approval
        state.pending_request_id = request_id
        state.pending_tool_input = (
            permission["tool_input"] if isinstance(permission["tool_input"], dict) else {}
        )
        state.pending_tool_name = str(permission["tool_name"])
        # Stamped when the operator is first asked, so decision_ms is their
        # thinking time rather than the loop's polling interval.
        state.pending_since = time.time()
        state.approval_deadline = time.time() + min(
            remaining_for_approval, settings.permission_approval_timeout_seconds
        )
        return _LoopStep("continue")

    def _finalize(
        self,
        *,
        proc: Any,
        stdout_reader: SubprocessLineReader,
        timeout_seconds: int,
        state: _LoopState,
        stderr_lines: list[str],
        streamer: RunStreamSink | None,
    ) -> BridgeResult:
        import subprocess

        if state.finished_with_result:
            _close_stdin(proc)
            _drain_stdout_after_result(proc, stdout_reader, state.stdout_lines, streamer=streamer)

        remaining = min(state.idle_deadline, state.hard_deadline) - time.time()
        if proc.poll() is None:
            if remaining <= 0:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return BridgeResult(
                    status=RunStatus.FAILED,
                    stdout="\n".join(state.stdout_lines),
                    stderr=agent_timeout_message(timeout_seconds),
                    session_id=state.session_id,
                )
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return BridgeResult(
                    status=RunStatus.FAILED,
                    stdout="\n".join(state.stdout_lines),
                    stderr=agent_timeout_message(timeout_seconds),
                    session_id=state.session_id,
                )
        elif proc.returncode is None:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        stderr_stream = proc.stderr
        if stderr_stream:
            stderr_lines.extend(stderr_stream.read().decode("utf-8", errors="replace").splitlines())

        stdout = "\n".join(state.stdout_lines)
        stderr = "\n".join(stderr_lines)
        if state.finished_with_result:
            status = RunStatus.FAILED if state.result_is_error else RunStatus.SUCCEEDED
        else:
            status = RunStatus.SUCCEEDED if proc.returncode == 0 else RunStatus.FAILED
        return BridgeResult(
            status=status, stdout=stdout, stderr=stderr, session_id=state.session_id
        )

    def _create_question_approval(
        self,
        *,
        run_id: str,
        scope: ApprovalScope,
        request_id: str,
        tool_input: Any,
        cli_adapter: str,
        cli_session_id: str,
    ) -> Approval:
        questions = tool_input.get("questions") if isinstance(tool_input, dict) else []
        first_question = ""
        if questions and isinstance(questions[0], dict):
            first_question = str(questions[0].get("question") or "").strip()
        summary = first_question or "Agent needs input before continuing."
        if len(questions) > 1:
            summary = f"{summary} (+{len(questions) - 1} more)"

        self._set_stage_status(scope, StageStatus.AWAITING)

        approval = Approval(
            ticket_id=scope.ticket_id,
            workspace_id=scope.workspace_id,
            run_id=run_id,
            kind=ApprovalKind.CLI_QUESTION,
            title="Agent questions",
            level="medium",
            stage_key=scope.approval_stage_key(self.track_workflow_stage),
            impact=summary[:2000],
            permission_request_id=request_id,
            tool_name=ASK_USER_QUESTION_TOOL,
            tool_input_json=serialize_tool_input(tool_input),
            cli_adapter=cli_adapter,
            cli_session_id=cli_session_id,
            status=ApprovalStatus.PENDING,
        )
        self._touch_ticket(scope)
        self.session.add(approval)
        self.session.commit()
        self.session.refresh(approval)

        run = self.session.get(AgentRun, run_id)
        if run:
            run.status = RunStatus.AWAITING_PERMISSION
            self.session.add(run)
            self.session.commit()
        return approval

    def _create_permission_approval(
        self,
        *,
        run_id: str,
        scope: ApprovalScope,
        request_id: str,
        tool_name: str,
        tool_input: Any,
        cli_adapter: str,
        cli_session_id: str,
    ) -> Approval:
        self._set_stage_status(scope, StageStatus.AWAITING)

        ticket = scope.ticket
        if self.track_workflow_stage and ticket:
            impact = f"Agent requested `{tool_name}` during stage `{ticket.workflow_stage_key}`."
        elif ticket:
            impact = f"Agent requested `{tool_name}` during triage."
        else:
            impact = f"Agent requested `{tool_name}` during Home chat."

        approval = Approval(
            ticket_id=scope.ticket_id,
            workspace_id=scope.workspace_id,
            run_id=run_id,
            kind=ApprovalKind.CLI_PERMISSION,
            title=f"Allow {tool_name}?",
            level="high",
            stage_key=scope.approval_stage_key(self.track_workflow_stage),
            impact=impact,
            permission_request_id=request_id,
            tool_name=tool_name,
            tool_input_json=serialize_tool_input(tool_input),
            cli_adapter=cli_adapter,
            cli_session_id=cli_session_id,
            status=ApprovalStatus.PENDING,
        )
        self._touch_ticket(scope)
        self.session.add(approval)
        self.session.commit()
        self.session.refresh(approval)

        run = self.session.get(AgentRun, run_id)
        if run:
            run.status = RunStatus.AWAITING_PERMISSION
            self.session.add(run)
            self.session.commit()
        return approval

    def _touch_ticket(self, scope: ApprovalScope) -> None:
        """Bump the ticket so watchers see the new approval. No-op without one."""
        ticket = scope.ticket
        if ticket is None:
            return
        ticket.revision += 1
        ticket.last_updated_by = "permission_bridge"
        ticket.updated_at = datetime.now(timezone.utc)
        self.session.add(ticket)

    def _set_stage_status(self, scope: ApprovalScope, status: StageStatus) -> bool:
        ticket = scope.ticket
        if not self.track_workflow_stage or ticket is None:
            return False
        instance, stages = self.orch._resolve_stages(ticket)
        if not (instance and stages and ticket.workflow_stage_key):
            return False
        set_stage_status(ticket, instance, stages, ticket.workflow_stage_key, status)
        self.session.add(instance)
        return True

    def _mark_stage_blocked(self, scope: ApprovalScope, message: str) -> None:
        ticket = scope.ticket
        if not self.track_workflow_stage or ticket is None:
            return
        self._set_stage_status(scope, StageStatus.BLOCKED)
        ticket.blocking_issues = message[:2000]
        ticket.revision += 1
        self.session.add(ticket)
        self.session.commit()
